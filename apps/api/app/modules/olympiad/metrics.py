"""Learning metrics (proposal 0001 §7) as pure functions over plain records.

Only judged evidence counts. Self-reports and `not_judged` submissions are reported
separately and never feed these numbers. Retention and mixed-set recognition need
learning paths and contests (Phase 4) and are not computed yet.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Distinct hint-free accepted problems needed to call a topic stage "demonstrated".
# Provisional: proposal 0001 §11 lists this threshold as an open educational decision.
DEMONSTRATED_MIN = 2
HINT_RELIANCE_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class AttemptRecord:
    problem_id: str
    state: str  # SubmissionState value
    verdict: str | None  # Verdict value
    hints_revealed_count: int
    created_at: datetime
    # Submitted after the student gave up and unlocked the editorial: never unaided (DF1).
    after_give_up: bool = False


def is_unaided(a: AttemptRecord) -> bool:
    return a.hints_revealed_count == 0 and not a.after_give_up


@dataclass(frozen=True)
class ProblemRecord:
    id: str
    band: str
    topics: tuple[tuple[str, int], ...]  # (topic slug, stage)


@dataclass
class StageEvidence:
    stage: int
    unaided_by_band: dict[str, int] = field(default_factory=dict)
    with_hints: int = 0
    demonstrated: bool = False


@dataclass
class Progress:
    stages: dict[str, list[StageEvidence]]  # topic slug -> stages 1..n
    hint_reliance_30d: float | None
    median_attempts_to_accept: float | None
    demonstrated_topics: int
    total_topics: int
    pending_not_judged: int


def is_judged(a: AttemptRecord) -> bool:
    """A real attempt: judged to completion and not a compile error."""
    return a.state == "done" and a.verdict is not None and a.verdict != "compile_error"


def compute_progress(
    attempts: list[AttemptRecord],
    problems: dict[str, ProblemRecord],
    topic_stage_counts: dict[str, int],
    now: datetime,
) -> Progress:
    by_problem: dict[str, list[AttemptRecord]] = defaultdict(list)
    for a in sorted(attempts, key=lambda a: a.created_at):
        by_problem[a.problem_id].append(a)

    stages = {
        slug: [StageEvidence(stage=s) for s in range(1, count + 1)]
        for slug, count in topic_stage_counts.items()
    }
    attempts_to_accept: list[int] = []
    recent_accepts = recent_with_hints = 0

    for problem_id, history in by_problem.items():
        problem = problems.get(problem_id)
        judged = [a for a in history if is_judged(a)]
        accepted = [a for a in judged if a.verdict == "accepted"]
        if problem is None or not accepted:
            continue
        first = accepted[0]
        attempts_to_accept.append(judged.index(first) + 1)
        unaided = any(is_unaided(a) for a in accepted)
        if now - first.created_at <= HINT_RELIANCE_WINDOW:
            recent_accepts += 1
            recent_with_hints += 0 if unaided else 1
        for slug, stage in problem.topics:
            if slug not in stages or not 1 <= stage <= len(stages[slug]):
                continue
            ev = stages[slug][stage - 1]
            if unaided:
                ev.unaided_by_band[problem.band] = ev.unaided_by_band.get(problem.band, 0) + 1
            else:
                ev.with_hints += 1

    for evidence in stages.values():
        # Evidence at a higher stage also counts towards the lower stages of the topic.
        running = 0
        for ev in reversed(evidence):
            running += sum(ev.unaided_by_band.values())
            ev.demonstrated = running >= DEMONSTRATED_MIN

    return Progress(
        stages=stages,
        hint_reliance_30d=(recent_with_hints / recent_accepts) if recent_accepts else None,
        median_attempts_to_accept=(
            float(statistics.median(attempts_to_accept)) if attempts_to_accept else None
        ),
        demonstrated_topics=sum(1 for ev in stages.values() if ev and ev[0].demonstrated),
        total_topics=len(stages),
        pending_not_judged=sum(1 for a in attempts if a.state == "not_judged"),
    )
