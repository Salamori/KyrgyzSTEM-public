"""Olympiad domain (proposal 0001, Phase 2 subset).

Contests, problem sets and learning paths arrive in Phase 4 and will add
`submissions.problem_set_id`.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.core.db import Base, Timestamps, UUIDPrimaryKey, str_enum, utcnow
from app.modules.users.models import Locale


class Band(StrEnum):
    intro = "intro"
    easy = "easy"
    medium = "medium"
    hard = "hard"
    challenge = "challenge"


class DifficultyStatus(StrEnum):
    author_estimate = "author_estimate"
    reviewed = "reviewed"


class ProblemStatus(StrEnum):
    draft = "draft"
    in_review = "in_review"
    published = "published"
    archived = "archived"


class Origin(StrEnum):
    original = "original"
    licensed = "licensed"
    adapted = "adapted"


class Scoring(StrEnum):
    ioi = "ioi"
    binary = "binary"


class Language(StrEnum):
    cpp = "cpp"
    python = "python"


class PrerequisiteStrength(StrEnum):
    required = "required"
    helpful = "helpful"


class SubmissionContext(StrEnum):
    practice = "practice"
    contest = "contest"


class SubmissionState(StrEnum):
    queued = "queued"
    judging = "judging"
    done = "done"
    not_judged = "not_judged"
    judge_error = "judge_error"


class Verdict(StrEnum):
    accepted = "accepted"
    partial = "partial"
    wrong_answer = "wrong_answer"
    time_limit = "time_limit"
    memory_limit = "memory_limit"
    runtime_error = "runtime_error"
    compile_error = "compile_error"


class UnlockReason(StrEnum):
    solved = "solved"
    gave_up = "gave_up"


# ------------------------------------------------------------------ curriculum


class Topic(UUIDPrimaryKey, Base):
    __tablename__ = "topics"
    __table_args__ = (CheckConstraint("stage_count BETWEEN 1 AND 4", name="stage_count"),)

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id"))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    stage_count: Mapped[int] = mapped_column(Integer)

    translations: Mapped[list["TopicTranslation"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class TopicTranslation(Base):
    __tablename__ = "topic_translations"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[Locale] = mapped_column(str_enum(Locale, "locale"), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    summary_md: Mapped[str | None] = mapped_column(Text)


class TopicPrerequisite(UUIDPrimaryKey, Base):
    __tablename__ = "topic_prerequisites"
    __table_args__ = (
        UniqueConstraint("topic_id", "stage", "requires_topic_id", "requires_stage"),
        CheckConstraint("stage BETWEEN 1 AND 4 AND requires_stage BETWEEN 1 AND 4", name="stages"),
        CheckConstraint(
            "NOT (topic_id = requires_topic_id AND stage <= requires_stage)", name="no_self_loop"
        ),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"))
    stage: Mapped[int] = mapped_column(Integer)
    requires_topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    requires_stage: Mapped[int] = mapped_column(Integer)
    strength: Mapped[PrerequisiteStrength] = mapped_column(
        str_enum(PrerequisiteStrength, "strength")
    )


class Tag(UUIDPrimaryKey, Base):
    __tablename__ = "tags"

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    translations: Mapped[list["TagTranslation"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class TagTranslation(Base):
    __tablename__ = "tag_translations"

    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[Locale] = mapped_column(str_enum(Locale, "locale"), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


# ------------------------------------------------------------------ problems


class Problem(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "problems"
    __table_args__ = (
        CheckConstraint("time_limit_ms BETWEEN 100 AND 20000", name="time_limit"),
        CheckConstraint("memory_limit_mb BETWEEN 16 AND 2048", name="memory_limit"),
        CheckConstraint(
            "status <> 'published' OR published_at IS NOT NULL", name="published_has_timestamp"
        ),
        # Original problems have no source (SQL NULL); reused ones need a JSON object. A CHECK
        # passes when its expression is NULL, so both branches test IS [NOT] NULL explicitly.
        CheckConstraint(
            "(origin = 'original' AND source IS NULL) OR (origin <> 'original' "
            "AND source IS NOT NULL AND jsonb_typeof(source) = 'object')",
            name="source_shape",
        ),
    )

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[ProblemStatus] = mapped_column(
        str_enum(ProblemStatus, "problem_status"), default=ProblemStatus.draft, index=True
    )
    difficulty_band: Mapped[Band] = mapped_column(str_enum(Band, "band"))
    difficulty_status: Mapped[DifficultyStatus] = mapped_column(
        str_enum(DifficultyStatus, "difficulty_status"), default=DifficultyStatus.author_estimate
    )
    difficulty_rationale: Mapped[str] = mapped_column(Text)
    time_limit_ms: Mapped[int] = mapped_column(Integer)
    memory_limit_mb: Mapped[int] = mapped_column(Integer)
    scoring: Mapped[Scoring] = mapped_column(str_enum(Scoring, "scoring"))
    origin: Mapped[Origin] = mapped_column(str_enum(Origin, "origin"))
    license: Mapped[str] = mapped_column(String(32))  # SPDX identifier
    attribution_md: Mapped[str] = mapped_column(Text)
    authors_md: Mapped[str] = mapped_column(Text)
    generated_with_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    # {title, author, url, license} of the reused work; required unless origin is original.
    # none_as_null: Python None is stored as SQL NULL, not the JSON value null.
    source: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))
    # A human confirmed the right to publish under `license`. Cleared when provenance changes.
    rights_verified_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    rights_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    tests_hash: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    translations: Mapped[list["ProblemTranslation"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    topics: Mapped[list["ProblemTopic"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    tags: Mapped[list[Tag]] = relationship(secondary="problem_tags", lazy="selectin")


class ProblemTranslation(Base):
    __tablename__ = "problem_translations"

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[Locale] = mapped_column(str_enum(Locale, "locale"), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    legend_md: Mapped[str] = mapped_column(Text)
    input_md: Mapped[str] = mapped_column(Text)
    output_md: Mapped[str] = mapped_column(Text)
    notes_md: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProblemTopic(Base):
    __tablename__ = "problem_topics"
    __table_args__ = (
        CheckConstraint("stage BETWEEN 1 AND 4", name="stage"),
        # At most one primary topic per problem.
        Index(
            "uq_problem_topics_one_primary",
            "problem_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    stage: Mapped[int] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    topic: Mapped[Topic] = relationship(lazy="joined")


class ProblemTag(Base):
    __tablename__ = "problem_tags"

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class TestGroup(UUIDPrimaryKey, Base):
    """IOI-style subtask. Ordinal 0 holds the samples and is worth 0 points."""

    __tablename__ = "test_groups"
    __table_args__ = (
        UniqueConstraint("problem_id", "ordinal"),
        CheckConstraint("ordinal >= 0 AND points BETWEEN 0 AND 100", name="ordinal_points"),
    )
    __test__ = False  # not a pytest class

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    description_md: Mapped[str] = mapped_column(Text)


class TestCase(UUIDPrimaryKey, Base):
    """Non-sample cases never leave the API except towards the judge."""

    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("group_id", "ordinal"),
        CheckConstraint("ordinal >= 1", name="ordinal"),
    )
    __test__ = False

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_groups.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deferred: tests can be megabytes; load only when actually needed.
    input: Mapped[str] = deferred(mapped_column(Text))
    expected_output: Mapped[str] = deferred(mapped_column(Text))


class Hint(UUIDPrimaryKey, Base):
    __tablename__ = "hints"
    __table_args__ = (
        UniqueConstraint("problem_id", "locale", "ordinal"),
        CheckConstraint("ordinal >= 1", name="ordinal"),
    )

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)  # 1 = gentlest nudge
    locale: Mapped[Locale] = mapped_column(str_enum(Locale, "locale"))
    body_md: Mapped[str] = mapped_column(Text)
    # Set only by an admin review; cleared by a re-import that changes this locale's hints.
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Editorial(Base):
    __tablename__ = "editorials"

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[Locale] = mapped_column(str_enum(Locale, "locale"), primary_key=True)
    body_md: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferenceSolution(UUIDPrimaryKey, Base):
    __tablename__ = "reference_solutions"
    __table_args__ = (UniqueConstraint("problem_id", "language"),)

    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[Language] = mapped_column(str_enum(Language, "language"))
    source: Mapped[str] = deferred(mapped_column(Text))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    # Verified = agreed with an independent brute force and passed every current test.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_tests_hash: Mapped[str | None] = mapped_column(String(64))


# ------------------------------------------------------------------ attempts


class Submission(UUIDPrimaryKey, Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_user_problem_created", "user_id", "problem_id", "created_at"),
        CheckConstraint("score IS NULL OR (score BETWEEN 0 AND 100)", name="score_range"),
        CheckConstraint("hints_revealed_count >= 0", name="hints_revealed_count"),
        # A verdict exists only once judging has finished.
        CheckConstraint("verdict IS NULL OR state = 'done'", name="verdict_requires_done"),
        CheckConstraint(
            "tests_hash IS NULL OR tests_hash ~ '^[0-9a-f]{64}$'", name="tests_hash_format"
        ),
        CheckConstraint("judge_attempts >= 0", name="judge_attempts"),
        # A judging lease exists only while a worker holds the submission.
        CheckConstraint(
            "lease_expires_at IS NULL OR state = 'judging'", name="lease_only_while_judging"
        ),
        CheckConstraint(
            "compile_message IS NULL OR (verdict = 'compile_error' "
            "AND char_length(compile_message) <= 4096)",
            name="compile_message",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    context: Mapped[SubmissionContext] = mapped_column(
        str_enum(SubmissionContext, "submission_context"), default=SubmissionContext.practice
    )
    language: Mapped[Language] = mapped_column(str_enum(Language, "language"))
    source_code: Mapped[str] = deferred(mapped_column(Text))
    state: Mapped[SubmissionState] = mapped_column(str_enum(SubmissionState, "submission_state"))
    verdict: Mapped[Verdict | None] = mapped_column(str_enum(Verdict, "verdict"))
    score: Mapped[int | None] = mapped_column(Integer)
    hints_revealed_count: Mapped[int] = mapped_column(Integer, default=0)
    judge_token: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Judging metadata (migration 0009). `tests_hash` is the v2 judging snapshot hash
    # (judge/snapshot.py) the submission is bound to, not `problems.tests_hash` (v1);
    # NULL when unknown or unbindable (never backfilled; such a submission is not judged).
    tests_hash: Mapped[str | None] = mapped_column(String(64))
    judge_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    compile_message: Mapped[str | None] = mapped_column(Text)

    problem: Mapped[Problem] = relationship(lazy="joined")
    group_results: Mapped[list["SubmissionGroupResult"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class SubmissionGroupResult(Base):
    """One subtask's result. The group's ordinal and points are kept here, so the result
    stays meaningful after a re-import replaces the test groups (`group_id` becomes NULL)."""

    __tablename__ = "submission_group_results"
    __table_args__ = (
        CheckConstraint("score >= 0", name="score"),
        CheckConstraint("score <= points", name="score_within_points"),
        CheckConstraint(
            "group_ordinal >= 0 AND points BETWEEN 0 AND 100", name="group_ordinal_points"
        ),
    )

    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), primary_key=True
    )
    group_ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    points: Mapped[int] = mapped_column(Integer)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_groups.id", ondelete="SET NULL"), index=True
    )
    verdict: Mapped[Verdict] = mapped_column(str_enum(Verdict, "verdict"))
    score: Mapped[int] = mapped_column(Integer)
    max_time_ms: Mapped[int | None] = mapped_column(Integer)
    max_memory_kb: Mapped[int | None] = mapped_column(Integer)


class HintReveal(Base):
    """Keyed by hint ordinal (not hint row): revealing hint 2 in one locale reveals it in all."""

    __tablename__ = "hint_reveals"
    __table_args__ = (CheckConstraint("ordinal >= 1", name="ordinal"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    revealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EditorialUnlock(Base):
    __tablename__ = "editorial_unlocks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    reason: Mapped[UnlockReason] = mapped_column(str_enum(UnlockReason, "unlock_reason"))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SelfReport(Base):
    """Unverified 'solved offline' claim. Shown only to the student; never used as evidence."""

    __tablename__ = "self_reports"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True
    )
    claim: Mapped[str] = mapped_column(String(32), default="solved_offline")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
