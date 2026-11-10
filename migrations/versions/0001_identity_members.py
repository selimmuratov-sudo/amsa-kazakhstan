"""Initial identity, scoped roles, and private member registry.

Revision ID: 0001_identity_members
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_identity_members"
down_revision = None
branch_labels = None
depends_on = None

ROLE_ROWS = [
    {"code": "super_admin", "name": "Super Admin", "description": "Full national platform control"},
    {"code": "national_board", "name": "National Board", "description": "National organization administration"},
    {"code": "national_officer", "name": "National Officer", "description": "National portfolio role; scoped access is granted per module"},
    {"code": "lc_director", "name": "Local Committee Director", "description": "Administration limited to assigned local committee"},
    {"code": "local_officer", "name": "Local Officer", "description": "Officer access limited to assigned local committee"},
    {"code": "member", "name": "Member", "description": "Private self-service member account"},
]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("account_status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table("roles", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("code", sa.String(40), nullable=False, unique=True), sa.Column("name", sa.String(100), nullable=False), sa.Column("description", sa.String(255), nullable=False, server_default=""))
    op.create_table("cities", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(120), nullable=False, unique=True), sa.Column("region", sa.String(120)))
    op.create_table("universities", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(200), nullable=False, unique=True), sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id", ondelete="SET NULL")))
    op.create_table(
        "local_committees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("university_id", sa.Integer(), sa.ForeignKey("universities.id", ondelete="SET NULL")),
        sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id", ondelete="SET NULL")),
        sa.Column("public_description", sa.Text()), sa.Column("public_email", sa.String(254)),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "user_role_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scope_type", sa.String(32), nullable=False, server_default="national"),
        sa.Column("scope_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "role_id", "scope_type", "scope_id", name="uq_user_role_scope"),
    )
    op.create_table(
        "member_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("full_name", sa.String(160), nullable=False), sa.Column("photo_object_key", sa.String(500)),
        sa.Column("university_name", sa.String(200), nullable=False, server_default=""), sa.Column("city_name", sa.String(120)),
        sa.Column("committee_interest", sa.String(200)),
        sa.Column("university_id", sa.Integer(), sa.ForeignKey("universities.id", ondelete="SET NULL")),
        sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id", ondelete="SET NULL")),
        sa.Column("study_year", sa.Integer()),
        sa.Column("committee_id", sa.Integer(), sa.ForeignKey("local_committees.id", ondelete="SET NULL")),
        sa.Column("phone", sa.String(40)), sa.Column("position_title", sa.String(120)), sa.Column("activity_area", sa.String(120)),
        sa.Column("joined_on", sa.Date()), sa.Column("membership_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("profile_visibility", sa.String(16), nullable=False, server_default="private"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("study_year IS NULL OR study_year BETWEEN 1 AND 12", name="ck_profile_study_year"),
    )
    op.create_index("ix_member_profiles_name", "member_profiles", ["full_name"])
    op.create_index("ix_member_profiles_committee", "member_profiles", ["committee_id"])
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(100), nullable=False), sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(100)), sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    roles = sa.table("roles", sa.column("code", sa.String), sa.column("name", sa.String), sa.column("description", sa.String))
    op.bulk_insert(roles, ROLE_ROWS)


def downgrade() -> None:
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("user_sessions")
    op.drop_index("ix_member_profiles_committee", table_name="member_profiles")
    op.drop_index("ix_member_profiles_name", table_name="member_profiles")
    op.drop_table("member_profiles")
    op.drop_table("user_role_assignments")
    op.drop_table("local_committees")
    op.drop_table("universities")
    op.drop_table("cities")
    op.drop_table("roles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
