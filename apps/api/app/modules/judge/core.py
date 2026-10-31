"""Pure judging rules: from per-test run outcomes to group results, a verdict and a score.

Deterministic and side-effect free: no database session, network, environment or code
execution. A future worker runs submissions in the isolated judge, collects one `TestRun`
per executed test and calls `judge_submission`; nothing in the request path uses this yet.

Rules (Step 5.1, approved):
* A group stops at its first failing test; its verdict is that test's (D1).
* `ioi`: the score is the sum of the points of fully passed scored groups; `accepted` at 100
  with every sample passing, else `partial` above 0, else the first failing scored group's
  verdict (D2). A failed sample never costs points but blocks `accepted`: every scored group
  passing with a failed sample is `partial` with score 100 (D4).
* `binary`: `accepted` and 100 only if every group, samples included, passes; otherwise 0
  and the first failing group's verdict. Each group still reports its own points if it
  passed, so the student sees which groups did (D3).
* A compile error is `done` / `compile_error` / 0 with no group results (D5).
* An internal judge error anywhere makes the whole submission `judge_error` with no
  verdict, score or group results; it is never shown as the contestant's fault (D6).
* Invalid test data or results raise `JudgeDataError`; nothing invalid counts as passed (D7).
* Only the approved state transitions are allowed (D8).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.olympiad.content import same_output
from app.modules.olympiad.models import Scoring, SubmissionState, Verdict

SAMPLE_GROUP = 0  # group 0 holds the samples and is worth 0 points
TOTAL_POINTS = 100  # the scored groups' points sum to this (content.load_problem)


class JudgeDataError(ValueError):
    """Test data or run results that cannot be judged. Fail closed: the caller records
    the submission as `judge_error`, never as passed."""


class InvalidTransition(ValueError):
    """A submission state change outside the approved transitions."""


class RunStatus(StrEnum):
    """How one test run ended, as reported by the sandbox."""

    completed = "completed"  # exited normally; the output is then compared
    time_limit = "time_limit"
    memory_limit = "memory_limit"
    runtime_error = "runtime_error"
    internal_error = "internal_error"  # the judge failed, not the program


_FAILED_RUN_VERDICTS = {
    RunStatus.time_limit: Verdict.time_limit,
    RunStatus.memory_limit: Verdict.memory_limit,
    RunStatus.runtime_error: Verdict.runtime_error,
}


@dataclass(frozen=True)
class TestRun:
    """The result of running one test (in the group's test order)."""

    __test__ = False  # not a pytest class

    status: RunStatus
    output: str = ""
    time_ms: int | None = None
    memory_kb: int | None = None


@dataclass(frozen=True)
class GroupSpec:
    """One test group to judge: its points and the expected output of each test, in order."""

    ordinal: int
    points: int
    expected_outputs: tuple[str, ...]


@dataclass(frozen=True)
class GroupResult:
    """Mirrors `SubmissionGroupResult`: a passed group has verdict `accepted`."""

    ordinal: int
    points: int
    verdict: Verdict
    score: int
    max_time_ms: int | None
    max_memory_kb: int | None

    @property
    def passed(self) -> bool:
        return self.verdict == Verdict.accepted


@dataclass(frozen=True)
class SubmissionResult:
    """The final outcome of judging one submission, consistent with the `submissions`
    constraints: a verdict and a score exist only when the state is `done`."""

    state: SubmissionState
    verdict: Verdict | None
    score: int | None
    groups: tuple[GroupResult, ...] = ()

    def __post_init__(self) -> None:
        if self.state == SubmissionState.done:
            if self.verdict is None or self.score is None:
                raise ValueError("a done submission has a verdict and a score")
            if not 0 <= self.score <= TOTAL_POINTS:
                raise ValueError("score out of range")
        elif self.state == SubmissionState.judge_error:
            if self.verdict is not None or self.score is not None or self.groups:
                raise ValueError("a judge error has no verdict, score or group results")
        else:
            raise ValueError(f"not a final judging state: {self.state}")


def check_output(actual: str, expected: str) -> bool:
    """The platform's checker: token-wise comparison (see `content.same_output`)."""
    return same_output(actual, expected)


def compile_error() -> SubmissionResult:
    return SubmissionResult(SubmissionState.done, Verdict.compile_error, 0)


def internal_error() -> SubmissionResult:
    return SubmissionResult(SubmissionState.judge_error, None, None)


class _InternalError(Exception):
    pass


def _max(values: Sequence[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return max(known) if known else None


def judge_group(group: GroupSpec, runs: Sequence[TestRun]) -> GroupResult:
    """Judge one group from its test runs. The runs stop at the first failing test (D1), so
    there is one run per test, or fewer only when the last run failed."""
    if not group.expected_outputs:
        raise JudgeDataError(f"group {group.ordinal} has no tests")
    if len(runs) > len(group.expected_outputs):
        raise JudgeDataError(f"group {group.ordinal}: more results than tests")
    if not runs:
        raise JudgeDataError(f"group {group.ordinal}: no results")
    verdict = Verdict.accepted
    for index, (run, expected) in enumerate(zip(runs, group.expected_outputs, strict=False)):
        if run.status == RunStatus.internal_error:
            raise _InternalError
        if run.status != RunStatus.completed:
            verdict = _FAILED_RUN_VERDICTS[run.status]
        elif not check_output(run.output, expected):
            verdict = Verdict.wrong_answer
        if verdict != Verdict.accepted:
            if index != len(runs) - 1:
                raise JudgeDataError(f"group {group.ordinal}: results after the first failure")
            break
    else:
        if len(runs) != len(group.expected_outputs):
            raise JudgeDataError(f"group {group.ordinal}: missing results")
    passed = verdict == Verdict.accepted
    return GroupResult(
        ordinal=group.ordinal,
        points=group.points,
        verdict=verdict,
        score=group.points if passed else 0,
        max_time_ms=_max([r.time_ms for r in runs]),
        max_memory_kb=_max([r.memory_kb for r in runs]),
    )


def _validate_groups(groups: Sequence[GroupSpec]) -> list[GroupSpec]:
    ordered = sorted(groups, key=lambda g: g.ordinal)
    ordinals = [g.ordinal for g in ordered]
    if len(set(ordinals)) != len(ordinals):
        raise JudgeDataError("duplicate group ordinals")
    scored = [g for g in ordered if g.ordinal != SAMPLE_GROUP]
    if not scored:
        raise JudgeDataError("no scored groups")
    for g in ordered:
        if g.ordinal < 0 or g.points < 0:
            raise JudgeDataError(f"group {g.ordinal}: invalid ordinal or points")
        if g.ordinal == SAMPLE_GROUP and g.points != 0:
            raise JudgeDataError("the sample group is worth 0 points")
    if sum(g.points for g in scored) != TOTAL_POINTS:
        raise JudgeDataError(f"scored group points must sum to {TOTAL_POINTS}")
    return ordered


def judge_submission(
    scoring: Scoring, groups: Sequence[GroupSpec], runs: Mapping[int, Sequence[TestRun]]
) -> SubmissionResult:
    """Judge a compiled submission from the runs of every group (keyed by group ordinal)."""
    ordered = _validate_groups(groups)
    if set(runs) != {g.ordinal for g in ordered}:
        raise JudgeDataError("results do not match the problem's groups")
    try:
        results = [judge_group(g, runs[g.ordinal]) for g in ordered]
    except _InternalError:
        return internal_error()  # partial group results are discarded (D6)

    failed = [r for r in results if not r.passed]
    if Scoring(scoring) == Scoring.binary:
        if not failed:
            return SubmissionResult(SubmissionState.done, Verdict.accepted, 100, tuple(results))
        return SubmissionResult(SubmissionState.done, failed[0].verdict, 0, tuple(results))

    score = sum(r.score for r in results if r.ordinal != SAMPLE_GROUP)
    if not failed:
        verdict = Verdict.accepted
    elif score > 0:
        verdict = Verdict.partial  # includes 100 points with a failed sample (D4)
    else:
        verdict = next(r.verdict for r in failed if r.ordinal != SAMPLE_GROUP)
    return SubmissionResult(SubmissionState.done, verdict, score, tuple(results))


_TRANSITIONS: dict[SubmissionState, frozenset[SubmissionState]] = {
    SubmissionState.queued: frozenset({SubmissionState.judging}),
    SubmissionState.judging: frozenset(
        {SubmissionState.done, SubmissionState.judge_error, SubmissionState.queued}
    ),
    SubmissionState.judge_error: frozenset({SubmissionState.queued}),  # rejudge
    SubmissionState.done: frozenset(),
    SubmissionState.not_judged: frozenset(),
}


def can_transition(current: SubmissionState, target: SubmissionState) -> bool:
    return SubmissionState(target) in _TRANSITIONS[SubmissionState(current)]


def check_transition(current: SubmissionState, target: SubmissionState) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"{current} -> {target} is not allowed")
