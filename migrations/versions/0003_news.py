"""news CMS"""
from alembic import op
import sqlalchemy as sa

revision = "0003_news"
down_revision = "0002_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("news_articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(220), nullable=False, unique=True),
        sa.Column("title", sa.String(220), nullable=False),
        sa.Column("summary", sa.String(600), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False, server_default="Новости"),
        sa.Column("cover_image", sa.String(500)),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("committee_id", sa.Integer(), sa.ForeignKey("local_committees.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_news_status_publish", "news_articles", ["status", "published_at"])


def downgrade() -> None:
    op.drop_index("ix_news_status_publish", table_name="news_articles")
    op.drop_table("news_articles")
