from datetime import timezone
import os
from pathlib import Path
import secrets
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.config import get_settings
from app.models import (AuditEvent, ExchangeApplication, ExchangeApplicationHistory,
                        ExchangeApplicationDocument, ExchangeCall, MemberProfile, Role, User, UserRoleAssignment, utcnow)
from app.notifications import notify_roles, notify_user
from app.schemas import (ExchangeApplicationOut, ExchangeApplicationReview,
                         ExchangeApplicationWrite, ExchangeCallOut, ExchangeCallWrite, ExchangeReviewerCreate,
                         ExchangeHistoryOut)
from app.security import current_user, require_csrf, session_from_request

router = APIRouter(tags=["international exchanges"])
PRIVATE_DOCUMENT_DIR = Path("data/exchange_private_documents")
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTS_PER_APPLICATION = 5
REVIEW_ROLES = {"super_admin", "national_board", "exchange_reviewer"}
REVIEW_STATUSES = {"submitted", "under_review", "shortlisted", "accepted", "rejected", "withdrawn"}
TRANSITIONS = {
    "submitted": {"under_review", "shortlisted", "accepted", "rejected"},
    "under_review": {"shortlisted", "accepted", "rejected"},
    "shortlisted": {"under_review", "accepted", "rejected"},
}


def _role_codes(db: Session, user: User) -> set[str]:
    return set(db.scalars(select(Role.code).join(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)))


def _require_reviewer(request: Request, db: Session) -> User:
    actor = current_user(request, db)
    if not (_role_codes(db, actor) & REVIEW_ROLES):
        raise HTTPException(status_code=403, detail="National exchange reviewer role required")
    return actor


def _require_super_admin(request: Request, db: Session) -> User:
    actor = current_user(request, db)
    if "super_admin" not in _role_codes(db, actor):
        raise HTTPException(status_code=403, detail="Super-admin role required")
    return actor


def _utc(value):
    if value and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _private_files_cipher() -> Fernet:
    settings = get_settings()
    key = settings.private_files_encryption_key.strip()
    if not key:
        if settings.is_production:
            raise HTTPException(status_code=503, detail="Private document encryption is not configured")
        key_path = PRIVATE_DOCUMENT_DIR.parent / ".private-files-dev.key"
        key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            key = Fernet.generate_key().decode("ascii")
            with os.fdopen(fd, "w") as handle:
                handle.write(key)
        except FileExistsError:
            key = key_path.read_text().strip()
        os.chmod(key_path, 0o600)
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=503, detail="Private document encryption key is invalid") from exc


def _document_name(filename: str | None) -> str:
    name = Path((filename or "").replace("\\", "/")).name
    name = "".join(char for char in name if char.isprintable() and char not in "\r\n")[:180].strip()
    if not name:
        name = "application.pdf"
    if Path(name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Only PDF documents are accepted")
    return name


def _document_metadata(document: ExchangeApplicationDocument) -> dict:
    return {"id": document.id, "filename": document.original_filename,
            "size": document.file_size, "uploaded_at": document.created_at}


def _document_download(document: ExchangeApplicationDocument, actor: User, db: Session) -> Response:
    path = PRIVATE_DOCUMENT_DIR / f"{document.storage_key}.enc"
    try:
        encrypted = path.read_bytes()
        content = _private_files_cipher().decrypt(encrypted)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Document integrity check failed") from exc
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.document_downloaded",
        entity_type="exchange_application_document", entity_id=str(document.id), details={}))
    db.commit()
    encoded_name = quote(document.original_filename, safe="")
    headers = {
        "Content-Disposition": f"attachment; filename=application.pdf; filename*=UTF-8''{encoded_name}",
        "Cache-Control": "private, no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type="application/pdf", headers=headers)


def _call_out(db: Session, call: ExchangeCall, *, include_status: bool) -> ExchangeCallOut:
    count = db.scalar(select(func.count(ExchangeApplication.id)).where(
        ExchangeApplication.call_id == call.id,
        ExchangeApplication.status.notin_(["withdrawn", "rejected"]))) or 0
    return ExchangeCallOut(id=call.id, title=call.title, program_type=call.program_type,
                           destination=call.destination, summary=call.summary,
                           description=call.description, requirements=call.requirements,
                           opens_at=call.opens_at, closes_at=call.closes_at, seats=call.seats,
                           status=call.status if include_status else None, applications_count=count)


def _application_out(application: ExchangeApplication, *, include_private: bool, include_candidate: bool = False) -> ExchangeApplicationOut:
    profile = application.user.profile if include_candidate else None
    return ExchangeApplicationOut(
        id=application.id, call_id=application.call_id, call_title=application.call.title,
        destination=application.call.destination, status=application.status,
        motivation=application.motivation if include_private else None,
        relevant_experience=application.relevant_experience if include_private else None,
        preferred_destination=application.preferred_destination if include_private else None,
        reviewer_note=application.reviewer_note,
        candidate_name=profile.full_name if profile else None,
        candidate_username=application.user.username if include_candidate else None,
        university_name=profile.university_name if profile else None,
        submitted_at=application.submitted_at, updated_at=application.updated_at)


@router.get("/api/v1/exchanges", response_model=list[ExchangeCallOut])
def public_exchange_calls(db: Session = Depends(get_db)):
    now = utcnow()
    stmt = select(ExchangeCall).where(ExchangeCall.status == "open", ExchangeCall.closes_at > now,
        or_(ExchangeCall.opens_at.is_(None), ExchangeCall.opens_at <= now)).order_by(ExchangeCall.closes_at)
    return [_call_out(db, call, include_status=False) for call in db.scalars(stmt)]


@router.get("/api/v1/members/me/exchange-applications", response_model=list[ExchangeApplicationOut])
def my_exchange_applications(request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    rows = db.scalars(select(ExchangeApplication).options(joinedload(ExchangeApplication.call)).where(
        ExchangeApplication.user_id == actor.id).order_by(ExchangeApplication.submitted_at.desc())).unique()
    return [_application_out(row, include_private=False) for row in rows]


@router.get("/api/v1/members/me/exchange-applications/{application_id}/history", response_model=list[ExchangeHistoryOut])
def my_application_history(application_id: int, request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    application = db.get(ExchangeApplication, application_id)
    if not application or application.user_id != actor.id:
        raise HTTPException(status_code=404, detail="Application not found")
    rows = db.scalars(select(ExchangeApplicationHistory).where(
        ExchangeApplicationHistory.application_id == application_id).order_by(ExchangeApplicationHistory.created_at))
    return rows


@router.post("/api/v1/exchanges/{call_id}/applications", response_model=ExchangeApplicationOut, status_code=status.HTTP_201_CREATED)
def submit_exchange_application(call_id: int, payload: ExchangeApplicationWrite, request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    require_csrf(request, db)
    profile = db.scalar(select(MemberProfile).where(MemberProfile.user_id == actor.id))
    if not profile or profile.membership_status != "active":
        raise HTTPException(status_code=403, detail="Active membership is required to apply")
    call = db.get(ExchangeCall, call_id)
    now = utcnow()
    if not call or call.status != "open" or (_utc(call.opens_at) and _utc(call.opens_at) > now) or _utc(call.closes_at) <= now:
        raise HTTPException(status_code=404, detail="Exchange call is not open")
    existing = db.scalar(select(ExchangeApplication.id).where(ExchangeApplication.call_id == call_id, ExchangeApplication.user_id == actor.id))
    if existing:
        raise HTTPException(status_code=409, detail="You have already applied to this call")
    application = ExchangeApplication(call_id=call.id, user_id=actor.id,
        motivation=payload.motivation, relevant_experience=payload.relevant_experience,
        preferred_destination=payload.preferred_destination)
    db.add(application)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="You have already applied to this call") from exc
    db.add(ExchangeApplicationHistory(application_id=application.id, actor_user_id=actor.id,
        from_status=None, to_status="submitted", note=None))
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.application_submitted",
        entity_type="exchange_application", entity_id=str(application.id), details={"call_id": call.id}))
    notify_roles(db, REVIEW_ROLES, kind="exchange_application", title="Новая заявка на обмен",
        body=f"Получена заявка на «{call.title}».", link="/admin/exchanges", exclude_user_id=actor.id)
    db.commit()
    db.refresh(application)
    application.call = call
    application.user = actor
    return _application_out(application, include_private=False)


@router.delete("/api/v1/members/me/exchange-applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_application(application_id: int, request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    require_csrf(request, db)
    application = db.scalar(select(ExchangeApplication).options(joinedload(ExchangeApplication.call)).where(
        ExchangeApplication.id == application_id, ExchangeApplication.user_id == actor.id))
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status not in {"submitted", "under_review"}:
        raise HTTPException(status_code=409, detail="This application can no longer be withdrawn")
    before = application.status
    application.status = "withdrawn"
    application.updated_at = utcnow()
    db.add(ExchangeApplicationHistory(application_id=application.id, actor_user_id=actor.id,
        from_status=before, to_status="withdrawn", note=None))
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.application_withdrawn",
        entity_type="exchange_application", entity_id=str(application.id), details={}))
    db.commit()


@router.get("/api/v1/admin/exchanges", response_model=list[ExchangeCallOut])
def admin_exchange_calls(request: Request, db: Session = Depends(get_db)):
    _require_reviewer(request, db)
    return [_call_out(db, call, include_status=True) for call in db.scalars(select(ExchangeCall).order_by(ExchangeCall.closes_at.desc()))]


@router.post("/api/v1/admin/exchanges", response_model=ExchangeCallOut, status_code=status.HTTP_201_CREATED)
def create_exchange_call(payload: ExchangeCallWrite, request: Request, db: Session = Depends(get_db)):
    actor = _require_reviewer(request, db)
    require_csrf(request, db)
    call = ExchangeCall(**payload.model_dump(), created_by=actor.id)
    db.add(call)
    db.flush()
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.call_created", entity_type="exchange_call",
        entity_id=str(call.id), details={"status": call.status}))
    db.commit()
    db.refresh(call)
    return _call_out(db, call, include_status=True)


@router.patch("/api/v1/admin/exchanges/{call_id}", response_model=ExchangeCallOut)
def update_exchange_call(call_id: int, payload: ExchangeCallWrite, request: Request, db: Session = Depends(get_db)):
    actor = _require_reviewer(request, db)
    require_csrf(request, db)
    call = db.get(ExchangeCall, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Exchange call not found")
    for field, value in payload.model_dump().items():
        setattr(call, field, value)
    call.updated_at = utcnow()
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.call_updated", entity_type="exchange_call",
        entity_id=str(call.id), details={"status": call.status}))
    db.commit()
    db.refresh(call)
    return _call_out(db, call, include_status=True)


@router.get("/api/v1/admin/exchange-reviewers")
def list_exchange_reviewers(request: Request, db: Session = Depends(get_db)):
    _require_super_admin(request, db)
    role = db.scalar(select(Role).where(Role.code == "exchange_reviewer"))
    if not role:
        return []
    assignments = db.scalars(select(UserRoleAssignment).options(joinedload(UserRoleAssignment.user).joinedload(User.profile)).where(
        UserRoleAssignment.role_id == role.id).order_by(UserRoleAssignment.created_at))
    return [{"user_id": row.user_id, "username": row.user.username,
             "full_name": row.user.profile.full_name if row.user.profile else row.user.username}
            for row in assignments]


@router.post("/api/v1/admin/exchange-reviewers", status_code=status.HTTP_201_CREATED)
def add_exchange_reviewer(payload: ExchangeReviewerCreate, request: Request, db: Session = Depends(get_db)):
    actor = _require_super_admin(request, db)
    require_csrf(request, db)
    target = db.scalar(select(User).where(User.username == payload.username))
    role = db.scalar(select(Role).where(Role.code == "exchange_reviewer"))
    if not role:
        raise HTTPException(status_code=503, detail="Exchange-reviewer role is not configured")
    if not target or target.account_status != "active":
        raise HTTPException(status_code=404, detail="Active account not found")
    exists = db.scalar(select(UserRoleAssignment.id).where(UserRoleAssignment.user_id == target.id,
        UserRoleAssignment.role_id == role.id, UserRoleAssignment.scope_type == "national", UserRoleAssignment.scope_id.is_(None)))
    if exists:
        raise HTTPException(status_code=409, detail="This user is already an exchange reviewer")
    db.add(UserRoleAssignment(user_id=target.id, role_id=role.id, scope_type="national"))
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.reviewer_assigned", entity_type="user",
        entity_id=str(target.id), details={"username": target.username}))
    db.commit()
    return {"user_id": target.id, "username": target.username,
            "full_name": target.profile.full_name if target.profile else target.username}


@router.delete("/api/v1/admin/exchange-reviewers/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_exchange_reviewer(user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = _require_super_admin(request, db)
    require_csrf(request, db)
    role = db.scalar(select(Role).where(Role.code == "exchange_reviewer"))
    assignment = db.scalar(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id,
        UserRoleAssignment.role_id == role.id, UserRoleAssignment.scope_type == "national", UserRoleAssignment.scope_id.is_(None))) if role else None
    if not assignment:
        raise HTTPException(status_code=404, detail="Exchange reviewer not found")
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.reviewer_removed", entity_type="user",
        entity_id=str(user_id), details={"username": assignment.user.username}))
    db.delete(assignment)
    db.commit()


@router.get("/api/v1/admin/exchanges/applications", response_model=list[ExchangeApplicationOut])
def admin_exchange_applications(request: Request, call_id: int | None = None,
                                status_filter: str | None = None, db: Session = Depends(get_db)):
    _require_reviewer(request, db)
    stmt = select(ExchangeApplication).options(joinedload(ExchangeApplication.call),
        joinedload(ExchangeApplication.user).joinedload(User.profile)).order_by(ExchangeApplication.submitted_at)
    if call_id:
        stmt = stmt.where(ExchangeApplication.call_id == call_id)
    if status_filter:
        if status_filter not in REVIEW_STATUSES:
            raise HTTPException(status_code=422, detail="Unsupported application status")
        stmt = stmt.where(ExchangeApplication.status == status_filter)
    return [_application_out(row, include_private=True, include_candidate=True) for row in db.scalars(stmt).unique()]


@router.patch("/api/v1/admin/exchanges/applications/{application_id}", response_model=ExchangeApplicationOut)
def review_exchange_application(application_id: int, payload: ExchangeApplicationReview,
                                request: Request, db: Session = Depends(get_db)):
    actor = _require_reviewer(request, db)
    require_csrf(request, db)
    application = db.scalar(select(ExchangeApplication).options(joinedload(ExchangeApplication.call),
        joinedload(ExchangeApplication.user).joinedload(User.profile)).where(ExchangeApplication.id == application_id))
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if payload.status not in TRANSITIONS.get(application.status, set()):
        raise HTTPException(status_code=409, detail=f"Cannot move application from {application.status} to {payload.status}")
    before = application.status
    note = payload.reviewer_note.strip() if payload.reviewer_note else None
    application.status = payload.status
    application.reviewer_note = note
    application.reviewed_by = actor.id
    application.updated_at = utcnow()
    db.add(ExchangeApplicationHistory(application_id=application.id, actor_user_id=actor.id,
        from_status=before, to_status=payload.status, note=note))
    db.add(AuditEvent(actor_user_id=actor.id, action="exchange.application_reviewed",
        entity_type="exchange_application", entity_id=str(application.id), details={"from": before, "to": payload.status}))
    notify_user(db, application.user_id, kind="exchange_status", title="Обновлён статус заявки на обмен",
        body=note or f"Статус заявки «{application.call.title}»: {payload.status}.",
        link="/cabinet#my-exchanges-title")
    db.commit()
    db.refresh(application)
    return _application_out(application, include_private=True, include_candidate=True)


@router.post("/api/v1/members/me/exchange-applications/{application_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_exchange_document(application_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    actor = current_user(request, db)
    require_csrf(request, db)
    application = db.scalar(select(ExchangeApplication).where(
        ExchangeApplication.id == application_id, ExchangeApplication.user_id == actor.id))
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status not in {"submitted", "under_review", "shortlisted", "accepted"}:
        raise HTTPException(status_code=409, detail="Documents cannot be added to this application")
    existing_count = db.scalar(select(func.count(ExchangeApplicationDocument.id)).where(
        ExchangeApplicationDocument.application_id == application.id)) or 0
    if existing_count >= MAX_DOCUMENTS_PER_APPLICATION:
        raise HTTPException(status_code=409, detail="An application can have at most five documents")
    filename = _document_name(file.filename)
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    await file.close()
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="A document must be 10 MB or smaller")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid PDF")
    cipher = _private_files_cipher()
    PRIVATE_DOCUMENT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(PRIVATE_DOCUMENT_DIR, 0o700)
    key = secrets.token_hex(32)
    target = PRIVATE_DOCUMENT_DIR / f"{key}.enc"
    document = ExchangeApplicationDocument(application_id=application.id, uploaded_by=actor.id,
        storage_key=key, original_filename=filename, file_size=len(content))
    with target.open("xb") as output:
        os.chmod(target, 0o600)
        output.write(cipher.encrypt(content))
    db.add(document)
    try:
        db.flush()
        db.add(AuditEvent(actor_user_id=actor.id, action="exchange.document_uploaded",
            entity_type="exchange_application_document", entity_id=str(document.id),
            details={"application_id": application.id, "bytes": len(content)}))
        db.commit()
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    db.refresh(document)
    return _document_metadata(document)


@router.get("/api/v1/members/me/exchange-applications/{application_id}/documents")
def list_my_exchange_documents(application_id: int, request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    application = db.scalar(select(ExchangeApplication).where(
        ExchangeApplication.id == application_id, ExchangeApplication.user_id == actor.id))
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    rows = db.scalars(select(ExchangeApplicationDocument).where(
        ExchangeApplicationDocument.application_id == application_id).order_by(ExchangeApplicationDocument.created_at))
    return [_document_metadata(row) for row in rows]


@router.get("/api/v1/members/me/exchange-applications/{application_id}/documents/{document_id}")
def download_my_exchange_document(application_id: int, document_id: int, request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    document = db.scalar(select(ExchangeApplicationDocument).join(ExchangeApplication).where(
        ExchangeApplicationDocument.id == document_id, ExchangeApplicationDocument.application_id == application_id,
        ExchangeApplication.user_id == actor.id))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_download(document, actor, db)


@router.get("/api/v1/admin/exchanges/applications/{application_id}/documents")
def list_exchange_documents_for_review(application_id: int, request: Request, db: Session = Depends(get_db)):
    _require_reviewer(request, db)
    if not db.get(ExchangeApplication, application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    rows = db.scalars(select(ExchangeApplicationDocument).where(
        ExchangeApplicationDocument.application_id == application_id).order_by(ExchangeApplicationDocument.created_at))
    return [_document_metadata(row) for row in rows]


@router.get("/api/v1/admin/exchanges/applications/{application_id}/documents/{document_id}")
def download_exchange_document_for_review(application_id: int, document_id: int, request: Request, db: Session = Depends(get_db)):
    actor = _require_reviewer(request, db)
    document = db.scalar(select(ExchangeApplicationDocument).where(
        ExchangeApplicationDocument.id == document_id,
        ExchangeApplicationDocument.application_id == application_id))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_download(document, actor, db)
