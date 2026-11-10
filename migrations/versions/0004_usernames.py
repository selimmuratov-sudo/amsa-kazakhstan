"""username-based login with optional email"""
import re
from alembic import op
import sqlalchemy as sa

revision = "0004_usernames"
down_revision = "0003_news"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=40), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email FROM users ORDER BY id")).mappings().all()
    used = set()
    for row in rows:
        local = (row["email"] or "").split("@", 1)[0].lower()
        base = re.sub(r"[^a-z0-9_.-]+", "-", local).strip("-._")[:30] or f"member{row['id']}"
        if not base[0].isalnum():
            base = f"member-{base}"
        candidate = base
        suffix = 0
        while candidate in used:
            suffix += 1
            tail = f"-{row['id']}" if suffix == 1 else f"-{row['id']}-{suffix}"
            candidate = f"{base[:max(1, 40-len(tail))]}{tail}"
        used.add(candidate)
        bind.execute(sa.text("UPDATE users SET username=:username WHERE id=:id"), {"username": candidate, "id": row["id"]})
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", existing_type=sa.String(length=40), nullable=False)
        batch.alter_column("email", existing_type=sa.String(length=254), nullable=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("username")
        batch.alter_column("email", existing_type=sa.String(length=254), nullable=False)
