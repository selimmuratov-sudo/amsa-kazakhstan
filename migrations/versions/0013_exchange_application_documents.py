"""store private exchange application documents"""
from alembic import op
import sqlalchemy as sa


revision = "0013_exchange_application_documents"
down_revision = "0012_login_attempt_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exchange_application_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("exchange_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("storage_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(length=180), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_exchange_documents_application_created", "exchange_application_documents", ["application_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_exchange_documents_application_created", table_name="exchange_application_documents")
    op.drop_table("exchange_application_documents")
