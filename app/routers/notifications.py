from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notification, utcnow
from app.schemas import NotificationListOut, NotificationOut
from app.security import current_user, require_csrf

router = APIRouter(tags=["notifications"])


@router.get("/api/v1/members/me/notifications", response_model=NotificationListOut)
def my_notifications(request: Request, limit: int = 30, before_id: int | None = None,
                     unread_only: bool = False, db: Session = Depends(get_db)) -> NotificationListOut:
    user = current_user(request, db)
    limit = max(1, min(limit, 100))
    unread_count = db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
    stmt = select(Notification).where(Notification.user_id == user.id).order_by(
        Notification.created_at.desc(), Notification.id.desc())
    if before_id is not None:
        stmt = stmt.where(Notification.id < before_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = list(db.scalars(stmt.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [NotificationOut(id=row.id, kind=row.kind, title=row.title, body=row.body,
        link=row.link, read_at=row.read_at, created_at=row.created_at) for row in rows]
    return NotificationListOut(items=items, unread_count=unread_count, has_more=has_more,
                                next_before_id=rows[-1].id if has_more and rows else None)


@router.patch("/api/v1/members/me/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_notification_read(notification_id: int, request: Request,
                           db: Session = Depends(get_db)) -> NotificationOut:
    user = current_user(request, db)
    require_csrf(request, db)
    notification = db.scalar(select(Notification).where(Notification.id == notification_id,
        Notification.user_id == user.id))
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.read_at is None:
        notification.read_at = utcnow()
        db.commit()
        db.refresh(notification)
    return NotificationOut(id=notification.id, kind=notification.kind, title=notification.title,
        body=notification.body, link=notification.link, read_at=notification.read_at,
        created_at=notification.created_at)


@router.post("/api/v1/members/me/notifications/read-all")
def mark_all_notifications_read(request: Request, db: Session = Depends(get_db)) -> dict[str, int]:
    user = current_user(request, db)
    require_csrf(request, db)
    result = db.execute(update(Notification).where(Notification.user_id == user.id,
        Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return {"marked_read": result.rowcount or 0}
