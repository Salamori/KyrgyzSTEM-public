import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.core.schemas import RequestModel
from app.modules.olympiad.models import (
    Band,
    DifficultyStatus,
    Language,
    Origin,
    PrerequisiteStrength,
    ProblemStatus,
    Scoring,
    SubmissionState,
    UnlockReason,
    Verdict,
)
from app.modules.users.models import Locale

MAX_SOURCE_BYTES = 64 * 1024


class PrerequisiteOut(BaseModel):
    stage: int
    requires: str
    requires_stage: int
    strength: PrerequisiteStrength


class TopicOut(BaseModel):
    slug: str
    name: str
    summary_md: str | None
    stage_count: int
    prerequisites: list[PrerequisiteOut]


class TagOut(BaseModel):
    slug: str
    name: str


class ProblemTopicOut(BaseModel):
    slug: str
    name: str
    stage: int
    is_primary: bool


MyStatus = Literal["solved", "attempted", "self_reported"]


class ProblemSummary(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    locale: Locale
    status: ProblemStatus
    difficulty_band: Band
    difficulty_status: DifficultyStatus
    topics: list[ProblemTopicOut]
    tags: list[TagOut]
    origin: Origin
    license: str
    my_status: MyStatus | None


class SampleOut(BaseModel):
    input: str
    output: str


class GroupOut(BaseModel):
    ordinal: int
    points: int
    description_md: str


class SourceOut(BaseModel):
    """The work a licensed or adapted problem comes from."""

    title: str
    author: str
    url: str
    license: str


class HintReviewSummary(BaseModel):
    """Admin-only: how many of one locale's hints have been reviewed."""

    locale: Locale
    total: int
    reviewed: int


class PublishBlocker(BaseModel):
    """Admin-only: one reason a problem cannot be published yet, with where it applies.

    `item` is the thing to fix (statement, editorial, hint, rights, provenance, ...);
    `locale` and `ordinal` are set when the reason concerns one language or one hint.
    """

    code: str
    item: str
    locale: Locale | None = None
    ordinal: int | None = None


class ReviewState(BaseModel):
    """Admin-only: what still blocks publication."""

    reviewed_locales: list[Locale]
    rights_verified_at: datetime | None
    # False for imported problems whose author is not linked to an account.
    author_linked: bool
    editorial_locales: list[Locale]
    reviewed_editorial_locales: list[Locale]
    hint_review: list[HintReviewSummary]
    publish_blockers: list[str]
    # Every blocking condition at once, per language/hint where that applies.
    blocking_items: list[PublishBlocker]


class ProblemDetail(ProblemSummary):
    is_fallback: bool
    available_locales: list[Locale]
    legend_md: str
    input_md: str
    output_md: str
    notes_md: str | None
    time_limit_ms: int
    memory_limit_mb: int
    scoring: Scoring
    groups: list[GroupOut]
    samples: list[SampleOut]
    difficulty_rationale: str
    attribution_md: str
    authors_md: str
    generated_with_ai: bool
    source: SourceOut | None
    hint_count: int
    revealed_hint_count: int
    editorial_available: bool
    editorial_unlocked: bool
    languages: list[Language]
    judge_available: bool
    review: ReviewState | None


class HintOut(BaseModel):
    ordinal: int
    locale: Locale
    body_md: str


class HintsOut(BaseModel):
    total: int
    revealed: list[HintOut]


class EditorialOut(BaseModel):
    locale: Locale
    is_fallback: bool
    body_md: str
    reviewed: bool
    unlock_reason: UnlockReason


class UnlockRequest(RequestModel):
    reason: Literal["gave_up"]


class SubmissionCreate(RequestModel):
    problem_slug: Annotated[str, StringConstraints(max_length=64)]
    language: Language
    source_code: Annotated[str, Field(min_length=1, max_length=MAX_SOURCE_BYTES)]


class GroupResultOut(BaseModel):
    ordinal: int
    points: int
    verdict: Verdict
    score: int


class SubmissionSummary(BaseModel):
    id: uuid.UUID
    problem_slug: str
    problem_title: str
    language: Language
    state: SubmissionState
    verdict: Verdict | None
    score: int | None
    hints_revealed_count: int
    created_at: datetime


class SubmissionDetail(SubmissionSummary):
    source_code: str
    group_results: list[GroupResultOut]


class SelfReportOut(BaseModel):
    problem_slug: str
    created_at: datetime


class StageEvidenceOut(BaseModel):
    stage: int
    unaided_by_band: dict[Band, int]
    with_hints: int
    demonstrated: bool


class TopicProgressOut(BaseModel):
    slug: str
    name: str
    stages: list[StageEvidenceOut]


class ProgressOut(BaseModel):
    judge_available: bool
    pending_not_judged: int
    topics: list[TopicProgressOut]
    hint_reliance_30d: float | None
    median_attempts_to_accept: float | None
    demonstrated_topics: int
    total_topics: int
    demonstrated_threshold: int
    self_reported: list[SelfReportOut]


class RightsVerification(RequestModel):
    # Required when the problem's author is not linked to an account (imported content):
    # the server cannot check identity then, so the reviewer states it and it is audited.
    attest_not_author: bool = False


class EditorialReview(RequestModel):
    # Same rule as RightsVerification: required when no author account is linked.
    attest_not_author: bool = False


class HintReview(RequestModel):
    # Same rule as EditorialReview: required when no author account is linked.
    attest_not_author: bool = False


class AdminHintOut(BaseModel):
    """Admin-only: one hint's text and review state, read without revealing it."""

    locale: Locale
    ordinal: int
    body_md: str
    body_sha256: str
    reviewed: bool
    reviewed_at: datetime | None


class AdminEditorialOut(BaseModel):
    """Admin-only: an editorial's text and review state, read without unlocking it."""

    locale: Locale
    body_md: str
    body_sha256: str
    reviewed: bool
    reviewed_at: datetime | None


class DifficultyUpdate(RequestModel):
    band: Band
    rationale: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)
    ]
