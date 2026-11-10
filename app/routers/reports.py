from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from datetime import timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.google_sheets import enqueue_google_sync, run_google_sync
from app.models import (AuditEvent, CommitteeReport, CommitteeReportComment,
                        CommitteeReportHistory, GoogleSyncRun, LocalCommittee, Role, User,
                        UserRoleAssignment, utcnow)
from app.notifications import notify_committee_officers, notify_roles
from app.rbac import NATIONAL_ROLES, committee_scope, role_assignments
from app.schemas import (CommitteeReportCommentIn, CommitteeReportCommentOut,
                         CommitteeReportOut, CommitteeReportReview,
                         CommitteeReportWrite, ExchangeHistoryOut)
from app.security import current_user, require_csrf

router = APIRouter(tags=["committee reports"])
WRITER_ROLES = {"lc_director", "local_officer"}


def _codes(assignments: list[UserRoleAssignment]) -> set[str]:
    return {assignment.role.code for assignment in assignments}


def _writer_scope(db: Session, user: User) -> list[int]:
    assignments = role_assignments(db, user)
    scopes = sorted({row.scope_id for row in assignments if row.role.code in WRITER_ROLES
                     and row.scope_type == "local_committee" and row.scope_id is not None})
    if not scopes:
        raise HTTPException(status_code=403, detail="Local committee officer role required")
    return scopes


def _is_national(db: Session, user: User) -> bool:
    return bool(_codes(role_assignments(db, user)) & NATIONAL_ROLES)


def _report_access(db: Session, user: User, report: CommitteeReport) -> bool:
    if _is_national(db, user):
        return True
    return report.committee_id in _writer_scope(db, user)


def _out(report: CommitteeReport) -> CommitteeReportOut:
    return CommitteeReportOut(id=report.id, committee_id=report.committee_id,
        committee_name=report.committee.name, period=report.period,
        executive_summary=report.executive_summary, activities=report.activities,
        impact=report.impact, challenges=report.challenges, next_steps=report.next_steps,
        members_count=report.members_count, events_count=report.events_count,
        status=report.status, submitted_at=report.submitted_at, updated_at=report.updated_at)


def _report_stmt():
    return select(CommitteeReport).options(joinedload(CommitteeReport.committee))


@router.get("/api/v1/reports/committees")
def report_committees(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    scope = _writer_scope(db, user)
    rows = db.scalars(select(LocalCommittee).where(LocalCommittee.id.in_(scope)).order_by(LocalCommittee.name))
    return [{"id": row.id, "name": row.name} for row in rows]


@router.get("/api/v1/reports", response_model=list[CommitteeReportOut])
def list_my_reports(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if _is_national(db, user):
        stmt = _report_stmt().order_by(CommitteeReport.period.desc(), CommitteeReport.updated_at.desc())
    else:
        scope = _writer_scope(db, user)
        stmt = _report_stmt().where(CommitteeReport.committee_id.in_(scope)).order_by(CommitteeReport.period.desc(), CommitteeReport.updated_at.desc())
    return [_out(row) for row in db.scalars(stmt).unique()]


@router.post("/api/v1/reports", response_model=CommitteeReportOut, status_code=status.HTTP_201_CREATED)
def create_report(payload: CommitteeReportWrite, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    require_csrf(request, db)
    if payload.committee_id not in _writer_scope(db, user):
        raise HTTPException(status_code=403, detail="This committee is outside your report scope")
    if not db.get(LocalCommittee, payload.committee_id):
        raise HTTPException(status_code=404, detail="Committee not found")
    report = CommitteeReport(**payload.model_dump())
    db.add(report)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A report for this committee and quarter already exists") from exc
    db.add(CommitteeReportHistory(report_id=report.id, actor_user_id=user.id,
        from_status=None, to_status="draft", note="Draft created"))
    db.add(AuditEvent(actor_user_id=user.id, action="report.created", entity_type="committee_report",
        entity_id=str(report.id), details={"committee_id": report.committee_id, "period": report.period}))
    db.commit()
    db.refresh(report)
    report.committee = db.get(LocalCommittee, report.committee_id)
    return _out(report)


@router.patch("/api/v1/reports/{report_id}", response_model=CommitteeReportOut)
def update_report(report_id: int, payload: CommitteeReportWrite, request: Request,
                  db: Session = Depends(get_db)):
    user = current_user(request, db)
    require_csrf(request, db)
    scope = _writer_scope(db, user)
    report = db.scalar(_report_stmt().where(CommitteeReport.id == report_id))
    if not report or report.committee_id not in scope:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status not in {"draft", "changes_requested"}:
        raise HTTPException(status_code=409, detail="Only a draft or report returned for changes can be edited")
    if payload.committee_id != report.committee_id:
        raise HTTPException(status_code=422, detail="A report cannot be moved to another committee")
    for key, value in payload.model_dump(exclude={"committee_id"}).items():
        setattr(report, key, value)
    report.updated_at = utcnow()
    db.add(AuditEvent(actor_user_id=user.id, action="report.updated", entity_type="committee_report",
        entity_id=str(report.id), details={"period": report.period}))
    db.commit()
    db.refresh(report)
    return _out(report)


@router.post("/api/v1/reports/{report_id}/submit", response_model=CommitteeReportOut)
def submit_report(report_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    require_csrf(request, db)
    scope = _writer_scope(db, user)
    report = db.scalar(_report_stmt().where(CommitteeReport.id == report_id))
    if not report or report.committee_id not in scope:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status not in {"draft", "changes_requested"}:
        raise HTTPException(status_code=409, detail="This report cannot be submitted in its current state")
    previous = report.status
    report.status = "submitted"
    report.submitted_by = user.id
    report.submitted_at = utcnow()
    report.updated_at = utcnow()
    db.add(CommitteeReportHistory(report_id=report.id, actor_user_id=user.id,
        from_status=previous, to_status="submitted", note=None))
    db.add(AuditEvent(actor_user_id=user.id, action="report.submitted", entity_type="committee_report",
        entity_id=str(report.id), details={"committee_id": report.committee_id, "period": report.period}))
    notify_roles(db, ["super_admin", "national_board"], kind="report_submitted",
        title="Новый отчёт комитета", body=f"{report.committee.name} отправил отчёт {report.period}.",
        link="/reports", exclude_user_id=user.id)
    db.commit()
    db.refresh(report)
    return _out(report)


@router.get("/api/v1/reports/{report_id}/comments", response_model=list[CommitteeReportCommentOut])
def list_report_comments(report_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    report = db.get(CommitteeReport, report_id)
    if not report or not _report_access(db, user, report):
        raise HTTPException(status_code=404, detail="Report not found")
    rows = db.scalars(select(CommitteeReportComment).options(joinedload(CommitteeReportComment.author)).where(
        CommitteeReportComment.report_id == report_id).order_by(CommitteeReportComment.created_at))
    return [CommitteeReportCommentOut(id=row.id,
        author_name=row.author.profile.full_name if row.author and row.author.profile else "AMSA reviewer",
        body=row.body, created_at=row.created_at) for row in rows]


@router.post("/api/v1/reports/{report_id}/comments", response_model=CommitteeReportCommentOut, status_code=status.HTTP_201_CREATED)
def add_report_comment(report_id: int, payload: CommitteeReportCommentIn, request: Request,
                       db: Session = Depends(get_db)):
    user = current_user(request, db)
    require_csrf(request, db)
    report = db.get(CommitteeReport, report_id)
    if not report or not _report_access(db, user, report):
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status == "approved":
        raise HTTPException(status_code=409, detail="Approved reports are read-only")
    comment = CommitteeReportComment(report_id=report.id, author_user_id=user.id, body=payload.body)
    db.add(comment)
    db.add(AuditEvent(actor_user_id=user.id, action="report.comment_added", entity_type="committee_report",
        entity_id=str(report.id), details={}))
    if _is_national(db, user):
        notify_committee_officers(db, report.committee_id, kind="report_comment",
            title="Комментарий к отчёту комитета", body="В отчёте появился новый комментарий.",
            link="/reports", exclude_user_id=user.id)
    else:
        notify_roles(db, ["super_admin", "national_board"], kind="report_comment",
            title="Комментарий к отчёту комитета", body=f"К отчёту {report.period} добавили комментарий.",
            link="/reports", exclude_user_id=user.id)
    db.commit()
    db.refresh(comment)
    return CommitteeReportCommentOut(id=comment.id, author_name=user.profile.full_name if user.profile else user.username,
                                    body=comment.body, created_at=comment.created_at)


@router.get("/api/v1/reports/{report_id}/history", response_model=list[ExchangeHistoryOut])
def report_history(report_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    report = db.get(CommitteeReport, report_id)
    if not report or not _report_access(db, user, report):
        raise HTTPException(status_code=404, detail="Report not found")
    return db.scalars(select(CommitteeReportHistory).where(CommitteeReportHistory.report_id == report_id)
                      .order_by(CommitteeReportHistory.created_at))


@router.get("/api/v1/admin/reports", response_model=list[CommitteeReportOut])
def list_all_reports(request: Request, status_filter: str | None = None, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not _is_national(db, user):
        raise HTTPException(status_code=403, detail="National report reviewer role required")
    stmt = _report_stmt().order_by(CommitteeReport.period.desc(), CommitteeReport.updated_at.desc())
    if status_filter:
        if status_filter not in {"draft", "submitted", "under_review", "changes_requested", "approved"}:
            raise HTTPException(status_code=422, detail="Unsupported report status")
        stmt = stmt.where(CommitteeReport.status == status_filter)
    return [_out(row) for row in db.scalars(stmt).unique()]


@router.get("/api/v1/admin/reports/google-sheets/status")
def google_sheets_sync_status(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not _is_national(db, user):
        raise HTTPException(status_code=403, detail="National report reviewer role required")
    settings = get_settings()
    runs = db.scalars(select(GoogleSyncRun).options(joinedload(GoogleSyncRun.report).joinedload(CommitteeReport.committee))
        .order_by(GoogleSyncRun.created_at.desc(), GoogleSyncRun.id.desc()).limit(100))
    return {"enabled": settings.google_sheets_sync_configured, "sheet_name": settings.google_reports_sheet_name,
        "runs": [{"id": run.id, "report_id": run.report_id,
            "committee_name": run.report.committee.name, "period": run.report.period,
            "status": run.status, "error_code": run.error_code,
            "sheet_range": run.sheet_range, "created_at": run.created_at,
            "finished_at": run.finished_at} for run in runs]}


@router.patch("/api/v1/admin/reports/{report_id}", response_model=CommitteeReportOut)
def review_report(report_id: int, payload: CommitteeReportReview, request: Request,
                  background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = current_user(request, db)
    require_csrf(request, db)
    if not _is_national(db, user):
        raise HTTPException(status_code=403, detail="National report reviewer role required")
    report = db.scalar(_report_stmt().where(CommitteeReport.id == report_id))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    transitions = {"submitted": {"under_review", "changes_requested", "approved"},
                   "under_review": {"changes_requested", "approved"}}
    if payload.status not in transitions.get(report.status, set()):
        raise HTTPException(status_code=409, detail=f"Cannot move report from {report.status} to {payload.status}")
    previous = report.status
    report.status = payload.status
    report.reviewed_by = user.id
    report.updated_at = utcnow()
    db.add(CommitteeReportHistory(report_id=report.id, actor_user_id=user.id,
        from_status=previous, to_status=payload.status, note=payload.comment))
    db.add(CommitteeReportComment(report_id=report.id, author_user_id=user.id, body=payload.comment))
    db.add(AuditEvent(actor_user_id=user.id, action="report.reviewed", entity_type="committee_report",
        entity_id=str(report.id), details={"from": previous, "to": payload.status}))
    notify_committee_officers(db, report.committee_id, kind="report_status",
        title="Обновлён статус отчёта комитета", body=payload.comment,
        link="/reports", exclude_user_id=user.id)
    db.commit()
    db.refresh(report)
    if payload.status == "approved":
        sync_run = enqueue_google_sync(db, report, actor_user_id=user.id)
        db.commit()
        if sync_run.status == "queued":
            background_tasks.add_task(run_google_sync, report.id, sync_run.id)
    return _out(report)


@router.post("/api/v1/admin/reports/{report_id}/sync")
def retry_google_sheets_sync(report_id: int, request: Request,
                             background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = current_user(request, db)
    require_csrf(request, db)
    if not _is_national(db, user):
        raise HTTPException(status_code=403, detail="National report reviewer role required")
    report = db.scalar(_report_stmt().where(CommitteeReport.id == report_id))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status != "approved":
        raise HTTPException(status_code=409, detail="Only approved reports can be synced")
    latest = db.scalar(select(GoogleSyncRun).where(GoogleSyncRun.report_id == report.id)
        .order_by(GoogleSyncRun.created_at.desc(), GoogleSyncRun.id.desc()).limit(1))
    if latest and latest.status == "queued":
        queued_at = latest.created_at if latest.created_at.tzinfo else latest.created_at.replace(tzinfo=timezone.utc)
        if queued_at > utcnow() - timedelta(minutes=2):
            raise HTTPException(status_code=409, detail="A synchronization attempt is already queued")
    sync_run = enqueue_google_sync(db, report, actor_user_id=user.id)
    db.commit()
    if sync_run.status == "queued":
        background_tasks.add_task(run_google_sync, report.id, sync_run.id)
    return {"id": sync_run.id, "status": sync_run.status, "error_code": sync_run.error_code}


def google_form_embed_url() -> str | None:
    return get_settings().google_reports_embed_url
