"""log Google Sheets synchronization attempts"""
from alembic import op
import sqlalchemy as sa

revision = "0010_google_sync_runs"
down_revision = "0009_event_certificates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("committee_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="google_sheets"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("sheet_range", sa.String(length=240)),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("attempted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_google_sync_report_created", "google_sync_runs", ["report_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_google_sync_report_created", table_name="google_sync_runs")
    op.drop_table("google_sync_runs")
