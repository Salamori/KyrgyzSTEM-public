import hashlib
import json
import logging
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Select, case, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.db import Base, utcnow
from app.core.errors import ApiError
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.judge import snapshot as judge_snapshot
from app.modules.olympiad import content, help, metrics
from app.modules.olympiad.models import (
    Band,
    DifficultyStatus,
    Editorial,
    EditorialUnlock,
    Hint,
    HintReveal,
    Language,
    Problem,
    ProblemStatus,
    ProblemTopic,
    ProblemTranslation,
    ReferenceSolution,
    Scoring,
    SelfReport,
    Submission,
    SubmissionState,
    Tag,
    TestCase,
    TestGroup,
    Topic,
    TopicPrerequisite,
    UnlockReason,
    Verdict,
)
from app.modules.olympiad.schemas import (
    MAX_SOURCE_BYTES,
    AdminEditorialOut,
    AdminHintOut,
    DifficultyUpdate,
    EditorialOut,
    EditorialReview,
    GroupOut,
    GroupResultOut,
    HintOut,
    HintReview,
    HintReviewSummary,
    HintsOut,
    MyStatus,
    PrerequisiteOut,
    ProblemDetail,
    ProblemSummary,
    ProblemTopicOut,
    ProgressOut,
    PublishBlocker,
    ReviewState,
    RightsVerification,
    SampleOut,
    SelfReportOut,
    SourceOut,
    StageEvidenceOut,
    SubmissionCreate,
    SubmissionDetail,
    SubmissionSummary,
    TagOut,
    TopicOut,
    TopicProgressOut,
)
from app.modules.users.models import Locale, Role, User

FALLBACK_ORDER = (Locale.ru, Locale.en, Locale.ky)
ProblemSort = Literal["band", "newest"]
BAND_ORDER = {band: i for i, band in enumerate(Band)}


class Translated(Protocol):
    @property
    def locale(self) -> Locale: ...


def pick[T: Translated](items: Sequence[T], locale: Locale) -> tuple[T, bool]:
    """Requested locale, else ru → en → ky. Returns (item, is_fallback)."""
    by_locale = {i.locale: i for i in items}
    if locale in by_locale:
        return by_locale[locale], False
    for loc in FALLBACK_ORDER:
        if loc in by_locale:
            return by_locale[loc], True
    raise ApiError(404, "not_found", "No translation available.")


def requested_locale(locale: Locale | None, user: User | None) -> Locale:
    if locale is not None:
        return locale
    return user.preferred_locale if user is not None else Locale.ru


def _is_admin(user: User | None) -> bool:
    return user is not None and user.role == Role.admin


def _visible(stmt: Select[Problem], user: User | None) -> Select[Problem]:
    if _is_admin(user):
        return stmt
    if user is None:
        return stmt.where(Problem.status == ProblemStatus.published)
    return stmt.where(or_(Problem.status == ProblemStatus.published, Problem.created_by == user.id))


def get_problem(db: Session, slug: str, user: User | None) -> Problem:
    problem = db.scalar(_visible(select(Problem).where(Problem.slug == slug), user))
    if problem is None:
        # Same answer for "missing" and "not visible": drafts do not leak.
        raise ApiError(404, "not_found", "Problem not found.")
    return problem


# ------------------------------------------------------------------ catalogue


def list_topics(db: Session, locale: Locale) -> list[TopicOut]:
    topics = list(db.scalars(select(Topic).order_by(Topic.ordinal)))
    slugs = {t.id: t.slug for t in topics}
    prereqs: dict[uuid.UUID, list[PrerequisiteOut]] = {t.id: [] for t in topics}
    for p in db.scalars(select(TopicPrerequisite)):
        prereqs[p.topic_id].append(
            PrerequisiteOut(
                stage=p.stage,
                requires=slugs[p.requires_topic_id],
                requires_stage=p.requires_stage,
                strength=p.strength,
            )
        )
    out = []
    for t in topics:
        tr, _ = pick(t.translations, locale)
        out.append(
            TopicOut(
                slug=t.slug,
                name=tr.name,
                summary_md=tr.summary_md,
                stage_count=t.stage_count,
                prerequisites=sorted(prereqs[t.id], key=lambda p: (p.stage, p.requires)),
            )
        )
    return out


def list_tags(db: Session, locale: Locale) -> list[TagOut]:
    return [_tag_out(t, locale) for t in db.scalars(select(Tag).order_by(Tag.slug))]


def _tag_out(tag: Tag, locale: Locale) -> TagOut:
    return TagOut(slug=tag.slug, name=pick(tag.translations, locale)[0].name)


def _topic_out(pt: ProblemTopic, locale: Locale) -> ProblemTopicOut:
    return ProblemTopicOut(
        slug=pt.topic.slug,
        name=pick(pt.topic.translations, locale)[0].name,
        stage=pt.stage,
        is_primary=pt.is_primary,
    )


def _my_statuses(
    db: Session, user: User | None, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, MyStatus]:
    ids = list(ids)
    if user is None or not ids:
        return {}
    result: dict[uuid.UUID, MyStatus] = {}
    for pid in db.scalars(
        select(SelfReport.problem_id).where(
            SelfReport.user_id == user.id, SelfReport.problem_id.in_(ids)
        )
    ):
        result[pid] = "self_reported"
    rows = db.execute(
        select(Submission.problem_id, func.bool_or(Submission.verdict == Verdict.accepted))
        .where(Submission.user_id == user.id, Submission.problem_id.in_(ids))
        .group_by(Submission.problem_id)
    )
    for pid, solved in rows:
        if solved:
            result[pid] = "solved"
        elif pid not in result:
            result[pid] = "attempted"
    return result


def _summary(problem: Problem, locale: Locale, status: MyStatus | None) -> ProblemSummary:
    tr, _ = pick(problem.translations, locale)
    return ProblemSummary(
        id=problem.id,
        slug=problem.slug,
        title=tr.title,
        locale=tr.locale,
        status=problem.status,
        difficulty_band=problem.difficulty_band,
        difficulty_status=problem.difficulty_status,
        topics=[
            _topic_out(t, locale) for t in sorted(problem.topics, key=lambda t: not t.is_primary)
        ],
        tags=[_tag_out(t, locale) for t in problem.tags],
        origin=problem.origin,
        license=problem.license,
        my_status=status,
    )


def list_problems(
    db: Session,
    user: User | None,
    locale: Locale,
    *,
    q: str | None,
    topic: str | None,
    tag: str | None,
    band: Band | None,
    status: ProblemStatus | None,
    sort: ProblemSort = "band",
    limit: int,
    offset: int,
) -> tuple[list[ProblemSummary], int]:
    stmt = _visible(select(Problem), user)
    if status is not None and _is_admin(user):
        stmt = stmt.where(Problem.status == status)
    if band is not None:
        stmt = stmt.where(Problem.difficulty_band == band)
    if topic:
        stmt = stmt.where(
            exists().where(
                ProblemTopic.problem_id == Problem.id,
                ProblemTopic.topic_id == Topic.id,
                Topic.slug == topic,
            )
        )
    if tag:
        stmt = stmt.where(Problem.tags.any(Tag.slug == tag))
    if q and q.strip():
        pattern = (
            "%"
            + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        stmt = stmt.where(
            or_(
                Problem.slug.ilike(pattern, escape="\\"),
                exists().where(
                    ProblemTranslation.problem_id == Problem.id,
                    func.lower(ProblemTranslation.title).like(pattern, escape="\\"),
                ),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = (
        (Problem.created_at.desc(), Problem.slug)
        if sort == "newest"
        else (case(BAND_ORDER, value=Problem.difficulty_band), Problem.slug)
    )
    rows = list(db.scalars(stmt.order_by(*order).limit(limit).offset(offset)))
    statuses = _my_statuses(db, user, (p.id for p in rows))
    return [_summary(p, locale, statuses.get(p.id)) for p in rows], total


# ------------------------------------------------------------------ problem page


# Review audit actions whose events are the evidence behind each review record.
_REVIEW_ACTIONS = (
    "problem.translation_reviewed",
    "problem.editorial_reviewed",
    "problem.hint_reviewed",
    "problem.rights_verified",
    "problem.difficulty_changed",
)
_ReviewKey = tuple[str, str | None, int | None]


def _latest_review_events(db: Session, problem: Problem) -> dict[_ReviewKey, AuditLog]:
    """The latest review audit event per item: (action, locale, ordinal)."""
    rows = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.target_type == "problem",
            AuditLog.target_id == str(problem.id),
            AuditLog.action.in_(_REVIEW_ACTIONS),
        )
        .order_by(AuditLog.created_at)
    )
    latest: dict[_ReviewKey, AuditLog] = {}
    for row in rows:
        details = row.details or {}
        latest[(row.action, details.get("locale"), details.get("ordinal"))] = row
    return latest


class _Reviews:
    """Decides whether a stored review counts towards publication.

    A review counts only if it is recorded (reviewer and time), was not made by the linked
    author, and is backed by the latest audited review of that item by the same reviewer;
    for editorials and hints the audited SHA-256 must also match the current text. A review
    record without matching audit evidence (e.g. written directly to the database) does not
    count.
    """

    def __init__(self, db: Session, problem: Problem) -> None:
        self.author = problem.created_by
        self.events = _latest_review_events(db, problem)

    def by_author(self, reviewed_by: uuid.UUID | None) -> bool:
        return self.author is not None and reviewed_by == self.author

    def valid(
        self,
        action: str,
        locale: Locale,
        ordinal: int | None,
        reviewed_by: uuid.UUID | None,
        reviewed_at: datetime | None,
        body_md: str | None = None,
    ) -> bool:
        if reviewed_by is None or reviewed_at is None or self.by_author(reviewed_by):
            return False
        event = self.events.get((action, locale.value, ordinal))
        if event is None or event.actor_id != reviewed_by:
            return False
        return body_md is None or (event.details or {}).get("body_sha256") == body_sha256(body_md)

    def statement_valid(self, t: ProblemTranslation) -> bool:
        """A statement review counts only for the exact text that was reviewed."""
        if not self.valid(
            "problem.translation_reviewed", t.locale, None, t.reviewed_by, t.reviewed_at
        ):
            return False
        event = self.events[("problem.translation_reviewed", t.locale.value, None)]
        return (event.details or {}).get("statement_sha256") == statement_sha256(t)

    def rights_valid(self, problem: Problem) -> bool:
        """The stored confirmation is the latest audited one, by the same person, at the same
        moment, for the provenance the problem has now."""
        event = self.events.get(("problem.rights_verified", None, None))
        if event is None or event.actor_id != problem.rights_verified_by:
            return False
        details = event.details or {}
        try:
            verified_at = datetime.fromisoformat(str(details.get("verified_at")))
        except ValueError:
            return False
        return verified_at == problem.rights_verified_at and details.get(
            "provenance_sha256"
        ) == provenance_sha256(problem)

    def difficulty_reviewer(self, problem: Problem) -> uuid.UUID | None:
        """Who confirmed the current band (from the latest audited difficulty review), or None
        if the difficulty is not reviewed or the latest review is for another band."""
        if problem.difficulty_status != DifficultyStatus.reviewed:
            return None
        event = self.events.get(("problem.difficulty_changed", None, None))
        if event is None:
            return None
        details = event.details or {}
        if details.get("to") != problem.difficulty_band.value:
            return None
        if details.get("spec_sha256") != difficulty_spec_sha256(problem):
            return None  # limits, scoring, topics or tags changed since the review
        return event.actor_id


def publication_blockers(db: Session, problem: Problem, actor: User | None) -> list[PublishBlocker]:
    """The publication gate: every reason the problem cannot be published, all at once.

    `publish()` refuses while this list is non-empty, so every publishing path goes through
    it. It only reads: evaluating it never changes the problem, its reviews or its status.
    """
    reviews = _Reviews(db, problem)
    items: list[PublishBlocker] = []

    def block(
        code: str, item: str, locale: Locale | None = None, ordinal: int | None = None
    ) -> None:
        items.append(PublishBlocker(code=code, item=item, locale=locale, ordinal=ordinal))

    if problem.status == ProblemStatus.published:
        block("already_published", "problem")

    # Provenance and rights: declared fields, the shared source rules, a human confirmation.
    if not (problem.license and problem.attribution_md.strip() and problem.authors_md.strip()):
        block("missing_license_or_attribution", "provenance")
    try:
        if problem.license not in content.ALLOWED_LICENSES:
            raise content.ContentError("license not in allow-list")
        content.check_source(problem.slug, problem.origin.value, problem.license, problem.source)
    except content.ContentError:
        block("provenance_incomplete", "provenance")
    if problem.rights_verified_at is None or problem.rights_verified_by is None:
        block("rights_not_verified", "rights")
    elif reviews.by_author(problem.rights_verified_by):
        block("author_review_conflict", "rights")
    elif not reviews.rights_valid(problem):
        block("rights_not_verified", "rights")

    # Every statement language that exists must be reviewed.
    translations = sorted(problem.translations, key=lambda t: list(Locale).index(t.locale))
    if not translations:
        block("statement_unreviewed", "statement")
    for t in translations:
        if reviews.by_author(t.reviewed_by):
            block("author_review_conflict", "statement", t.locale)
        elif not reviews.statement_valid(t):
            block("statement_unreviewed", "statement", t.locale)

    # Every editorial that exists, and every hint that exists, must be reviewed.
    for e in _editorials(db, problem):
        if reviews.by_author(e.reviewed_by):
            block("author_review_conflict", "editorial", e.locale)
        elif not reviews.valid(
            "problem.editorial_reviewed", e.locale, None, e.reviewed_by, e.reviewed_at, e.body_md
        ):
            block("editorial_unreviewed", "editorial", e.locale)
    for rows in _hints_by_locale(db, problem).values():
        for h in rows:
            if reviews.by_author(h.reviewed_by):
                block("author_review_conflict", "hint", h.locale, h.ordinal)
            elif not reviews.valid(
                "problem.hint_reviewed",
                h.locale,
                h.ordinal,
                h.reviewed_by,
                h.reviewed_at,
                h.body_md,
            ):
                block("hint_unreviewed", "hint", h.locale, h.ordinal)

    verified = db.scalar(
        select(func.count()).where(
            ReferenceSolution.problem_id == problem.id,
            ReferenceSolution.verified_at.is_not(None),
            ReferenceSolution.verified_tests_hash == problem.tests_hash,
        )
    )
    if not verified:
        block("no_verified_reference_solution", "solution")
    difficulty_reviewer = reviews.difficulty_reviewer(problem)
    if difficulty_reviewer is None:
        block("difficulty_not_reviewed", "difficulty")
    elif reviews.by_author(difficulty_reviewer):
        block("author_review_conflict", "difficulty")
    samples = db.scalar(
        select(func.count()).where(TestCase.problem_id == problem.id, TestCase.is_sample)
    )
    if not samples:
        block("no_samples", "tests")
    if actor is not None and problem.created_by == actor.id:
        block("approver_is_author", "publisher")
    return items


def publish_blockers(db: Session, problem: Problem, actor: User | None) -> list[str]:
    """The distinct blocker codes, in order (the `details` of `409 publish_blocked`)."""
    return list(dict.fromkeys(b.code for b in publication_blockers(db, problem, actor)))


def _revealed_count(db: Session, user: User | None, problem: Problem) -> int:
    if user is None:
        return 0
    return (
        db.scalar(
            select(func.count()).where(
                HintReveal.user_id == user.id, HintReveal.problem_id == problem.id
            )
        )
        or 0
    )


def _hint_total(db: Session, problem: Problem) -> int:
    return (
        db.scalar(
            select(func.count(func.distinct(Hint.ordinal))).where(Hint.problem_id == problem.id)
        )
        or 0
    )


def _has_accepted(db: Session, user: User, problem: Problem) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    Submission.user_id == user.id,
                    Submission.problem_id == problem.id,
                    Submission.verdict == Verdict.accepted,
                )
            )
        )
    )


def _unlock(db: Session, user: User | None, problem: Problem) -> EditorialUnlock | None:
    if user is None:
        return None
    return db.get(EditorialUnlock, (user.id, problem.id))


def editorial_unlocked(db: Session, user: User, problem: Problem) -> bool:
    """True after an accepted submission or a recorded give-up (the editorial rule)."""
    return _unlock(db, user, problem) is not None or _has_accepted(db, user, problem)


def _review_state(db: Session, problem: Problem, user: User | None) -> ReviewState:
    # "Reviewed" here uses the publication gate's validity rule, so the admin view never
    # shows as reviewed something the gate would reject.
    reviews = _Reviews(db, problem)
    editorials = _editorials(db, problem)
    blockers = publication_blockers(db, problem, user)
    return ReviewState(
        reviewed_locales=[t.locale for t in problem.translations if reviews.statement_valid(t)],
        rights_verified_at=problem.rights_verified_at,
        author_linked=problem.created_by is not None,
        editorial_locales=[e.locale for e in editorials],
        reviewed_editorial_locales=[
            e.locale
            for e in editorials
            if reviews.valid(
                "problem.editorial_reviewed",
                e.locale,
                None,
                e.reviewed_by,
                e.reviewed_at,
                e.body_md,
            )
        ],
        hint_review=[
            HintReviewSummary(
                locale=locale,
                total=len(rows),
                reviewed=sum(
                    1
                    for h in rows
                    if reviews.valid(
                        "problem.hint_reviewed",
                        h.locale,
                        h.ordinal,
                        h.reviewed_by,
                        h.reviewed_at,
                        h.body_md,
                    )
                ),
            )
            for locale, rows in _hints_by_locale(db, problem).items()
        ],
        publish_blockers=list(dict.fromkeys(b.code for b in blockers)),
        blocking_items=blockers,
    )


def problem_detail(
    db: Session, problem: Problem, user: User | None, locale: Locale, judge_available: bool
) -> ProblemDetail:
    tr, is_fallback = pick(problem.translations, locale)
    groups = list(
        db.scalars(
            select(TestGroup).where(TestGroup.problem_id == problem.id).order_by(TestGroup.ordinal)
        )
    )
    samples = db.execute(
        select(TestCase.input, TestCase.expected_output)
        .where(TestCase.problem_id == problem.id, TestCase.is_sample)
        .order_by(TestCase.ordinal)
    ).all()
    editorial_available = bool(
        db.scalar(select(exists().where(Editorial.problem_id == problem.id)))
    )
    unlocked = _unlock(db, user, problem) is not None or (
        user is not None and _has_accepted(db, user, problem)
    )
    summary = _summary(problem, locale, _my_statuses(db, user, [problem.id]).get(problem.id))
    return ProblemDetail(
        **summary.model_dump(),
        is_fallback=is_fallback,
        available_locales=sorted((t.locale for t in problem.translations), key=list(Locale).index),
        legend_md=tr.legend_md,
        input_md=tr.input_md,
        output_md=tr.output_md,
        notes_md=tr.notes_md,
        time_limit_ms=problem.time_limit_ms,
        memory_limit_mb=problem.memory_limit_mb,
        scoring=problem.scoring,
        groups=[
            GroupOut(ordinal=g.ordinal, points=g.points, description_md=g.description_md)
            for g in groups
            if g.ordinal > 0
        ],
        samples=[SampleOut(input=i, output=o) for i, o in samples],
        difficulty_rationale=problem.difficulty_rationale,
        attribution_md=problem.attribution_md,
        authors_md=problem.authors_md,
        generated_with_ai=problem.generated_with_ai,
        source=SourceOut(**problem.source) if problem.source else None,
        hint_count=_hint_total(db, problem),
        revealed_hint_count=_revealed_count(db, user, problem),
        editorial_available=editorial_available,
        editorial_unlocked=unlocked,
        languages=list(Language),
        judge_available=judge_available,
        review=_review_state(db, problem, user) if _is_admin(user) else None,
    )


# ------------------------------------------------------------------ hints & editorial


def hints(db: Session, problem: Problem, user: User, locale: Locale) -> HintsOut:
    revealed = set(
        db.scalars(
            select(HintReveal.ordinal).where(
                HintReveal.user_id == user.id, HintReveal.problem_id == problem.id
            )
        )
    )
    rows = list(db.scalars(select(Hint).where(Hint.problem_id == problem.id)))
    out = []
    for ordinal in sorted(revealed):
        hint, _ = pick([h for h in rows if h.ordinal == ordinal], locale)
        out.append(HintOut(ordinal=ordinal, locale=hint.locale, body_md=hint.body_md))
    return HintsOut(total=len({h.ordinal for h in rows}), revealed=out)


def _insert_once(db: Session, model: type[Base], **values: object) -> None:
    """Atomic idempotent insert: a double-click or concurrent request cannot cause a 500."""
    db.execute(pg_insert(model).values(**values).on_conflict_do_nothing())


def reveal_hint(
    db: Session, problem: Problem, user: User, ordinal: int, locale: Locale
) -> HintsOut:
    total = _hint_total(db, problem)
    if not 1 <= ordinal <= total:
        raise ApiError(404, "not_found", "Hint not found.")
    if db.get(HintReveal, (user.id, problem.id, ordinal)) is None:
        if ordinal != _revealed_count(db, user, problem) + 1:
            raise ApiError(409, "hint_out_of_order", "Reveal the previous hints first.")
        _insert_once(db, HintReveal, user_id=user.id, problem_id=problem.id, ordinal=ordinal)
    return hints(db, problem, user, locale)


def editorial(
    db: Session, problem: Problem, user: User, locale: Locale, give_up: bool = False
) -> EditorialOut:
    rows = list(db.scalars(select(Editorial).where(Editorial.problem_id == problem.id)))
    if not rows:
        raise ApiError(404, "not_found", "This problem has no editorial yet.")
    unlock = _unlock(db, user, problem)
    if unlock is None:
        if _has_accepted(db, user, problem):
            reason = UnlockReason.solved
        elif give_up:
            reason = UnlockReason.gave_up
        else:
            raise ApiError(
                403, "editorial_locked", "Solve the problem or give up to see the editorial."
            )
        _insert_once(db, EditorialUnlock, user_id=user.id, problem_id=problem.id, reason=reason)
        unlock = _unlock(db, user, problem)
        if unlock is None:  # pragma: no cover - the row exists after the insert
            raise RuntimeError("editorial unlock missing after insert")
    chosen, is_fallback = pick(rows, locale)
    return EditorialOut(
        locale=chosen.locale,
        is_fallback=is_fallback,
        body_md=chosen.body_md,
        reviewed=chosen.reviewed_by is not None,
        unlock_reason=unlock.reason,
    )


def self_report(db: Session, problem: Problem, user: User) -> SelfReportOut:
    _insert_once(db, SelfReport, user_id=user.id, problem_id=problem.id)
    report = db.get(SelfReport, (user.id, problem.id))
    if report is None:  # pragma: no cover - the row exists after the insert
        raise RuntimeError("self-report missing after insert")
    return SelfReportOut(problem_slug=problem.slug, created_at=report.created_at)


# ------------------------------------------------------------------ submissions


def _title(problem: Problem, locale: Locale) -> str:
    return pick(problem.translations, locale)[0].title


def submission_summary(s: Submission, locale: Locale) -> SubmissionSummary:
    return SubmissionSummary(
        id=s.id,
        problem_slug=s.problem.slug,
        problem_title=_title(s.problem, locale),
        language=s.language,
        state=s.state,
        verdict=s.verdict,
        score=s.score,
        hints_revealed_count=s.hints_revealed_count,
        created_at=s.created_at,
    )


def submission_detail(db: Session, s: Submission, locale: Locale) -> SubmissionDetail:
    # Each result keeps its own group ordinal and points, so results stay complete after a
    # re-import replaced the problem's test groups.
    results = sorted(
        (
            GroupResultOut(
                ordinal=r.group_ordinal, points=r.points, verdict=r.verdict, score=r.score
            )
            for r in s.group_results
        ),
        key=lambda r: r.ordinal,
    )
    return SubmissionDetail(
        **submission_summary(s, locale).model_dump(),
        source_code=s.source_code,
        group_results=results,
    )


logger = logging.getLogger("kyrgyzstem.judging")

# One statement, so PostgreSQL reads every value from one snapshot even under READ COMMITTED:
# a concurrent import (which replaces tests and v1 in one transaction) is seen wholly or not
# at all. No test inputs or outputs are read; v1 stands for them (judge/snapshot.py).
_SNAPSHOT_SQL = text(
    """
    SELECT p.tests_hash, p.scoring, p.time_limit_ms, p.memory_limit_mb,
           COALESCE((SELECT json_agg(json_build_array(g.ordinal, g.points))
                     FROM test_groups g WHERE g.problem_id = p.id), '[]') AS groups,
           COALESCE((SELECT json_agg(json_build_array(g.ordinal, c.ordinal, c.is_sample))
                     FROM test_cases c
                     LEFT JOIN test_groups g ON g.id = c.group_id AND g.problem_id = p.id
                     WHERE c.problem_id = p.id), '[]') AS tests
    FROM problems p WHERE p.id = :problem_id
    """
)


def judging_snapshot(db: Session, problem_id: uuid.UUID) -> judge_snapshot.JudgingSnapshot | None:
    """The problem's judging snapshot from one consistent read, or None if it has no v1
    test-set hash (a problem that was never imported). Raises `SnapshotError` for anything
    malformed: a test without a group of this problem, an unknown scoring mode, and so on."""
    row = db.execute(_SNAPSHOT_SQL, {"problem_id": problem_id}).one()
    if row.tests_hash is None:
        return None
    try:
        groups = tuple(judge_snapshot.SnapshotGroup(o, p) for o, p in row.groups)
        tests = tuple(judge_snapshot.SnapshotTest(g, o, s) for g, o, s in row.tests)
        result = judge_snapshot.JudgingSnapshot(
            tests_hash_v1=row.tests_hash,
            scoring=Scoring(row.scoring),
            time_limit_ms=row.time_limit_ms,
            memory_limit_mb=row.memory_limit_mb,
            groups=groups,
            tests=tests,
        )
    except (TypeError, ValueError) as exc:
        raise judge_snapshot.SnapshotError("malformed problem snapshot") from exc
    if any(t.group_ordinal is None for t in tests):
        raise judge_snapshot.SnapshotError("a test without a group of this problem")
    judge_snapshot.snapshot_hash(result)  # validates; raises SnapshotError
    return result


def _bound_snapshot_hash(db: Session, problem: Problem) -> str | None:
    """The v2 hash a new submission is bound to. NULL when the problem has no v1 hash (never
    invented), and NULL with a warning when its data is malformed: the submission is then
    unverifiable and will not be judged, instead of being bound to a snapshot that is not
    valid. `problems.tests_hash` (v1) is only read, never changed."""
    try:
        snapshot = judging_snapshot(db, problem.id)
    except judge_snapshot.SnapshotError:
        logger.warning("judge.snapshot_unbindable", extra={"fields": {"problem": problem.slug}})
        return None
    return None if snapshot is None else judge_snapshot.snapshot_hash(snapshot)


def create_submission(
    db: Session, user: User, data: SubmissionCreate, judge_available: bool
) -> Submission:
    problem = get_problem(db, data.problem_slug, user)
    if len(data.source_code.encode()) > MAX_SOURCE_BYTES:
        raise ApiError(422, "source_too_large", "Source code is limited to 64 KiB.")
    tests_hash = _bound_snapshot_hash(db, problem)
    submission = Submission(
        user_id=user.id,
        problem_id=problem.id,
        language=data.language,
        source_code=data.source_code,
        # Bound once at creation, for queued and not_judged alike; never replaced later.
        tests_hash=tests_hash,
        # Queued only when the judge takes this language and the problem has a valid
        # snapshot; otherwise stored and shown as not checked (never a judge_error later).
        state=SubmissionState.queued
        if judge_available and tests_hash is not None
        else SubmissionState.not_judged,
        # Authored hints plus help reported by other modules (e.g. AI answers).
        hints_revealed_count=_revealed_count(db, user, problem)
        + help.extra_help(db, user.id, problem.id),
    )
    db.add(submission)
    db.flush()
    return submission


def get_submission(db: Session, submission_id: uuid.UUID, user: User) -> Submission:
    submission = db.get(Submission, submission_id)
    # Other users' submissions look missing (no existence leak). Teachers: Phase 4.
    if submission is None or (submission.user_id != user.id and not _is_admin(user)):
        raise ApiError(404, "not_found", "Submission not found.")
    return submission


def list_submissions(
    db: Session, user: User, problem_slug: str | None, limit: int, offset: int
) -> tuple[list[Submission], int]:
    stmt = select(Submission).where(Submission.user_id == user.id)
    if problem_slug:
        stmt = stmt.join(Problem).where(Problem.slug == problem_slug)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Submission.created_at.desc()).limit(limit).offset(offset))
    return list(rows), total


# ------------------------------------------------------------------ progress


def progress(db: Session, user: User, locale: Locale, judge_available: bool) -> ProgressOut:
    topics = list(db.scalars(select(Topic).order_by(Topic.ordinal)))
    subs = db.execute(
        select(
            Submission.problem_id,
            Submission.state,
            Submission.verdict,
            Submission.hints_revealed_count,
            Submission.created_at,
        ).where(Submission.user_id == user.id)
    ).all()
    problem_ids = {row.problem_id for row in subs}
    # When the student gave up on a problem (editorial unlocked without a solve): anything
    # submitted from then on was written with the editorial at hand (DF1).
    gave_up_at = {
        problem_id: unlocked_at
        for problem_id, unlocked_at in db.execute(
            select(EditorialUnlock.problem_id, EditorialUnlock.unlocked_at).where(
                EditorialUnlock.user_id == user.id,
                EditorialUnlock.reason == UnlockReason.gave_up,
            )
        ).all()
    }
    problems: dict[str, metrics.ProblemRecord] = {}
    if problem_ids:
        for p in db.scalars(select(Problem).where(Problem.id.in_(problem_ids))):
            problems[str(p.id)] = metrics.ProblemRecord(
                id=str(p.id),
                band=p.difficulty_band.value,
                topics=tuple((pt.topic.slug, pt.stage) for pt in p.topics),
            )
    result = metrics.compute_progress(
        [
            metrics.AttemptRecord(
                problem_id=str(r.problem_id),
                state=r.state.value,
                verdict=r.verdict.value if r.verdict else None,
                hints_revealed_count=r.hints_revealed_count,
                created_at=r.created_at,
                after_give_up=r.problem_id in gave_up_at
                and r.created_at >= gave_up_at[r.problem_id],
            )
            for r in subs
        ],
        problems,
        {t.slug: t.stage_count for t in topics},
        utcnow(),
    )
    reports = db.execute(
        select(Problem.slug, SelfReport.created_at)
        .join(Problem, Problem.id == SelfReport.problem_id)
        .where(SelfReport.user_id == user.id)
        .order_by(SelfReport.created_at.desc())
    ).all()
    return ProgressOut(
        judge_available=judge_available,
        pending_not_judged=result.pending_not_judged,
        topics=[
            TopicProgressOut(
                slug=t.slug,
                name=pick(t.translations, locale)[0].name,
                stages=[
                    StageEvidenceOut(
                        stage=ev.stage,
                        unaided_by_band={Band(b): n for b, n in ev.unaided_by_band.items()},
                        with_hints=ev.with_hints,
                        demonstrated=ev.demonstrated,
                    )
                    for ev in result.stages[t.slug]
                ],
            )
            for t in topics
        ],
        hint_reliance_30d=result.hint_reliance_30d,
        median_attempts_to_accept=result.median_attempts_to_accept,
        demonstrated_topics=result.demonstrated_topics,
        total_topics=result.total_topics,
        demonstrated_threshold=metrics.DEMONSTRATED_MIN,
        self_reported=[SelfReportOut(problem_slug=s, created_at=c) for s, c in reports],
    )


# ------------------------------------------------------------------ review & publishing


# How long an admin write waits for another transaction holding the problem row (an import,
# another review or publish). Past it PostgreSQL raises lock_not_available, an
# OperationalError, which the API answers with a retryable 503; it is the first statement of
# the action, so nothing has been written and the request changed nothing.
LOCK_TIMEOUT_MS = 5000


def _admin_problem(db: Session, slug: str, *, lock: bool = False) -> Problem:
    """The problem for an admin action. `lock=True` (every write here) takes the problem row
    lock for the rest of the transaction, as the importer does, so a publish and an import
    or review of the same problem never interleave: the later one waits, then reads the
    other's committed state. FOR NO KEY UPDATE excludes those writers from each other but
    not student writes that only reference the problem (submissions, self-reports)."""
    stmt = select(Problem).where(Problem.slug == slug)
    if lock:
        db.execute(select(func.set_config("lock_timeout", f"{LOCK_TIMEOUT_MS}ms", True)))
        stmt = stmt.with_for_update(of=Problem, key_share=True).execution_options(
            populate_existing=True
        )
    problem = db.scalar(stmt)
    if problem is None:
        raise ApiError(404, "not_found", "Problem not found.")
    return problem


def review_difficulty(
    db: Session, slug: str, data: DifficultyUpdate, actor: User, ip: str
) -> Problem:
    problem = _admin_problem(db, slug, lock=True)
    if problem.created_by == actor.id:
        raise ApiError(403, "reviewer_is_author", "The author cannot review their own problem.")
    audit.record(
        db,
        action="problem.difficulty_changed",
        actor_id=actor.id,
        target_type="problem",
        target_id=problem.id,
        details={
            "from": problem.difficulty_band.value,
            "to": data.band.value,
            "status_from": problem.difficulty_status.value,
            "rationale": data.rationale,
            # What else this review confirmed (see difficulty_spec_sha256).
            "spec_sha256": difficulty_spec_sha256(problem),
        },
        ip=ip,
    )
    problem.difficulty_band = data.band
    problem.difficulty_rationale = data.rationale
    problem.difficulty_status = DifficultyStatus.reviewed
    return problem


def review_translation(db: Session, slug: str, locale: Locale, actor: User, ip: str) -> Problem:
    problem = _admin_problem(db, slug, lock=True)
    if problem.created_by == actor.id:
        raise ApiError(403, "reviewer_is_author", "The author cannot review their own problem.")
    translation = next((t for t in problem.translations if t.locale == locale), None)
    if translation is None:
        raise ApiError(404, "not_found", "No statement in this language.")
    translation.reviewed_by = actor.id
    translation.reviewed_at = utcnow()
    audit.record(
        db,
        action="problem.translation_reviewed",
        actor_id=actor.id,
        target_type="problem",
        target_id=problem.id,
        details={"locale": locale.value, "statement_sha256": statement_sha256(translation)},
        ip=ip,
    )
    return problem


def verify_rights(
    db: Session, slug: str, data: RightsVerification, actor: User, ip: str
) -> Problem:
    """A human confirms the problem may be published under its declared license.

    The author may not do this. Where the author is linked to an account (`created_by`),
    the server enforces it. Imported problems usually have no linked author; then the
    server cannot check identity, so the reviewer must attest it explicitly and the audit
    entry records that the identity was attested, not verified.
    """
    problem = _admin_problem(db, slug, lock=True)
    if problem.created_by == actor.id:
        raise ApiError(403, "reviewer_is_author", "The author cannot review their own problem.")
    linked = problem.created_by is not None
    if not linked and not data.attest_not_author:
        raise ApiError(
            422,
            "author_attestation_required",
            "This problem's author is not linked to an account: confirm you are not an author.",
        )
    problem.rights_verified_by = actor.id
    problem.rights_verified_at = verified_at = utcnow()
    audit.record(
        db,
        action="problem.rights_verified",
        actor_id=actor.id,
        target_type="problem",
        target_id=problem.id,
        details={
            "origin": problem.origin.value,
            "license": problem.license,
            "generated_with_ai": problem.generated_with_ai,
            "source_url": (problem.source or {}).get("url"),
            "author_identity": "linked" if linked else "unlinked",
            "attested_not_author": data.attest_not_author,
            # Binds this event to this confirmation and to the provenance it covered.
            "verified_at": verified_at.isoformat(),
            "provenance_sha256": provenance_sha256(problem),
        },
        ip=ip,
    )
    return problem


def body_sha256(body_md: str) -> str:
    """Evidence of exactly which text a reviewer approved (the stored, normalized body)."""
    return hashlib.sha256(body_md.encode("utf-8")).hexdigest()


def _json_sha256(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def statement_sha256(t: ProblemTranslation) -> str:
    """Evidence of exactly which statement text (all of its fields) a reviewer approved."""
    return _json_sha256([t.title, t.legend_md, t.input_md, t.output_md, t.notes_md])


def difficulty_spec_sha256(problem: Problem) -> str:
    """What a difficulty review confirms besides the band: the judging limits and scoring and
    the curriculum placement (topics with their stages, tags) shown beside it. A change to
    any of them makes the review stale."""
    return _json_sha256(
        {
            "time_limit_ms": problem.time_limit_ms,
            "memory_limit_mb": problem.memory_limit_mb,
            "scoring": Scoring(problem.scoring).value,
            "topics": sorted([str(t.topic_id), t.stage, t.is_primary] for t in problem.topics),
            "tags": sorted(str(t.id) for t in problem.tags),
        }
    )


def difficulty_review_base_band(db: Session, problem: Problem) -> Band | None:
    """The band the current difficulty review started from: `from` of the latest audited
    difficulty review made while the band was still the author's estimate. Only the importer
    sets the band in that state, so this is the band the content last imported with; later
    re-reviews keep it. None when no such review is recorded."""
    rows = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.target_type == "problem",
            AuditLog.target_id == str(problem.id),
            AuditLog.action == "problem.difficulty_changed",
        )
        .order_by(AuditLog.created_at.desc())
    )
    for row in rows:
        details = row.details or {}
        if details.get("status_from") == DifficultyStatus.author_estimate.value:
            try:
                return Band(str(details.get("from")))
            except ValueError:
                return None
    return None


def provenance_sha256(problem: Problem) -> str:
    """The provenance a rights confirmation covers: the fields whose change makes the
    importer clear it (see importer._provenance)."""
    return _json_sha256(
        {
            "origin": problem.origin.value,
            "license": problem.license,
            "attribution_md": problem.attribution_md,
            "authors_md": problem.authors_md,
            "generated_with_ai": problem.generated_with_ai,
            "source": problem.source,
            "created_by": str(problem.created_by) if problem.created_by else None,
        }
    )


def _require_independent_reviewer(problem: Problem, actor: User, attest_not_author: bool) -> bool:
    """The linked author may not review. With no linked author (imported content) the server
    cannot check identity, so the reviewer must attest it. Returns whether one is linked."""
    if problem.created_by == actor.id:
        raise ApiError(403, "reviewer_is_author", "The author cannot review their own problem.")
    linked = problem.created_by is not None
    if not linked and not attest_not_author:
        raise ApiError(
            422,
            "author_attestation_required",
            "This problem's author is not linked to an account: confirm you are not an author.",
        )
    return linked


def _editorials(db: Session, problem: Problem) -> list[Editorial]:
    rows = db.scalars(select(Editorial).where(Editorial.problem_id == problem.id))
    return sorted(rows, key=lambda e: list(Locale).index(e.locale))


def admin_editorials(db: Session, slug: str) -> list[AdminEditorialOut]:
    """Editorial text for reviewers. Deliberately not the student flow: reading here never
    records an unlock, a give-up or any other progress for the reviewer."""
    problem = _admin_problem(db, slug)
    return [
        AdminEditorialOut(
            locale=e.locale,
            body_md=e.body_md,
            body_sha256=body_sha256(e.body_md),
            reviewed=e.reviewed_by is not None,
            reviewed_at=e.reviewed_at,
        )
        for e in _editorials(db, problem)
    ]


def review_editorial(
    db: Session, slug: str, locale: Locale, data: EditorialReview, actor: User, ip: str
) -> Problem:
    """A human reviewed one editorial. Repeating it re-stamps the review and is audited again.

    The linked author may not review; with no linked author the reviewer must attest it
    (as for rights verification). Publication status is never changed here.
    """
    problem = _admin_problem(db, slug, lock=True)
    linked = _require_independent_reviewer(problem, actor, data.attest_not_author)
    editorial = db.get(Editorial, (problem.id, locale))
    if editorial is None:
        raise ApiError(404, "not_found", "No editorial in this language.")
    editorial.reviewed_by = actor.id
    editorial.reviewed_at = utcnow()
    audit.record(
        db,
        action="problem.editorial_reviewed",
        actor_id=actor.id,
        target_type="problem",
        target_id=problem.id,
        details={
            "locale": locale.value,
            "body_sha256": body_sha256(editorial.body_md),
            "author_identity": "linked" if linked else "unlinked",
            "attested_not_author": data.attest_not_author,
        },
        ip=ip,
    )
    return problem


def _hints_by_locale(db: Session, problem: Problem) -> dict[Locale, list[Hint]]:
    """Hints grouped by locale (ky, ru, en order), each list ordered by ordinal."""
    rows = db.scalars(select(Hint).where(Hint.problem_id == problem.id).order_by(Hint.ordinal))
    grouped: dict[Locale, list[Hint]] = {}
    for hint in rows:
        grouped.setdefault(hint.locale, []).append(hint)
    return {locale: grouped[locale] for locale in Locale if locale in grouped}


def admin_hints(db: Session, slug: str) -> list[AdminHintOut]:
    """Hint text for reviewers. Deliberately not the student flow: reading here never records
    a hint reveal or any other progress for the reviewer."""
    problem = _admin_problem(db, slug)
    return [
        AdminHintOut(
            locale=h.locale,
            ordinal=h.ordinal,
            body_md=h.body_md,
            body_sha256=body_sha256(h.body_md),
            reviewed=h.reviewed_by is not None,
            reviewed_at=h.reviewed_at,
        )
        for rows in _hints_by_locale(db, problem).values()
        for h in rows
    ]


def _review_hint_rows(
    db: Session,
    problem: Problem,
    hints: list[Hint],
    linked: bool,
    attested: bool,
    actor: User,
    ip: str,
) -> None:
    """Stamp and audit each hint (one audit event per hint) in the caller's transaction."""
    now = utcnow()
    for hint in hints:
        hint.reviewed_by = actor.id
        hint.reviewed_at = now
        audit.record(
            db,
            action="problem.hint_reviewed",
            actor_id=actor.id,
            target_type="problem",
            target_id=problem.id,
            details={
                "locale": hint.locale.value,
                "ordinal": hint.ordinal,
                "body_sha256": body_sha256(hint.body_md),
                "author_identity": "linked" if linked else "unlinked",
                "attested_not_author": attested,
            },
            ip=ip,
        )


def review_hint(
    db: Session, slug: str, locale: Locale, ordinal: int, data: HintReview, actor: User, ip: str
) -> Problem:
    """A human reviewed one hint. Repeating it re-stamps the review and is audited again.
    Publication status is never changed here."""
    problem = _admin_problem(db, slug, lock=True)
    linked = _require_independent_reviewer(problem, actor, data.attest_not_author)
    hint = db.scalar(
        select(Hint).where(
            Hint.problem_id == problem.id, Hint.locale == locale, Hint.ordinal == ordinal
        )
    )
    if hint is None:
        raise ApiError(404, "not_found", "No hint at this position in this language.")
    _review_hint_rows(db, problem, [hint], linked, data.attest_not_author, actor, ip)
    return problem


def review_locale_hints(
    db: Session, slug: str, locale: Locale, data: HintReview, actor: User, ip: str
) -> Problem:
    """Review every hint of one locale at once: one audit event per hint. Nothing is
    committed here, so the router's single commit makes it all-or-nothing."""
    problem = _admin_problem(db, slug, lock=True)
    linked = _require_independent_reviewer(problem, actor, data.attest_not_author)
    hints = _hints_by_locale(db, problem).get(locale, [])
    if not hints:
        raise ApiError(404, "not_found", "No hints in this language.")
    _review_hint_rows(db, problem, hints, linked, data.attest_not_author, actor, ip)
    return problem


def publish(db: Session, slug: str, actor: User, ip: str) -> Problem:
    problem = _admin_problem(db, slug, lock=True)
    blockers = publish_blockers(db, problem, actor)
    if blockers:
        raise ApiError(409, "publish_blocked", "This problem cannot be published yet.", blockers)
    problem.status = ProblemStatus.published
    problem.published_at = utcnow()
    problem.published_by = actor.id
    audit.record(
        db, action="problem.published", actor_id=actor.id, target_type="problem",
        target_id=problem.id, ip=ip,
    )  # fmt: skip
    return problem


def unpublish(db: Session, slug: str, actor: User, ip: str) -> Problem:
    problem = _admin_problem(db, slug, lock=True)
    if problem.status != ProblemStatus.published:
        raise ApiError(409, "not_published", "This problem is not published.")
    problem.status = ProblemStatus.in_review
    audit.record(
        db, action="problem.unpublished", actor_id=actor.id, target_type="problem",
        target_id=problem.id, ip=ip,
    )  # fmt: skip
    return problem
