"""event certificate issuance and revocation ledger"""
from alembic import op
import sqlalchemy as sa

revision = "0009_event_certificates"
down_revision = "0008_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_certificates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("public_code", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="issued"),
        sa.Column("issued_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.String(500)),
        sa.UniqueConstraint("event_id", "user_id", name="uq_event_certificate_recipient"),
    )


def downgrade() -> None:
    op.drop_table("event_certificates")
