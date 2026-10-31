"""Pure judging rules (Step 5.1): synthetic outputs only, no database and no code execution."""

from collections.abc import Sequence

import pytest

from app.modules.judge import core
from app.modules.judge.core import (
    GroupSpec,
    InvalidTransition,
    JudgeDataError,
    RunStatus,
    SubmissionResult,
    TestRun,
)
from app.modules.olympiad import content
from app.modules.olympiad.models import Scoring, SubmissionState, Verdict

S = SubmissionState


def ok(output: str = "3", time_ms: int | None = None, memory_kb: int | None = None) -> TestRun:
    return TestRun(RunStatus.completed, output, time_ms, memory_kb)


def run(status: RunStatus) -> TestRun:
    return TestRun(status)


SAMPLES = GroupSpec(0, 0, ("3",))
G1 = GroupSpec(1, 30, ("3", "3"))
G2 = GroupSpec(2, 70, ("3",))
GROUPS = (SAMPLES, G1, G2)


def judge(
    scoring: Scoring,
    samples: Sequence[TestRun] = (ok(),),
    g1: Sequence[TestRun] = (ok(), ok()),
    g2: Sequence[TestRun] = (ok(),),
) -> SubmissionResult:
    return core.judge_submission(scoring, GROUPS, {0: samples, 1: g1, 2: g2})


def summary(result: SubmissionResult) -> tuple[object, ...]:
    return (
        result.state,
        result.verdict,
        result.score,
        [(g.ordinal, g.verdict, g.score) for g in result.groups],
    )


# ------------------------------------------------------------------ checker


def test_checker_reuses_the_existing_token_comparison() -> None:
    assert core.check_output is not content.same_output  # a thin wrapper, not a copy
    for actual, expected in [("1 2\n", "1  2"), ("1\n2", "1 2"), ("1 2", "1 3"), ("", " \n")]:
        assert core.check_output(actual, expected) == content.same_output(actual, expected)
    assert core.check_output("3\n", " 3 ") and not core.check_output("3", "03")


# ------------------------------------------------------------------ groups (D1)


def test_group_passes_only_when_every_test_passes() -> None:
    result = core.judge_group(G1, [ok(time_ms=5, memory_kb=900), ok(time_ms=9, memory_kb=100)])
    assert (result.verdict, result.score, result.passed) == (Verdict.accepted, 30, True)
    assert (result.max_time_ms, result.max_memory_kb) == (9, 900)


@pytest.mark.parametrize(
    ("failure", "verdict"),
    [
        (ok("4"), Verdict.wrong_answer),
        (run(RunStatus.time_limit), Verdict.time_limit),
        (run(RunStatus.memory_limit), Verdict.memory_limit),
        (run(RunStatus.runtime_error), Verdict.runtime_error),
    ],
)
def test_group_verdict_is_its_first_failing_test(failure: TestRun, verdict: Verdict) -> None:
    result = core.judge_group(G1, [ok(), failure])  # the runs stop at the failure
    assert (result.verdict, result.score, result.passed) == (verdict, 0, False)
    first = core.judge_group(G1, [failure])
    assert first.verdict == verdict


def test_group_timing_is_unknown_when_not_reported() -> None:
    result = core.judge_group(G2, [ok()])
    assert (result.max_time_ms, result.max_memory_kb) == (None, None)


@pytest.mark.parametrize(
    ("group", "runs"),
    [
        (GroupSpec(1, 100, ()), []),  # an empty group never counts as passed
        (G1, []),  # no results
        (G1, [ok()]),  # a passing run but a test missing
        (G1, [ok(), ok(), ok()]),  # more results than tests
        (G1, [ok("4"), ok()]),  # results continue after the first failure
    ],
)
def test_invalid_group_data_fails_closed(group: GroupSpec, runs: list[TestRun]) -> None:
    with pytest.raises(JudgeDataError):
        core.judge_group(group, runs)


# ------------------------------------------------------------------ ioi scoring (D2, D4)


def test_ioi_all_groups_pass_is_accepted() -> None:
    assert summary(judge(Scoring.ioi)) == (
        S.done,
        Verdict.accepted,
        100,
        [(0, Verdict.accepted, 0), (1, Verdict.accepted, 30), (2, Verdict.accepted, 70)],
    )


def test_ioi_some_groups_pass_is_partial() -> None:
    result = judge(Scoring.ioi, g2=[run(RunStatus.time_limit)])
    assert summary(result) == (
        S.done,
        Verdict.partial,
        30,
        [(0, Verdict.accepted, 0), (1, Verdict.accepted, 30), (2, Verdict.time_limit, 0)],
    )


def test_ioi_no_points_takes_the_first_failing_scored_groups_verdict() -> None:
    result = judge(Scoring.ioi, g1=[ok(), run(RunStatus.memory_limit)], g2=[ok("9")])
    assert (result.verdict, result.score) == (Verdict.memory_limit, 0)


def test_ioi_failed_sample_blocks_accepted_but_never_costs_points() -> None:
    # Every scored group passes, one sample fails: partial with the full 100 points.
    result = judge(Scoring.ioi, samples=[ok("wrong")])
    assert summary(result) == (
        S.done,
        Verdict.partial,
        100,
        [(0, Verdict.wrong_answer, 0), (1, Verdict.accepted, 30), (2, Verdict.accepted, 70)],
    )


def test_ioi_failed_sample_with_no_points_reports_the_scored_failure() -> None:
    result = judge(Scoring.ioi, samples=[ok("x")], g1=[ok("x")], g2=[run(RunStatus.runtime_error)])
    assert (result.verdict, result.score) == (Verdict.wrong_answer, 0)


def test_problem_without_a_sample_group_is_judged_on_scored_groups() -> None:
    result = core.judge_submission(Scoring.ioi, (G1, G2), {1: [ok(), ok()], 2: [ok()]})
    assert (result.verdict, result.score) == (Verdict.accepted, 100)


# ------------------------------------------------------------------ binary scoring (D3)


def test_binary_all_groups_pass_is_accepted() -> None:
    assert (judge(Scoring.binary).verdict, judge(Scoring.binary).score) == (Verdict.accepted, 100)


def test_binary_any_failure_scores_zero_but_groups_keep_their_points() -> None:
    result = judge(Scoring.binary, g2=[ok("4")])
    assert summary(result) == (
        S.done,
        Verdict.wrong_answer,
        0,
        [(0, Verdict.accepted, 0), (1, Verdict.accepted, 30), (2, Verdict.wrong_answer, 0)],
    )


def test_binary_failed_sample_fails_the_submission() -> None:
    result = judge(Scoring.binary, samples=[run(RunStatus.runtime_error)])
    assert (result.verdict, result.score) == (Verdict.runtime_error, 0)


def test_binary_reports_the_first_failing_group_in_order() -> None:
    result = judge(Scoring.binary, g1=[run(RunStatus.time_limit)], g2=[ok("4")])
    assert result.verdict == Verdict.time_limit


# ------------------------------------------------------------------ compile and internal errors


def test_compile_error_is_done_with_no_group_results() -> None:
    assert summary(core.compile_error()) == (S.done, Verdict.compile_error, 0, [])


@pytest.mark.parametrize("scoring", list(Scoring))
@pytest.mark.parametrize("where", ["samples", "g1", "g2"])
def test_internal_error_anywhere_discards_all_results(scoring: Scoring, where: str) -> None:
    broken = {where: [run(RunStatus.internal_error)]}
    result = judge(scoring, **broken)
    # Never shown as the contestant's fault: no verdict, no score, no partial group results.
    assert summary(result) == (S.judge_error, None, None, [])


def test_internal_error_after_a_passing_test_still_discards_everything() -> None:
    result = judge(Scoring.ioi, g1=[ok(), run(RunStatus.internal_error)])
    assert summary(result) == (S.judge_error, None, None, [])


# ------------------------------------------------------------------ invalid problem data (D7)


@pytest.mark.parametrize(
    ("groups", "runs"),
    [
        ((SAMPLES, G1, G2), {0: [ok()], 1: [ok(), ok()]}),  # a group without results
        ((SAMPLES, G1, G2), {0: [ok()], 1: [ok(), ok()], 2: [ok()], 3: [ok()]}),  # unknown
        ((SAMPLES, G1, GroupSpec(2, 60, ("3",))), None),  # points do not sum to 100
        ((GroupSpec(0, 10, ("3",)), G1, G2), None),  # samples worth points
        ((SAMPLES,), {0: [ok()]}),  # no scored groups
        ((SAMPLES, G1, G1, G2), None),  # duplicate ordinals
        ((SAMPLES, G1, GroupSpec(2, 70, ())), None),  # an empty group
        ((GroupSpec(0, 0, ()), G1, G2), {0: [], 1: [ok(), ok()], 2: [ok()]}),  # empty samples
        ((SAMPLES, G1, GroupSpec(-1, 70, ("3",))), None),  # negative ordinal
    ],
)
def test_invalid_problem_data_fails_closed(
    groups: tuple[GroupSpec, ...], runs: dict[int, list[TestRun]] | None
) -> None:
    all_runs = runs or {g.ordinal: [ok()] * len(g.expected_outputs) for g in groups}
    for scoring in Scoring:
        with pytest.raises(JudgeDataError):
            core.judge_submission(scoring, groups, all_runs)


# ------------------------------------------------------------------ result invariants


@pytest.mark.parametrize(
    ("state", "verdict", "score", "groups"),
    [
        (S.done, None, 0, ()),  # done needs a verdict
        (S.done, Verdict.accepted, None, ()),  # and a score
        (S.done, Verdict.accepted, 101, ()),  # in range
        (S.judge_error, Verdict.wrong_answer, None, ()),  # a judge error has no verdict
        (S.judge_error, None, 0, ()),  # or score
        (
            S.judge_error,
            None,
            None,
            (core.GroupResult(1, 100, Verdict.accepted, 100, None, None),),
        ),  # or group results
        (S.queued, None, None, ()),  # only final judging states
    ],
)
def test_submission_result_matches_the_database_constraints(
    state: SubmissionState,
    verdict: Verdict | None,
    score: int | None,
    groups: tuple[core.GroupResult, ...],
) -> None:
    with pytest.raises(ValueError):
        SubmissionResult(state, verdict, score, groups)


# ------------------------------------------------------------------ state transitions (D8)

ALLOWED = {
    (S.queued, S.judging),
    (S.judging, S.done),
    (S.judging, S.judge_error),
    (S.judging, S.queued),
    (S.judge_error, S.queued),
}


@pytest.mark.parametrize("current", list(SubmissionState))
@pytest.mark.parametrize("target", list(SubmissionState))
def test_only_approved_transitions_are_allowed(
    current: SubmissionState, target: SubmissionState
) -> None:
    allowed = (current, target) in ALLOWED
    assert core.can_transition(current, target) is allowed
    if allowed:
        core.check_transition(current, target)
    else:
        with pytest.raises(InvalidTransition):
            core.check_transition(current, target)


def test_final_states_have_no_way_out() -> None:
    for state in (S.done, S.not_judged):
        assert not any(core.can_transition(state, t) for t in SubmissionState)
