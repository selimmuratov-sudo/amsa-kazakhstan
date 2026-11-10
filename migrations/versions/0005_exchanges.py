"""exchange calls and private application review workflow"""
from alembic import op
import sqlalchemy as sa

revision = "0005_exchanges"
down_revision = "0004_usernames"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exchange_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("program_type", sa.String(80), nullable=False, server_default="AMSEP"),
        sa.Column("destination", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(600), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("requirements", sa.Text(), nullable=False, server_default=""),
        sa.Column("opens_at", sa.DateTime(timezone=True)),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seats", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_exchange_calls_status_close", "exchange_calls", ["status", "closes_at"])
    op.create_table(
        "exchange_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("call_id", sa.Integer(), sa.ForeignKey("exchange_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="submitted"),
        sa.Column("motivation", sa.Text(), nullable=False),
        sa.Column("relevant_experience", sa.Text(), nullable=False, server_default=""),
        sa.Column("preferred_destination", sa.String(200)),
        sa.Column("reviewer_note", sa.Text()),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("call_id", "user_id", name="uq_exchange_application_candidate"),
    )
    op.create_index("ix_exchange_application_status", "exchange_applications", ["call_id", "status", "submitted_at"])
    op.create_table(
        "exchange_application_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("exchange_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("exchange_application_history")
    op.drop_index("ix_exchange_application_status", table_name="exchange_applications")
    op.drop_table("exchange_applications")
    op.drop_index("ix_exchange_calls_status_close", table_name="exchange_calls")
    op.drop_table("exchange_calls")
