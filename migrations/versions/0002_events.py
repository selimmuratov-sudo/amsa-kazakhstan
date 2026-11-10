"""events and registrations"""
from alembic import op
import sqlalchemy as sa

revision = "0002_events"
down_revision = "0001_identity_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(80), nullable=False, server_default="Событие AMSA"),
        sa.Column("location", sa.String(240), nullable=False, server_default="Онлайн"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("registration_deadline", sa.DateTime(timezone=True)),
        sa.Column("capacity", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("committee_id", sa.Integer(), sa.ForeignKey("local_committees.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_status_start", "events", ["status", "starts_at"])
    op.create_table("event_registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="confirmed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "user_id", name="uq_event_registration_user"),
    )


def downgrade() -> None:
    op.drop_table("event_registrations")
    op.drop_index("ix_events_status_start", table_name="events")
    op.drop_table("events")
