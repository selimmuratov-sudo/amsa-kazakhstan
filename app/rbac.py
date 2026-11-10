from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import User, UserRoleAssignment

NATIONAL_ROLES = {"super_admin", "national_board"}
MEMBER_ADMIN_ROLES = NATIONAL_ROLES | {"lc_director", "local_officer"}


def role_assignments(db: Session, user: User) -> list[UserRoleAssignment]:
    stmt = select(UserRoleAssignment).options(selectinload(UserRoleAssignment.role)).where(UserRoleAssignment.user_id == user.id)
    return list(db.scalars(stmt))


def role_codes(db: Session, user: User) -> list[str]:
    return sorted({assignment.role.code for assignment in role_assignments(db, user)})


def require_member_admin(db: Session, user: User) -> list[UserRoleAssignment]:
    assignments = role_assignments(db, user)
    if not any(item.role.code in MEMBER_ADMIN_ROLES for item in assignments):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    return assignments


def committee_scope(assignments: list[UserRoleAssignment]) -> list[int] | None:
    """Return a restricted LC scope, or None when a national role grants global scope."""
    if any(a.role.code in NATIONAL_ROLES for a in assignments):
        return None
    return sorted({a.scope_id for a in assignments if a.scope_type == "local_committee" and a.scope_id is not None})
