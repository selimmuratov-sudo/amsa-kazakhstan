"""Transactional helpers for user-facing platform notifications."""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Role, User, UserRoleAssignment


def notify_user(db: Session, user_id: int | None, *, kind: str, title: str,
                body: str = "", link: str | None = None) -> None:
    if user_id is None:
        return
    db.add(Notification(user_id=user_id, kind=kind, title=title[:180], body=body[:800], link=link))


def notify_roles(db: Session, role_codes: Iterable[str], *, kind: str, title: str,
                 body: str = "", link: str | None = None, exclude_user_id: int | None = None) -> None:
    codes = tuple(set(role_codes))
    if not codes:
        return
    stmt = select(User.id).join(UserRoleAssignment, UserRoleAssignment.user_id == User.id).join(
        Role, Role.id == UserRoleAssignment.role_id).where(
            Role.code.in_(codes), User.account_status == "active").distinct()
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    for user_id in db.scalars(stmt):
        notify_user(db, user_id, kind=kind, title=title, body=body, link=link)


def notify_committee_officers(db: Session, committee_id: int | None, *, kind: str,
                              title: str, body: str = "", link: str | None = None,
                              exclude_user_id: int | None = None) -> None:
    if committee_id is None:
        return
    stmt = select(User.id).join(UserRoleAssignment, UserRoleAssignment.user_id == User.id).join(
        Role, Role.id == UserRoleAssignment.role_id).where(
            Role.code.in_(["lc_director", "local_officer"]),
            UserRoleAssignment.scope_type == "local_committee",
            UserRoleAssignment.scope_id == committee_id,
            User.account_status == "active").distinct()
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    for user_id in db.scalars(stmt):
        notify_user(db, user_id, kind=kind, title=title, body=body, link=link)
