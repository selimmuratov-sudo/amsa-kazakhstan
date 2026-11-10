"""Database-backed auth throttling with HMAC pseudonyms instead of raw identifiers."""
from datetime import timedelta, timezone
import hashlib
import hmac

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LoginAttemptBucket, utcnow

WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)
LIMITS = {"ip": 50, "account_ip": 10, "registration_ip": 5}


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _bucket_hash(kind: str, value: str) -> str:
    settings = get_settings()
    pepper = settings.auth_rate_limit_pepper or settings.database_url
    return hmac.new(pepper.encode("utf-8"), f"{kind}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def login_bucket_keys(username: str, remote_ip: str) -> dict[str, str]:
    return {
        "ip": _bucket_hash("ip", remote_ip),
        "account_ip": _bucket_hash("account_ip", f"{username.lower()}\0{remote_ip}"),
    }


def registration_bucket_keys(remote_ip: str) -> dict[str, str]:
    return {"registration_ip": _bucket_hash("registration_ip", remote_ip)}


def _get_or_reset(db: Session, key: str, now):
    bucket = db.get(LoginAttemptBucket, key)
    if not bucket:
        return None
    if bucket.locked_until and _aware(bucket.locked_until) > now:
        return bucket
    if _aware(bucket.window_started_at) + WINDOW <= now:
        bucket.failed_attempts = 0
        bucket.window_started_at = now
        bucket.locked_until = None
    return bucket


def retry_after_seconds(db: Session, keys: dict[str, str]) -> int:
    now = utcnow()
    retry_after = 0
    for key in keys.values():
        bucket = _get_or_reset(db, key, now)
        if bucket and bucket.locked_until:
            retry_after = max(retry_after, int((_aware(bucket.locked_until) - now).total_seconds()) + 1)
    return retry_after


def record_throttled_attempt(db: Session, keys: dict[str, str]) -> None:
    now = utcnow()
    for kind, key in keys.items():
        bucket = _get_or_reset(db, key, now)
        if not bucket:
            bucket = LoginAttemptBucket(bucket_hash=key, failed_attempts=0, window_started_at=now)
            db.add(bucket)
        bucket.failed_attempts += 1
        if bucket.failed_attempts >= LIMITS[kind]:
            bucket.locked_until = now + LOCKOUT
    # Bound the pseudonymous table to roughly one day's worth of active buckets.
    db.execute(delete(LoginAttemptBucket).where(LoginAttemptBucket.window_started_at < now - timedelta(days=1))
        .execution_options(synchronize_session=False))


def record_login_success(db: Session, keys: dict[str, str]) -> None:
    # Preserve the broader IP bucket while clearing the successful username/IP pair.
    bucket = db.get(LoginAttemptBucket, keys["account_ip"])
    if bucket:
        db.delete(bucket)
