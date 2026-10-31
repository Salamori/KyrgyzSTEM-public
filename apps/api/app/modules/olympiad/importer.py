"""Upsert repository content into the database (used by scripts/import_content.py)."""

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.modules.olympiad import content as c
from app.modules.olympiad.models import (
    Band,
    DifficultyStatus,
    Editorial,
    Hint,
    Language,
    Origin,
    PrerequisiteStrength,
    Problem,
    ProblemStatus,
    ProblemTopic,
    ProblemTranslation,
    ReferenceSolution,
    Scoring,
    Tag,
    TagTranslation,
    TestCase,
    TestGroup,
    Topic,
    TopicPrerequisite,
    TopicTranslation,
)
from app.modules.olympiad.service import difficulty_review_base_band, difficulty_spec_sha256
from app.modules.users.models import Locale, User


class ContentImportError(c.ContentError):
    pass


def import_curriculum(db: Session, curriculum: c.Curriculum) -> None:
    topics: dict[str, Topic] = {t.slug: t for t in db.scalars(select(Topic))}
    for ordinal, spec in enumerate(curriculum.topics):
        topic = topics.get(spec.slug) or Topic(slug=spec.slug)
        topic.ordinal = ordinal
        topic.stage_count = spec.stages
        topic.translations = [
            TopicTranslation(locale=Locale(loc), name=name, summary_md=spec.summaries.get(loc))
            for loc, name in spec.names.items()
        ]
        db.add(topic)
        topics[spec.slug] = topic
    db.flush()

    db.execute(delete(TopicPrerequisite))
    for p in curriculum.prerequisites:
        db.add(
            TopicPrerequisite(
                topic_id=topics[p.topic].id,
                stage=p.stage,
                requires_topic_id=topics[p.requires].id,
                requires_stage=p.requires_stage,
                strength=PrerequisiteStrength(p.strength),
            )
        )

    tags = {t.slug: t for t in db.scalars(select(Tag))}
    for tag_spec in curriculum.tags:
        tag = tags.get(tag_spec.slug) or Tag(slug=tag_spec.slug)
        tag.translations = [
            TagTranslation(locale=Locale(loc), name=name) for loc, name in tag_spec.names.items()
        ]
        db.add(tag)
    db.flush()


@dataclass(frozen=True)
class ImportResult:
    slug: str
    created: bool
    tests: int
    tests_hash: str


def _provenance(row: Problem) -> tuple[object, ...]:
    return (
        row.origin, row.license, row.attribution_md, row.authors_md,
        row.generated_with_ai, row.source, row.created_by,
    )  # fmt: skip


def import_problem(
    db: Session, problem: c.ProblemContent, author: User | None = None
) -> ImportResult:
    """Verify, then upsert one problem. New problems start `in_review`.

    Published problems are never modified by an import: unpublish first (an audited admin
    action), so students never see content change underneath them.
    """
    c.verify_problem(problem)  # samples + brute-force cross-check, raises on failure
    tests = c.build_tests(problem)
    digest = c.tests_hash(tests)

    # Lock the row like every admin write does (service._admin_problem), so a concurrent
    # publish either finishes first (and this import then sees `published` and refuses) or
    # waits for this import to commit and then judges the new content. FOR NO KEY UPDATE:
    # it excludes the other writers but not rows that merely reference the problem.
    existing = db.scalar(
        select(Problem)
        .where(Problem.slug == problem.slug)
        .with_for_update(of=Problem, key_share=True)
        .execution_options(populate_existing=True)
    )
    if existing is not None and existing.status == ProblemStatus.published:
        raise ContentImportError(f"{problem.slug}: published problems cannot be re-imported")

    topics = {t.slug: t for t in db.scalars(select(Topic))}
    tags = {t.slug: t for t in db.scalars(select(Tag))}
    for ref in problem.topics:
        if ref.slug not in topics:
            raise ContentImportError(f"{problem.slug}: unknown topic {ref.slug!r}")
    for slug in problem.tags:
        if slug not in tags:
            raise ContentImportError(f"{problem.slug}: unknown tag {slug!r}")

    row = existing or Problem(
        slug=problem.slug,
        status=ProblemStatus.in_review,
        difficulty_band=Band(problem.band),
    )
    # A reviewed difficulty survives only if the author's band in the file is the one the
    # review started from (a reviewer's correction of it stands) and the limits, scoring,
    # topics and tags it also confirmed are unchanged. Decided after those are updated below.
    file_band = Band(problem.band)
    keep_difficulty = (
        existing is not None
        and existing.difficulty_status == DifficultyStatus.reviewed
        and file_band == (difficulty_review_base_band(db, existing) or existing.difficulty_band)
    )
    spec_before = difficulty_spec_sha256(existing) if existing is not None else None
    row.time_limit_ms = problem.time_limit_ms
    row.memory_limit_mb = problem.memory_limit_mb
    row.scoring = Scoring(problem.scoring)
    before = _provenance(row) if existing else None
    row.origin = Origin(problem.origin)
    row.license = problem.license
    row.attribution_md = problem.attribution
    row.authors_md = problem.authors
    row.generated_with_ai = problem.generated_with_ai
    row.source = asdict(problem.source) if problem.source else None
    if author is not None:
        # The operator names the author's account, so "the author cannot review or
        # publish" is enforced for this problem. A different existing link is a conflict.
        if row.created_by not in (None, author.id):
            raise ContentImportError(f"{problem.slug}: already linked to another author account")
        row.created_by = author.id
    if before != _provenance(row):
        # Rights were confirmed for different provenance: a human checks again.
        row.rights_verified_by = None
        row.rights_verified_at = None
    row.tests_hash = digest

    # Translations keep their review only if the text is unchanged.
    old = {t.locale: t for t in row.translations} if existing else {}
    new_translations = []
    for loc, st in problem.statements.items():
        locale = Locale(loc)
        prev = old.get(locale)
        same = prev is not None and (
            prev.title,
            prev.legend_md,
            prev.input_md,
            prev.output_md,
            prev.notes_md,
        ) == (st.title, st.legend, st.input, st.output, st.notes)
        new_translations.append(
            ProblemTranslation(
                locale=locale,
                title=st.title,
                legend_md=st.legend,
                input_md=st.input,
                output_md=st.output,
                notes_md=st.notes,
                reviewed_by=prev.reviewed_by if same and prev else None,
                reviewed_at=prev.reviewed_at if same and prev else None,
            )
        )
    row.translations = new_translations
    row.topics = [
        ProblemTopic(topic_id=topics[r.slug].id, stage=r.stage, is_primary=r.primary)
        for r in problem.topics
    ]
    row.tags = [tags[s] for s in problem.tags]
    if not (keep_difficulty and difficulty_spec_sha256(row) == spec_before):
        # New, changed band, or changed spec: back to the author's estimate and rationale,
        # to be reviewed again. Otherwise the reviewer's band, rationale and evidence stand.
        row.difficulty_band = file_band
        row.difficulty_status = DifficultyStatus.author_estimate
        row.difficulty_rationale = problem.band_rationale
    db.add(row)
    db.flush()

    # Editorials keep their review only if the text is unchanged (like translations).
    reviewed_editorials = {
        locale: (body, reviewed_by, reviewed_at)
        for locale, body, reviewed_by, reviewed_at in db.execute(
            select(
                Editorial.locale, Editorial.body_md, Editorial.reviewed_by, Editorial.reviewed_at
            ).where(Editorial.problem_id == row.id)
        )
    }
    # Hints keep their reviews only if the locale's whole ordered list is unchanged: hints
    # build on each other, so any edit, addition, removal or reordering clears that locale.
    previous_hints: dict[Locale, list[tuple[str, uuid.UUID | None, datetime | None]]] = {}
    for locale, body, reviewed_by, reviewed_at in db.execute(
        select(Hint.locale, Hint.body_md, Hint.reviewed_by, Hint.reviewed_at)
        .where(Hint.problem_id == row.id)
        .order_by(Hint.locale, Hint.ordinal)
    ):
        previous_hints.setdefault(locale, []).append((body, reviewed_by, reviewed_at))
    for model in (Hint, Editorial, ReferenceSolution, TestGroup):
        db.execute(delete(model).where(model.problem_id == row.id))
    for loc, bodies in problem.hints.items():
        previous = previous_hints.get(Locale(loc), [])
        unchanged = [body for body, _, _ in previous] == bodies
        for ordinal, body in enumerate(bodies, start=1):
            _, reviewed_by, reviewed_at = previous[ordinal - 1] if unchanged else (None, None, None)
            db.add(
                Hint(
                    problem_id=row.id,
                    locale=Locale(loc),
                    ordinal=ordinal,
                    body_md=body,
                    reviewed_by=reviewed_by,
                    reviewed_at=reviewed_at,
                )
            )
    for loc, body in problem.editorials.items():
        prev_body, reviewed_by, reviewed_at = reviewed_editorials.get(
            Locale(loc), (None, None, None)
        )
        same = prev_body == body
        db.add(
            Editorial(
                problem_id=row.id,
                locale=Locale(loc),
                body_md=body,
                reviewed_by=reviewed_by if same else None,
                reviewed_at=reviewed_at if same else None,
            )
        )

    now = utcnow()
    db.add(
        ReferenceSolution(
            problem_id=row.id,
            language=Language.python,
            source=(problem.directory / "solution.py").read_text(encoding="utf-8"),
            is_primary=True,
            verified_at=now,  # verify_problem passed and the tests were built from it
            verified_tests_hash=digest,
        )
    )
    cpp = problem.directory / "solution.cpp"
    if cpp.exists():
        # Compiled and checked by tests/test_content.py in CI, not by the importer.
        db.add(
            ReferenceSolution(
                problem_id=row.id,
                language=Language.cpp,
                source=cpp.read_text(encoding="utf-8"),
            )
        )

    groups: dict[int, TestGroup] = {}
    for g in problem.groups:
        group = TestGroup(
            problem_id=row.id, ordinal=g.ordinal, points=g.points, description_md=g.description
        )
        db.add(group)
        groups[g.ordinal] = group
    db.flush()
    for t in tests:
        db.add(
            TestCase(
                problem_id=row.id,
                group_id=groups[t.group].id,
                ordinal=t.ordinal,
                is_sample=t.is_sample,
                input=t.input,
                expected_output=t.output,
            )
        )
    db.flush()
    return ImportResult(problem.slug, existing is None, len(tests), digest)
