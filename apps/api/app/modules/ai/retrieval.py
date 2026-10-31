"""Source collection: a direct lookup of approved material for one problem (no search index).

Approved = the problem is published (it passed human review), and the editorial is used
only if a person reviewed it. Content from repository files is supported for the
evaluation runner.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.olympiad.content import ProblemContent
from app.modules.olympiad.models import Editorial, Hint, Problem
from app.modules.olympiad.service import pick
from app.modules.users.models import Locale


@dataclass(frozen=True)
class Source:
    id: str
    kind: str  # statement | metadata | hint | editorial
    label: str
    text: str


def _metadata(band: str, time_ms: int, memory_mb: int, topics: list[str], tags: list[str]) -> str:
    return (
        f"Difficulty band (estimate): {band}. Limits: {time_ms} ms, {memory_mb} MB. "
        f"Topics: {', '.join(topics) or 'none'}. Tags: {', '.join(tags) or 'none'}."
    )


def _statement(title: str, legend: str, inp: str, out: str, notes: str | None) -> str:
    parts = [f"Title: {title}", legend, f"Input:\n{inp}", f"Output:\n{out}"]
    if notes:
        parts.append(f"Notes:\n{notes}")
    return "\n\n".join(parts)


def _number(sources: list[tuple[str, str, str]]) -> list[Source]:
    return [
        Source(f"S{i}", kind, label, text) for i, (kind, label, text) in enumerate(sources, start=1)
    ]


def collect_sources(
    db: Session, problem: Problem, locale: Locale, include_editorial: bool
) -> tuple[list[Source], str | None]:
    """Returns (sources, reviewed editorial text if included)."""
    tr, _ = pick(problem.translations, locale)
    raw: list[tuple[str, str, str]] = [
        (
            "statement",
            "Problem statement",
            _statement(tr.title, tr.legend_md, tr.input_md, tr.output_md, tr.notes_md),
        ),
        (
            "metadata",
            "Problem metadata",
            _metadata(
                problem.difficulty_band.value,
                problem.time_limit_ms,
                problem.memory_limit_mb,
                [f"{pt.topic.slug} (stage {pt.stage})" for pt in problem.topics],
                [t.slug for t in problem.tags],
            ),
        ),
    ]
    hints = list(db.scalars(select(Hint).where(Hint.problem_id == problem.id)))
    for ordinal in sorted({h.ordinal for h in hints}):
        hint, _ = pick([h for h in hints if h.ordinal == ordinal], locale)
        raw.append(("hint", f"Authored hint {ordinal}", hint.body_md))
    editorial_text = None
    if include_editorial:
        reviewed = [
            e
            for e in db.scalars(select(Editorial).where(Editorial.problem_id == problem.id))
            if e.reviewed_by is not None
        ]
        if reviewed:
            ed, _ = pick(reviewed, locale)
            editorial_text = ed.body_md
            raw.append(("editorial", "Reviewed editorial", ed.body_md))
    return _number(raw), editorial_text


def sources_from_content(
    problem: ProblemContent, locale: str, include_editorial: bool
) -> tuple[list[Source], str | None]:
    """Same shape from repository content (evaluation runner; no database)."""
    st = problem.statements.get(locale) or problem.statements.get("ru") or problem.statements["en"]
    raw: list[tuple[str, str, str]] = [
        (
            "statement",
            "Problem statement",
            _statement(st.title, st.legend, st.input, st.output, st.notes),
        ),
        (
            "metadata",
            "Problem metadata",
            _metadata(
                problem.band,
                problem.time_limit_ms,
                problem.memory_limit_mb,
                [f"{t.slug} (stage {t.stage})" for t in problem.topics],
                problem.tags,
            ),
        ),
    ]
    hints = problem.hints.get(locale) or problem.hints.get("ru") or problem.hints.get("en", [])
    raw += [("hint", f"Authored hint {i}", h) for i, h in enumerate(hints, start=1)]
    editorial_text = None
    if include_editorial:
        editorial_text = problem.editorials.get(locale) or problem.editorials.get("en")
        if editorial_text:
            raw.append(("editorial", "Editorial", editorial_text))
    return _number(raw), editorial_text
