import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints

from app.core.schemas import RequestModel, ResponseModel
from app.modules.users.models import Locale, Role

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class UserPrivate(ResponseModel):
    """What a user may see about themself."""

    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    preferred_locale: Locale
    created_at: datetime


class UserAdminView(UserPrivate):
    is_active: bool
    is_synthetic: bool


class UpdateMe(RequestModel):
    display_name: DisplayName | None = None
    preferred_locale: Locale | None = None


class AdminUpdateUser(RequestModel):
    role: Role | None = None
    is_active: bool | None = None


class PasswordResetIssued(ResponseModel):
    """Shown once to the admin who issued it; only a hash is stored."""

    token: str
    expires_at: datetime


class DeleteAccountRequest(RequestModel):
    password: Annotated[str, Field(min_length=1, max_length=128)]


class ExportSession(ResponseModel):
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    user_agent: str | None


class ExportSubmission(ResponseModel):
    id: uuid.UUID
    problem_slug: str
    language: str
    source_code: str
    state: str
    verdict: str | None
    score: int | None
    created_at: datetime


class ExportHintReveal(ResponseModel):
    problem_slug: str
    ordinal: int
    revealed_at: datetime


class ExportEditorialUnlock(ResponseModel):
    problem_slug: str
    reason: str
    unlocked_at: datetime


class ExportSelfReport(ResponseModel):
    problem_slug: str
    claim: str
    created_at: datetime


class ExportAiRequest(ResponseModel):
    problem_slug: str
    level: int
    outcome: str
    created_at: datetime


class ExportMembership(ResponseModel):
    organization: str
    role: str
    created_at: datetime


class ExportGroup(ResponseModel):
    organization: str
    group: str
    joined_at: datetime


class ExportAttendance(ResponseModel):
    group: str
    starts_at: datetime
    title: str | None
    status: str


class AccountExport(ResponseModel):
    """Everything stored about the signed-in user, never hashes or other people's data."""

    exported_at: datetime
    profile: UserPrivate
    terms_version: str | None
    terms_accepted_at: datetime | None
    sessions: list[ExportSession]
    submissions: list[ExportSubmission]
    hint_reveals: list[ExportHintReveal]
    editorial_unlocks: list[ExportEditorialUnlock]
    self_reports: list[ExportSelfReport]
    ai_requests: list[ExportAiRequest]
    memberships: list[ExportMembership]
    groups: list[ExportGroup]
    attendance: list[ExportAttendance]
