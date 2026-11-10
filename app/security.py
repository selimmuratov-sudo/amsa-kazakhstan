import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import HTTPException, Request, Response, status
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserSession, utcnow

password_hash = PasswordHash.recommended()
settings = get_settings()
SESSION_COOKIE = "amsa_platform_session"
CSRF_HEADER = "x-csrf-token"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(password, hashed)
    except Exception:
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure or settings.is_production,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure or settings.is_production, samesite="lax")


def new_session(db: Session, user_id: int | None = None) -> tuple[str, str, UserSession]:
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    row = UserSession(
        token_hash=token_digest(raw_token),
        csrf_token=csrf_token,
        user_id=user_id,
        expires_at=utcnow() + timedelta(days=settings.session_days),
    )
    db.add(row)
    db.flush()
    return raw_token, csrf_token, row


def session_from_request(request: Request, db: Session) -> UserSession | None:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == token_digest(raw_token)))
    if not row:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utcnow():
        db.delete(row)
        db.commit()
        return None
    return row


def require_csrf(request: Request, db: Session) -> UserSession:
    row = session_from_request(request, db)
    supplied = request.headers.get(CSRF_HEADER, "")
    if not row or not secrets.compare_digest(row.csrf_token, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
    return row


def current_user(request: Request, db: Session, *, allow_password_reset: bool = False) -> User:
    row = session_from_request(request, db)
    if not row or row.user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user = db.get(User, row.user_id)
    if not user or user.account_status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if user.password_reset_required and not allow_password_reset:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password change required")
    return user
