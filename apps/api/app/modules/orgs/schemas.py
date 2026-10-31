import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints

from app.core.schemas import RequestModel
from app.modules.orgs.models import AttendanceStatus, GroupKind, OrgKind, OrgRole

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class OrgCreate(RequestModel):
    name: Name
    kind: OrgKind


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: OrgKind
    is_synthetic: bool
    my_role: OrgRole | None


class OrgAdminView(BaseModel):
    """What a platform admin sees: structure and counts, never rosters or attendance."""

    id: uuid.UUID
    name: str
    kind: OrgKind
    is_synthetic: bool
    admins: int
    teachers: int
    students: int
    groups: int


class StaffAdd(RequestModel):
    email: EmailStr
    role: Literal["teacher", "org_admin"]


class AdminAssign(RequestModel):
    email: EmailStr


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    role: OrgRole


class GroupCreate(RequestModel):
    name: Name
    kind: GroupKind


class GroupUpdate(RequestModel):
    name: Name


class OrgGroupOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    kind: GroupKind
    lead_name: str | None
    member_count: int
    can_manage: bool
    join_code_expires_at: datetime | None


class JoinCodeOut(BaseModel):
    code: str
    expires_at: datetime


class JoinRequest(RequestModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=16)]


class JoinedOut(BaseModel):
    org_id: uuid.UUID
    group_id: uuid.UUID
    group_name: str


class GroupMemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    joined_at: datetime


class SessionCreate(RequestModel):
    starts_at: datetime
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    title: str | None
    # Counts only for staff who manage the group; students get their own status only.
    present: int | None
    absent: int | None
    excused: int | None
    unmarked: int | None
    my_status: AttendanceStatus | None


class AttendanceMark(BaseModel):
    user_id: uuid.UUID
    status: AttendanceStatus


class AttendanceUpdate(RequestModel):
    records: Annotated[list[AttendanceMark], Field(min_length=1, max_length=500)]


class AttendanceRow(BaseModel):
    user_id: uuid.UUID
    display_name: str
    status: AttendanceStatus | None


class GroupSummary(BaseModel):
    """Aggregates only: no individual rows."""

    sessions: int
    members: int
    marked: int
    present: int
    attendance_rate: float | None


class MyAttendanceRow(BaseModel):
    org_name: str
    group_name: str
    starts_at: datetime
    title: str | None
    status: AttendanceStatus
