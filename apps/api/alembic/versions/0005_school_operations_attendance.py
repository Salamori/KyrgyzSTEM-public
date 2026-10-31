"""School/club operations: organizations, groups, sessions, attendance (docs/SCHOOL_OPERATIONS.md).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25 10:53:23.801073
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "school",
                "club",
                "other",
                name="org_kind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_organizations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_table(
        "org_groups",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "class",
                "club",
                name="group_kind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("lead_id", sa.Uuid(), nullable=True),
        sa.Column("join_code_hash", sa.String(length=64), nullable=True),
        sa.Column("join_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["users.id"], name=op.f("fk_org_groups_lead_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_org_groups_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_groups")),
        sa.UniqueConstraint("join_code_hash", name=op.f("uq_org_groups_join_code_hash")),
    )
    op.create_index(op.f("ix_org_groups_org_id"), "org_groups", ["org_id"], unique=False)
    op.create_table(
        "org_memberships",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "org_admin",
                "teacher",
                "student",
                name="org_role",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_org_memberships_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_org_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_memberships")),
        sa.UniqueConstraint("org_id", "user_id", name=op.f("uq_org_memberships_org_id")),
    )
    op.create_index(
        op.f("ix_org_memberships_user_id"), "org_memberships", ["user_id"], unique=False
    )
    op.create_table(
        "group_members",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["org_groups.id"],
            name=op.f("fk_group_members_group_id_org_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_group_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "user_id", name=op.f("pk_group_members")),
    )
    op.create_index(op.f("ix_group_members_user_id"), "group_members", ["user_id"], unique=False)
    op.create_table(
        "group_sessions",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_group_sessions_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["org_groups.id"],
            name=op.f("fk_group_sessions_group_id_org_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_sessions")),
    )
    op.create_index(
        "ix_group_sessions_group_starts", "group_sessions", ["group_id", "starts_at"], unique=False
    )
    op.create_table(
        "attendance_records",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "present",
                "absent",
                "excused",
                name="attendance_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("marked_by", sa.Uuid(), nullable=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["marked_by"],
            ["users.id"],
            name=op.f("fk_attendance_records_marked_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["group_sessions.id"],
            name=op.f("fk_attendance_records_session_id_group_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_attendance_records_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "user_id", name=op.f("pk_attendance_records")),
    )
    op.create_index(
        op.f("ix_attendance_records_user_id"), "attendance_records", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_attendance_records_user_id"), table_name="attendance_records")
    op.drop_table("attendance_records")
    op.drop_index("ix_group_sessions_group_starts", table_name="group_sessions")
    op.drop_table("group_sessions")
    op.drop_index(op.f("ix_group_members_user_id"), table_name="group_members")
    op.drop_table("group_members")
    op.drop_index(op.f("ix_org_memberships_user_id"), table_name="org_memberships")
    op.drop_table("org_memberships")
    op.drop_index(op.f("ix_org_groups_org_id"), table_name="org_groups")
    op.drop_table("org_groups")
    op.drop_table("organizations")
