import argparse
import re
import secrets

from sqlalchemy import select

from app.database import SessionLocal
from app.models import MemberProfile, Role, User, UserRoleAssignment
from app.security import hash_password


def create_super_admin(username: str, full_name: str) -> str:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", username):
        raise SystemExit("Username must use 3–40 letters, numbers, dots, hyphens or underscores.")
    password = secrets.token_urlsafe(24)
    with SessionLocal.begin() as db:
        if db.scalar(select(User).where(User.username == username)):
            raise SystemExit("An account with this username already exists.")
        role = db.scalar(select(Role).where(Role.code == "super_admin"))
        if not role:
            raise SystemExit("Role data is missing. Run `.venv/bin/alembic upgrade head` first.")
        user = User(username=username, email=None, password_hash=hash_password(password), account_status="active",
                    password_reset_required=True)
        user.profile = MemberProfile(full_name=full_name.strip(), university_name="AMSA Kazakhstan", membership_status="active", profile_visibility="private")
        db.add(user)
        db.flush()
        db.add(UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="national"))
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="AMSA Kazakhstan platform administration")
    sub = parser.add_subparsers(dest="command", required=True)
    create_admin = sub.add_parser("create-super-admin")
    create_admin.add_argument("--username", default="amsa-admin")
    create_admin.add_argument("--name", default="AMSA Administrator")
    args = parser.parse_args()
    if args.command == "create-super-admin":
        password = create_super_admin(args.username, args.name)
        print(f"Super Admin created. Username: {args.username.strip().lower()}\nTemporary password (shown once; must be changed at first login): {password}")


if __name__ == "__main__":
    main()
