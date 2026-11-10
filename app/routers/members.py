import csv
import io
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import AuditEvent, Event, LocalCommittee, MemberProfile, NewsArticle, Role, User, UserRoleAssignment, UserSession, utcnow
from app.notifications import notify_user
from app.rbac import committee_scope, require_member_admin
from app.schemas import AdminMemberOut, MemberMeOut, MemberProfileUpdate, MembershipStatusUpdate
from app.security import current_user, hash_password, require_csrf

router = APIRouter(tags=["members"])


@router.get("/api/v1/admin/summary")
def admin_summary(request: Request, db: Session = Depends(get_db)) -> dict[str, int]:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    scope = committee_scope(assignments)
    base = (select(func.count(func.distinct(MemberProfile.id))).join(User, User.id == MemberProfile.user_id)
            .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id).join(Role, Role.id == UserRoleAssignment.role_id)
            .where(Role.code == "member"))
    if scope is not None:
        base = base.where(MemberProfile.committee_id.in_(scope)) if scope else base.where(False)
    total_members = db.scalar(base) or 0
    active_members = db.scalar(base.where(MemberProfile.membership_status == "active")) or 0
    pending_members = db.scalar(base.where(MemberProfile.membership_status == "pending")) or 0
    committees = select(func.count(LocalCommittee.id))
    events = select(func.count(Event.id)).where(Event.status == "published", Event.starts_at >= utcnow())
    news = select(func.count(NewsArticle.id)).where(NewsArticle.status.in_(["draft", "scheduled"]))
    if scope is not None:
        committees = committees.where(LocalCommittee.id.in_(scope)) if scope else committees.where(False)
        events = events.where(Event.committee_id.in_(scope)) if scope else events.where(False)
        news = news.where(NewsArticle.committee_id.in_(scope)) if scope else news.where(False)
    return {"members": total_members, "active_members": active_members, "pending_members": pending_members,
            "committees": db.scalar(committees) or 0, "upcoming_events": db.scalar(events) or 0,
            "news_needing_attention": db.scalar(news) or 0}


def profile_response(db: Session, user: User) -> MemberMeOut:
    p = user.profile
    if not p:
        raise HTTPException(status_code=404, detail="Member profile not found")
    from app.rbac import role_codes
    return MemberMeOut(
        id=user.id, username=user.username, email=user.email, account_status=user.account_status, roles=role_codes(db, user),
        full_name=p.full_name, university_name=p.university_name, city_name=p.city_name,
        study_year=p.study_year, committee_interest=p.committee_interest, phone=p.phone,
        position_title=p.position_title, activity_area=p.activity_area,
        membership_status=p.membership_status, joined_on=p.joined_on, created_at=p.created_at,
    )


@router.get("/api/v1/members/me", response_model=MemberMeOut)
def read_own_profile(request: Request, db: Session = Depends(get_db)) -> MemberMeOut:
    user = current_user(request, db)
    user = db.scalar(select(User).options(joinedload(User.profile)).where(User.id == user.id))
    return profile_response(db, user)


@router.patch("/api/v1/members/me", response_model=MemberMeOut)
def update_own_profile(payload: MemberProfileUpdate, request: Request, db: Session = Depends(get_db)) -> MemberMeOut:
    user = current_user(request, db)
    require_csrf(request, db)
    user = db.scalar(select(User).options(joinedload(User.profile)).where(User.id == user.id))
    if not user.profile:
        raise HTTPException(status_code=404, detail="Member profile not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user.profile, field, value)
    user.profile.updated_at = utcnow()
    db.add(AuditEvent(actor_user_id=user.id, action="member.profile_updated", entity_type="member_profile", entity_id=str(user.profile.id), details={"fields": sorted(payload.model_fields_set)}))
    db.commit()
    db.refresh(user.profile)
    return profile_response(db, user)


def member_query(db: Session, assignments, q: str | None, university: str | None, city: str | None,
                 committee_id: int | None, position: str | None, membership_status: str | None):
    stmt = (select(MemberProfile).join(MemberProfile.user)
            .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .where(Role.code == "member")
            .options(joinedload(MemberProfile.user), joinedload(MemberProfile.committee)))
    scope = committee_scope(assignments)
    if scope is not None:
        if not scope:
            raise HTTPException(status_code=403, detail="No local committee scope is assigned")
        stmt = stmt.where(MemberProfile.committee_id.in_(scope))
    if q:
        term = f"%{q.strip()[:100]}%"
        stmt = stmt.join(MemberProfile.user).where(or_(MemberProfile.full_name.ilike(term), User.email.ilike(term), User.username.ilike(term), MemberProfile.university_name.ilike(term)))
    if university:
        stmt = stmt.where(MemberProfile.university_name.ilike(f"%{university[:120]}%"))
    if city:
        stmt = stmt.where(MemberProfile.city_name.ilike(f"%{city[:120]}%"))
    if committee_id is not None:
        stmt = stmt.where(MemberProfile.committee_id == committee_id)
    if position:
        stmt = stmt.where(MemberProfile.position_title.ilike(f"%{position[:120]}%"))
    if membership_status:
        stmt = stmt.where(MemberProfile.membership_status == membership_status)
    return stmt.order_by(MemberProfile.full_name.asc()).limit(500)


def admin_out(profile: MemberProfile) -> AdminMemberOut:
    return AdminMemberOut(
        id=profile.user.id, full_name=profile.full_name, username=profile.user.username, email=profile.user.email,
        university_name=profile.university_name, city_name=profile.city_name,
        study_year=profile.study_year, committee_id=profile.committee_id,
        committee_name=profile.committee.name if profile.committee else None,
        phone=profile.phone, position_title=profile.position_title, activity_area=profile.activity_area,
        membership_status=profile.membership_status, joined_on=profile.joined_on,
        password_reset_required=profile.user.password_reset_required,
        registered_at=profile.created_at,
    )


@router.get("/api/v1/admin/members", response_model=list[AdminMemberOut])
def list_members(request: Request, q: str | None = None, university: str | None = None,
                 city: str | None = None, committee_id: int | None = None,
                 position: str | None = None, membership_status: str | None = None,
                 db: Session = Depends(get_db)) -> list[AdminMemberOut]:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    stmt = member_query(db, assignments, q, university, city, committee_id, position, membership_status)
    return [admin_out(p) for p in db.scalars(stmt).unique().all()]


@router.get("/api/v1/admin/members.csv")
def export_members(request: Request, q: str | None = None, university: str | None = None,
                   city: str | None = None, committee_id: int | None = None,
                   position: str | None = None, membership_status: str | None = None,
                   db: Session = Depends(get_db)) -> StreamingResponse:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    stmt = member_query(db, assignments, q, university, city, committee_id, position, membership_status)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Full name", "Username", "Email", "University", "City", "Study year", "Committee", "Phone", "Position", "Activity area", "Membership status", "Joined on"])
    for p in db.scalars(stmt).unique():
        writer.writerow([p.full_name, p.user.username, p.user.email or "", p.university_name, p.city_name or "", p.study_year or "", p.committee.name if p.committee else "", p.phone or "", p.position_title or "", p.activity_area or "", p.membership_status, p.joined_on.isoformat() if p.joined_on else ""])
    data = "\ufeff" + output.getvalue()
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=amsa-members.csv", "Cache-Control": "no-store"})


@router.patch("/api/v1/admin/members/{user_id}/membership-status")
def update_membership_status(user_id: int, payload: MembershipStatusUpdate, request: Request,
                             db: Session = Depends(get_db)) -> dict:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    require_csrf(request, db)
    target = db.scalar(select(MemberProfile).options(joinedload(MemberProfile.user)).where(MemberProfile.user_id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    scope = committee_scope(assignments)
    if scope is not None and target.committee_id not in scope:
        raise HTTPException(status_code=403, detail="Member is outside your committee scope")
    before = target.membership_status
    target.membership_status = payload.status
    db.add(AuditEvent(actor_user_id=actor.id, action="member.status_changed", entity_type="member_profile", entity_id=str(target.id), details={"from": before, "to": payload.status}))
    notify_user(db, target.user_id, kind="membership_status", title="Обновлён статус членства",
        body=f"Статус членства изменён: {payload.status}.", link="/cabinet")
    db.commit()
    return {"user_id": user_id, "membership_status": payload.status}


@router.post("/api/v1/admin/members/{user_id}/password-reset")
def reset_member_password(user_id: int, request: Request, response: Response,
                          db: Session = Depends(get_db)) -> dict[str, str | int | bool]:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    require_csrf(request, db)
    target = db.scalar(select(MemberProfile).options(
        joinedload(MemberProfile.user).joinedload(User.role_assignments).joinedload(UserRoleAssignment.role)
    ).where(MemberProfile.user_id == user_id))
    if not target or not any(item.role.code == "member" for item in target.user.role_assignments):
        raise HTTPException(status_code=404, detail="Member not found")
    if target.user_id == actor.id:
        raise HTTPException(status_code=409, detail="Use your account password settings to change your own password")
    scope = committee_scope(assignments)
    if scope is not None and target.committee_id not in scope:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.user.account_status != "active":
        raise HTTPException(status_code=409, detail="This account is not active")

    temporary_password = secrets.token_urlsafe(18)
    target.user.password_hash = hash_password(temporary_password)
    target.user.password_reset_required = True
    sessions = db.scalars(select(UserSession).where(UserSession.user_id == target.user_id)).all()
    revoked_sessions = len(sessions)
    for session in sessions:
        db.delete(session)
    db.add(AuditEvent(actor_user_id=actor.id, action="auth.admin_password_reset",
        entity_type="user", entity_id=str(target.user_id),
        details={"revoked_sessions": revoked_sessions, "password_reset_required": True}))
    notify_user(db, target.user_id, kind="password_reset", title="Пароль сброшен администратором",
        body="Для входа получи временный пароль у администратора. При первом входе система попросит заменить его.",
        link="/login")
    db.commit()
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return {"user_id": target.user_id, "username": target.user.username,
            "temporary_password": temporary_password, "password_reset_required": True,
            "revoked_sessions": revoked_sessions}
