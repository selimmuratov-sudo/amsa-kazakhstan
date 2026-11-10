"""dedicated national exchange-review role"""
from alembic import op
import sqlalchemy as sa

revision = "0006_exchange_reviewer_role"
down_revision = "0005_exchanges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles = sa.table("roles", sa.column("code", sa.String), sa.column("name", sa.String), sa.column("description", sa.String))
    op.bulk_insert(roles, [{"code": "exchange_reviewer", "name": "Exchange Reviewer", "description": "National reviewer access to exchange calls and applications"}])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM user_role_assignments WHERE role_id IN (SELECT id FROM roles WHERE code='exchange_reviewer')"))
    op.execute(sa.text("DELETE FROM roles WHERE code='exchange_reviewer'"))
