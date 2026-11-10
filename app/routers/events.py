import secrets
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import AuditEvent, Event, EventCertificate, EventRegistration, LocalCommittee, MemberProfile, User, utcnow
from app.notifications import notify_user
from app.rbac import committee_scope, require_member_admin, role_assignments
from app.schemas import (AttendanceUpdate, CertificateRevokeIn, CertificateVerifyOut, EventCertificateOut,
                         EventCreate, EventOut, EventRegistrationAdminOut,
                         EventRegistrationOut)
from app.security import current_user, require_csrf, session_from_request

router = APIRouter(tags=["events"])


def out(db: Session, event: Event, user_id: int | None = None) -> EventOut:
    count = db.scalar(select(func.count(EventRegistration.id)).where(
        EventRegistration.event_id == event.id, EventRegistration.status.in_(["confirmed", "attended"]))) or 0
    mine = db.scalar(select(EventRegistration.status).where(
        EventRegistration.event_id == event.id, EventRegistration.user_id == user_id)) if user_id else None
    return EventOut(id=event.id, committee_id=event.committee_id, title=event.title, summary=event.summary, description=event.description,
                    category=event.category, location=event.location, starts_at=event.starts_at,
                    ends_at=event.ends_at, registration_deadline=event.registration_deadline,
                    capacity=event.capacity, registered_count=count, my_status=mine, status=event.status)


def _event_admin(db: Session, request: Request, event_id: int):
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    scope = committee_scope(assignments)
    event = db.get(Event, event_id)
    if not event or (scope is not None and event.committee_id not in scope):
        raise HTTPException(status_code=404, detail="Event not found")
    return actor, event


def _certificate_out(certificate: EventCertificate, *, include_print: bool = False) -> EventCertificateOut:
    return EventCertificateOut(id=certificate.id, event_id=certificate.event_id,
        event_title=certificate.event.title, public_code=certificate.public_code,
        status=certificate.status, issued_at=certificate.issued_at,
        verification_url=f"/certificate/verify/{certificate.public_code}",
        print_url=f"/certificates/{certificate.id}/print" if include_print else None)


@router.get("/api/v1/events", response_model=list[EventOut])
def public_events(request: Request, db: Session = Depends(get_db)):
    session = session_from_request(request, db)
    stmt = select(Event).where(Event.status == "published").order_by(Event.starts_at.asc())
    return [out(db, event, session.user_id if session else None) for event in db.scalars(stmt)]


@router.get("/api/v1/members/me/events", response_model=list[EventOut])
def my_events(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    stmt = select(Event).join(EventRegistration).where(EventRegistration.user_id == user.id).order_by(Event.starts_at.asc())
    return [out(db, event, user.id) for event in db.scalars(stmt)]


@router.get("/api/v1/members/me/certificates", response_model=list[EventCertificateOut])
def my_certificates(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    stmt = select(EventCertificate).options(joinedload(EventCertificate.event)).where(
        EventCertificate.user_id == user.id).order_by(EventCertificate.issued_at.desc())
    return [_certificate_out(row, include_print=row.status == "issued") for row in db.scalars(stmt).unique()]


@router.get("/api/v1/certificates/verify/{public_code}", response_model=CertificateVerifyOut)
def verify_certificate(public_code: str, db: Session = Depends(get_db)):
    if not 32 <= len(public_code) <= 64:
        raise HTTPException(status_code=404, detail="Certificate not found")
    certificate = db.scalar(select(EventCertificate).options(joinedload(EventCertificate.event),
        joinedload(EventCertificate.user).joinedload(User.profile)).where(EventCertificate.public_code == public_code))
    if not certificate:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return CertificateVerifyOut(is_valid=certificate.status == "issued",
        recipient_name=certificate.user.profile.full_name if certificate.user.profile else "AMSA Member",
        event_title=certificate.event.title, issued_at=certificate.issued_at,
        certificate_code=certificate.public_code)


@router.get("/api/v1/admin/events/{event_id}/registrations", response_model=list[EventRegistrationAdminOut])
def admin_event_registrations(event_id: int, request: Request, db: Session = Depends(get_db)):
    _event_admin(db, request, event_id)
    registrations = db.scalars(select(EventRegistration).options(
        joinedload(EventRegistration.user).joinedload(User.profile)).where(
            EventRegistration.event_id == event_id).order_by(EventRegistration.created_at, EventRegistration.id)).unique()
    certificates = {row.user_id: row for row in db.scalars(select(EventCertificate).options(
        joinedload(EventCertificate.event)).where(EventCertificate.event_id == event_id))}
    result = []
    for registration in registrations:
        profile = registration.user.profile
        certificate = certificates.get(registration.user_id)
        result.append(EventRegistrationAdminOut(user_id=registration.user_id,
            full_name=profile.full_name if profile else registration.user.username,
            username=registration.user.username,
            university_name=profile.university_name if profile else "",
            study_year=profile.study_year if profile else None, status=registration.status,
            certificate_id=certificate.id if certificate else None,
            certificate_status=certificate.status if certificate else None,
            verification_url=f"/certificate/verify/{certificate.public_code}" if certificate else None))
    return result


@router.patch("/api/v1/admin/events/{event_id}/registrations/{user_id}/attendance")
def update_attendance(event_id: int, user_id: int, payload: AttendanceUpdate,
                      request: Request, db: Session = Depends(get_db)) -> dict:
    actor, event = _event_admin(db, request, event_id)
    require_csrf(request, db)
    registration = db.scalar(select(EventRegistration).where(
        EventRegistration.event_id == event.id, EventRegistration.user_id == user_id))
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found")
    starts_at = event.starts_at if event.starts_at.tzinfo else event.starts_at.replace(tzinfo=timezone.utc)
    if starts_at > utcnow():
        raise HTTPException(status_code=409, detail="Attendance can only be recorded after the event starts")
    if registration.status == "waitlisted":
        raise HTTPException(status_code=409, detail="A waitlisted participant cannot be marked present")
    if registration.status not in {"confirmed", "attended", "no_show"}:
        raise HTTPException(status_code=409, detail="Unsupported registration state")
    certificate = db.scalar(select(EventCertificate.id).where(
        EventCertificate.event_id == event.id, EventCertificate.user_id == user_id,
        EventCertificate.status == "issued"))
    if certificate and not payload.attended:
        raise HTTPException(status_code=409, detail="Revoke the issued certificate before correcting attendance")
    previous = registration.status
    registration.status = "attended" if payload.attended else "no_show"
    db.add(AuditEvent(actor_user_id=actor.id, action="event.attendance_changed",
        entity_type="event_registration", entity_id=str(registration.id),
        details={"from": previous, "to": registration.status, "event_id": event.id}))
    notify_user(db, user_id, kind="event_attendance", title="Посещаемость мероприятия обновлена",
        body=f"«{event.title}»: {'присутствие подтверждено' if payload.attended else 'отмечено отсутствие'}.",
        link="/cabinet")
    db.commit()
    return {"event_id": event.id, "user_id": user_id, "attendance_status": registration.status}


@router.post("/api/v1/admin/events/{event_id}/certificates/{user_id}", response_model=EventCertificateOut,
             status_code=status.HTTP_201_CREATED)
def issue_event_certificate(event_id: int, user_id: int, request: Request,
                            db: Session = Depends(get_db)) -> EventCertificateOut:
    actor, event = _event_admin(db, request, event_id)
    require_csrf(request, db)
    if event.status != "published":
        raise HTTPException(status_code=409, detail="Certificates can only be issued for a published event")
    end_at = event.ends_at or event.starts_at
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    if end_at > utcnow():
        raise HTTPException(status_code=409, detail="The event must have ended before certificates can be issued")
    registration = db.scalar(select(EventRegistration).where(
        EventRegistration.event_id == event.id, EventRegistration.user_id == user_id))
    if not registration or registration.status != "attended":
        raise HTTPException(status_code=409, detail="Mark the member as attended before issuing a certificate")
    existing = db.scalar(select(EventCertificate).options(joinedload(EventCertificate.event)).where(
        EventCertificate.event_id == event.id, EventCertificate.user_id == user_id))
    if existing:
        if existing.status == "revoked":
            raise HTTPException(status_code=409, detail="This certificate was revoked and cannot be reissued")
        raise HTTPException(status_code=409, detail="A certificate has already been issued")
    certificate = EventCertificate(event_id=event.id, user_id=user_id,
        public_code=secrets.token_urlsafe(32), issued_by=actor.id)
    db.add(certificate)
    db.flush()
    db.add(AuditEvent(actor_user_id=actor.id, action="certificate.issued", entity_type="event_certificate",
        entity_id=str(certificate.id), details={"event_id": event.id, "user_id": user_id}))
    notify_user(db, user_id, kind="event_certificate", title="Сертификат готов",
        body=f"Сертификат участия в мероприятии «{event.title}» добавлен в кабинет.",
        link="/cabinet#my-certificates")
    db.commit()
    db.refresh(certificate)
    certificate.event = event
    return _certificate_out(certificate)


@router.post("/api/v1/admin/events/{event_id}/certificates/{certificate_id}/revoke")
def revoke_event_certificate(event_id: int, certificate_id: int, payload: CertificateRevokeIn,
                             request: Request, db: Session = Depends(get_db)) -> dict:
    actor, event = _event_admin(db, request, event_id)
    require_csrf(request, db)
    reason = payload.reason
    certificate = db.scalar(select(EventCertificate).where(
        EventCertificate.id == certificate_id, EventCertificate.event_id == event.id))
    if not certificate:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if certificate.status != "issued":
        raise HTTPException(status_code=409, detail="Certificate is already revoked")
    certificate.status = "revoked"
    certificate.revoked_by = actor.id
    certificate.revoked_at = utcnow()
    certificate.revocation_reason = reason
    db.add(AuditEvent(actor_user_id=actor.id, action="certificate.revoked", entity_type="event_certificate",
        entity_id=str(certificate.id), details={"event_id": event.id, "reason": reason}))
    notify_user(db, certificate.user_id, kind="event_certificate_revoked", title="Сертификат отозван",
        body=f"Сертификат участия в «{event.title}» отозван. Обратись к организаторам для уточнения.",
        link="/cabinet#my-certificates")
    db.commit()
    return {"certificate_id": certificate.id, "status": certificate.status}


@router.post("/api/v1/events/{event_id}/register", response_model=EventRegistrationOut, status_code=status.HTTP_201_CREATED)
def register_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    require_csrf(request, db)
    profile = db.get(MemberProfile, db.scalar(select(MemberProfile.id).where(MemberProfile.user_id == user.id)))
    if not profile or profile.membership_status != "active":
        raise HTTPException(status_code=403, detail="Active membership is required to register")
    event = db.get(Event, event_id)
    if not event or event.status != "published":
        raise HTTPException(status_code=404, detail="Event not found")
    now = utcnow()
    deadline = event.registration_deadline
    if deadline and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline and deadline <= now:
        raise HTTPException(status_code=409, detail="Registration is closed")
    if db.scalar(select(EventRegistration.id).where(EventRegistration.event_id == event.id, EventRegistration.user_id == user.id)):
        raise HTTPException(status_code=409, detail="Already registered")
    count = db.scalar(select(func.count(EventRegistration.id)).where(EventRegistration.event_id == event.id, EventRegistration.status == "confirmed")) or 0
    registration = EventRegistration(event_id=event.id, user_id=user.id,
                                     status="waitlisted" if event.capacity is not None and count >= event.capacity else "confirmed")
    db.add(registration)
    db.add(AuditEvent(actor_user_id=user.id, action="event.registered", entity_type="event", entity_id=str(event.id), details={"status": registration.status}))
    notify_user(db, user.id, kind="event_registration", title="Запись на мероприятие обновлена",
        body=f"«{event.title}»: {'вы добавлены в лист ожидания' if registration.status == 'waitlisted' else 'место подтверждено'}.",
        link="/cabinet")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Already registered") from exc
    return EventRegistrationOut(event_id=event.id, status=registration.status)


@router.delete("/api/v1/events/{event_id}/register", status_code=status.HTTP_204_NO_CONTENT)
def cancel_event_registration(event_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    user = current_user(request, db)
    require_csrf(request, db)
    registration = db.scalar(select(EventRegistration).where(EventRegistration.event_id == event_id, EventRegistration.user_id == user.id))
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found")
    if registration.status in {"attended", "no_show"}:
        raise HTTPException(status_code=409, detail="Attendance has already been recorded")
    event = db.get(Event, event_id)
    event_title = event.title if event else "Мероприятие AMSA"
    if event:
        starts_at = event.starts_at if event.starts_at.tzinfo else event.starts_at.replace(tzinfo=timezone.utc)
        if starts_at <= utcnow():
            raise HTTPException(status_code=409, detail="Registration can no longer be cancelled after the event starts")
    was_confirmed = registration.status == "confirmed"
    db.add(AuditEvent(actor_user_id=user.id, action="event.registration_cancelled", entity_type="event", entity_id=str(event_id), details={}))
    db.delete(registration)
    if was_confirmed:
        next_in_line = db.scalar(select(EventRegistration).where(
            EventRegistration.event_id == event_id, EventRegistration.status == "waitlisted"
        ).order_by(EventRegistration.created_at.asc(), EventRegistration.id.asc()).limit(1))
        if next_in_line:
            next_in_line.status = "confirmed"
            db.add(AuditEvent(actor_user_id=user.id, action="event.waitlist_promoted", entity_type="event", entity_id=str(event_id), details={"registration_id": next_in_line.id}))
            notify_user(db, next_in_line.user_id, kind="event_waitlist", title="Появилось место на мероприятии",
                body=f"«{event_title}»: ваша запись подтверждена.", link="/cabinet")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/v1/admin/events", response_model=list[EventOut])
def admin_events(request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    scope = committee_scope(assignments)
    stmt = select(Event).order_by(Event.starts_at.desc())
    if scope is not None:
        if not scope:
            return []
        stmt = stmt.where(Event.committee_id.in_(scope))
    return [out(db, event) for event in db.scalars(stmt)]


@router.post("/api/v1/admin/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    require_csrf(request, db)
    assignments = require_member_admin(db, actor)
    scope = committee_scope(assignments)
    if scope is not None:
        if len(scope) != 1:
            raise HTTPException(status_code=403, detail="An event needs a committee you manage")
        if payload.committee_id not in {None, scope[0]}:
            raise HTTPException(status_code=403, detail="Event committee is outside your permissions")
        payload.committee_id = scope[0]
    if payload.committee_id is not None and not db.get(LocalCommittee, payload.committee_id):
        raise HTTPException(status_code=422, detail="Committee not found")
    if payload.ends_at and payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="End time must be after start time")
    event = Event(**payload.model_dump(), created_by=actor.id)
    db.add(event)
    db.flush()
    db.add(AuditEvent(actor_user_id=actor.id, action="event.created", entity_type="event", entity_id=str(event.id), details={"status": event.status}))
    db.commit()
    db.refresh(event)
    return out(db, event)


@router.patch("/api/v1/admin/events/{event_id}", response_model=EventOut)
def update_event(event_id: int, payload: EventCreate, request: Request, db: Session = Depends(get_db)):
    actor = current_user(request, db)
    require_csrf(request, db)
    assignments = require_member_admin(db, actor)
    scope = committee_scope(assignments)
    event = db.get(Event, event_id)
    if not event or (scope is not None and event.committee_id not in scope):
        raise HTTPException(status_code=404, detail="Event not found")
    if scope is not None and payload.committee_id not in {None, event.committee_id}:
        raise HTTPException(status_code=403, detail="You cannot move an event outside your committee")
    if scope is not None:
        payload.committee_id = event.committee_id
    elif payload.committee_id is not None and not db.get(LocalCommittee, payload.committee_id):
        raise HTTPException(status_code=422, detail="Committee not found")
    if payload.ends_at and payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="End time must be after start time")
    for field, value in payload.model_dump().items():
        setattr(event, field, value)
    db.add(AuditEvent(actor_user_id=actor.id, action="event.updated", entity_type="event", entity_id=str(event.id), details={"status": event.status}))
    db.commit()
    db.refresh(event)
    return out(db, event)
