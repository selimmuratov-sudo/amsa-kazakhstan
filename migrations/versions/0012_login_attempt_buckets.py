"""store pseudonymous login throttling buckets"""
from alembic import op
import sqlalchemy as sa

revision = "0012_login_attempt_buckets"
down_revision = "0011_password_reset_required"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempt_buckets",
        sa.Column("bucket_hash", sa.String(length=64), primary_key=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_login_attempt_buckets_window", "login_attempt_buckets", ["window_started_at"])


def downgrade() -> None:
    op.drop_index("ix_login_attempt_buckets_window", table_name="login_attempt_buckets")
    op.drop_table("login_attempt_buckets")
