"""School and club operations: organizations, groups, sessions, attendance.

Every row hangs off an organization; all access goes through `orgs.permissions`.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey, str_enum, utcnow


class OrgKind(StrEnum):
    school = "school"
    club = "club"
    other = "other"


class OrgRole(StrEnum):
    org_admin = "org_admin"
    teacher = "teacher"
    student = "student"


class GroupKind(StrEnum):
    class_ = "class"
    club = "club"


class AttendanceStatus(StrEnum):
    present = "present"
    absent = "absent"
    excused = "excused"


class Organization(UUIDPrimaryKey, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[OrgKind] = mapped_column(str_enum(OrgKind, "org_kind"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrgMembership(UUIDPrimaryKey, Base):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[OrgRole] = mapped_column(str_enum(OrgRole, "org_role"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(UUIDPrimaryKey, Base):
    """A class or a club inside an organization, led by one staff member."""

    __tablename__ = "org_groups"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[GroupKind] = mapped_column(str_enum(GroupKind, "group_kind"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # SHA-256 of the current join code; the code itself is shown once.
    join_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    join_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org_groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupSession(UUIDPrimaryKey, Base):
    __tablename__ = "group_sessions"
    __table_args__ = (Index("ix_group_sessions_group_starts", "group_id", "starts_at"),)

    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_groups.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("group_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    status: Mapped[AttendanceStatus] = mapped_column(
        str_enum(AttendanceStatus, "attendance_status")
    )
    marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
