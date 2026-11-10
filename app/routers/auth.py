from datetime import timedelta
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.login_limiter import (login_bucket_keys, record_throttled_attempt, record_login_success,
    registration_bucket_keys, retry_after_seconds)
from app.models import AuditEvent, MemberProfile, Role, User, UserRoleAssignment, UserSession, utcnow
from app.rbac import role_codes
from app.schemas import LoginIn, PasswordChangeIn, RegisterIn
from app.security import clear_session_cookie, current_user, hash_password, new_session, require_csrf, session_from_request, set_session_cookie, token_digest, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
settings = get_settings()
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


@router.get("/csrf")
def issue_csrf(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    row = session_from_request(request, db)
    if row:
        return {"csrf_token": row.csrf_token}
    raw_token, csrf_token, _ = new_session(db)
    db.commit()
    set_session_cookie(response, raw_token)
    return {"csrf_token": csrf_token}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, request: Request, response: Response, x_csrf_token: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict:
    anon = require_csrf(request, db)
    if anon.user_id is not None:
        raise HTTPException(status_code=409, detail="Sign out before registering another account")
    remote_ip = request.client.host if request.client else "unknown"
    registration_buckets = registration_bucket_keys(remote_ip)
    retry_after = retry_after_seconds(db, registration_buckets)
    if retry_after:
        raise HTTPException(status_code=429, detail="Too many registration attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})
    member_role = db.scalar(select(Role).where(Role.code == "member"))
    if not member_role:
        raise HTTPException(status_code=503, detail="Role data is not initialized; apply database migrations")
    record_throttled_attempt(db, registration_buckets)
    db.commit()
    user = User(username=payload.username, email=payload.email, password_hash=hash_password(payload.password), account_status="active")
    user.profile = MemberProfile(
        full_name=payload.full_name,
        university_name=payload.university_name,
        city_name=payload.city_name,
        study_year=payload.study_year,
        membership_status="pending",
        profile_visibility="private",
    )
    user.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This username or email is already in use") from exc
    db.add(AuditEvent(actor_user_id=user.id, action="member.registered", entity_type="member_profile", entity_id=str(user.profile.id), details={"membership_status": "pending"}))
    raw_token, csrf_token, _ = new_session(db, user.id)
    db.delete(anon)
    db.commit()
    set_session_cookie(response, raw_token)
    return {"id": user.id, "username": user.username, "email": user.email, "membership_status": user.profile.membership_status, "csrf_token": csrf_token}


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    anon = require_csrf(request, db)
    if anon.user_id is not None:
        raise HTTPException(status_code=409, detail="Already signed in")
    remote_ip = request.client.host if request.client else "unknown"
    buckets = login_bucket_keys(payload.username, remote_ip)
    retry_after = retry_after_seconds(db, buckets)
    if retry_after:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})
    user = db.scalar(select(User).where(User.username == payload.username))
    password_matches = verify_password(payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if not user or not password_matches or user.account_status != "active":
        record_throttled_attempt(db, buckets)
        db.commit()
        raise HTTPException(status_code=401, detail="Username or password is incorrect")
    record_login_success(db, buckets)
    db.delete(anon)
    raw_token, csrf_token, _ = new_session(db, user.id)
    db.add(AuditEvent(actor_user_id=user.id, action="auth.login", entity_type="user", entity_id=str(user.id), details={}))
    db.commit()
    set_session_cookie(response, raw_token)
    response.headers["Cache-Control"] = "no-store, private"
    return {"id": user.id, "username": user.username, "email": user.email, "roles": role_codes(db, user),
            "password_reset_required": user.password_reset_required, "csrf_token": csrf_token}


@router.get("/me")
def get_me(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    return {"id": user.id, "username": user.username, "email": user.email, "account_status": user.account_status, "roles": role_codes(db, user)}


@router.post("/password")
def change_password(payload: PasswordChangeIn, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    user = current_user(request, db, allow_password_reset=True)
    current_session = require_csrf(request, db)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.password_reset_required = False
    other_sessions = db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.token_hash != current_session.token_hash)).all()
    for session in other_sessions:
        db.delete(session)
    db.add(AuditEvent(actor_user_id=user.id, action="auth.password_changed", entity_type="user", entity_id=str(user.id), details={"other_sessions_revoked": len(other_sessions)}))
    db.commit()
    return {"status": "password_changed"}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    user = current_user(request, db, allow_password_reset=True)
    row = require_csrf(request, db)
    db.add(AuditEvent(actor_user_id=user.id, action="auth.logout", entity_type="user", entity_id=str(user.id), details={}))
    db.delete(row)
    db.commit()
    clear_session_cookie(response)
    return {"status": "signed_out"}
