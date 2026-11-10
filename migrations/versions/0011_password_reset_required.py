"""require account owners to replace administrator-issued temporary passwords"""
from alembic import op
import sqlalchemy as sa

revision = "0011_password_reset_required"
down_revision = "0010_google_sync_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_reset_required", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "password_reset_required")
