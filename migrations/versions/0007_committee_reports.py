"""scoped quarterly committee reports and review history"""
from alembic import op
import sqlalchemy as sa

revision = "0007_committee_reports"
down_revision = "0006_exchange_reviewer_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "committee_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("committee_id", sa.Integer(), sa.ForeignKey("local_committees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period", sa.String(12), nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("activities", sa.Text(), nullable=False, server_default=""),
        sa.Column("impact", sa.Text(), nullable=False, server_default=""),
        sa.Column("challenges", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_steps", sa.Text(), nullable=False, server_default=""),
        sa.Column("members_count", sa.Integer()),
        sa.Column("events_count", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("committee_id", "period", name="uq_committee_report_period"),
    )
    op.create_index("ix_committee_reports_status_period", "committee_reports", ["status", "period"])
    op.create_table(
        "committee_report_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("committee_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "committee_report_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("committee_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("committee_report_history")
    op.drop_table("committee_report_comments")
    op.drop_index("ix_committee_reports_status_period", table_name="committee_reports")
    op.drop_table("committee_reports")
