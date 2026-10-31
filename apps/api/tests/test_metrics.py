from datetime import UTC, datetime, timedelta

from app.modules.olympiad.metrics import AttemptRecord, ProblemRecord, compute_progress

NOW = datetime(2026, 10, 24, tzinfo=UTC)


def attempt(
    pid: str, verdict: str | None, *, hints: int = 0, days_ago: float = 0, state: str = "done"
) -> AttemptRecord:
    return AttemptRecord(pid, state, verdict, hints, NOW - timedelta(days=days_ago))


PROBLEMS = {
    "a": ProblemRecord("a", "easy", (("sorting", 1),)),
    "b": ProblemRecord("b", "medium", (("sorting", 3), ("greedy", 1))),
    "c": ProblemRecord("c", "intro", (("greedy", 1),)),
}
STAGES = {"sorting": 4, "greedy": 4}


def test_no_activity() -> None:
    p = compute_progress([], PROBLEMS, STAGES, NOW)
    assert p.hint_reliance_30d is None and p.median_attempts_to_accept is None
    assert p.demonstrated_topics == 0 and p.total_topics == 2 and p.pending_not_judged == 0


def test_higher_stage_evidence_demonstrates_lower_stages() -> None:
    p = compute_progress(
        [attempt("a", "accepted"), attempt("b", "accepted")], PROBLEMS, STAGES, NOW
    )
    sorting = p.stages["sorting"]
    assert [s.demonstrated for s in sorting] == [True, False, False, False]
    assert sorting[2].unaided_by_band == {"medium": 1}
    assert p.demonstrated_topics == 1


def test_any_unaided_accept_counts_as_unaided() -> None:
    p = compute_progress(
        [attempt("c", "accepted", hints=1, days_ago=2), attempt("c", "accepted", days_ago=1)],
        PROBLEMS, STAGES, NOW,
    )  # fmt: skip
    assert p.stages["greedy"][0].unaided_by_band == {"intro": 1}
    assert p.stages["greedy"][0].with_hints == 0
    assert p.hint_reliance_30d == 0.0


def test_attempts_to_accept_ignores_compile_errors_and_later_attempts() -> None:
    history = [
        attempt("a", "compile_error", days_ago=5),
        attempt("a", "wrong_answer", days_ago=4),
        attempt("a", "time_limit", days_ago=3),
        attempt("a", "accepted", days_ago=2),
        attempt("a", "wrong_answer", days_ago=1),
    ]
    assert compute_progress(history, PROBLEMS, STAGES, NOW).median_attempts_to_accept == 3.0


def test_hint_reliance_window_uses_first_accept() -> None:
    history = [
        attempt("a", "accepted", hints=2, days_ago=40),  # outside the 30-day window
        attempt("c", "accepted", hints=1, days_ago=3),
    ]
    assert compute_progress(history, PROBLEMS, STAGES, NOW).hint_reliance_30d == 1.0


def test_unjudged_and_unknown_problems_are_not_evidence() -> None:
    history = [
        attempt("a", None, state="not_judged"),
        attempt("a", None, state="judge_error"),
        attempt("zzz", "accepted"),
    ]
    p = compute_progress(history, PROBLEMS, STAGES, NOW)
    assert p.pending_not_judged == 1
    assert all(not s.unaided_by_band for s in p.stages["sorting"])
    assert p.median_attempts_to_accept is None


def test_an_accept_after_giving_up_is_never_unaided() -> None:  # DF1
    gave_up = AttemptRecord("c", "done", "accepted", 0, NOW, after_give_up=True)
    p = compute_progress([gave_up], PROBLEMS, STAGES, NOW)
    assert p.stages["greedy"][0].unaided_by_band == {}
    assert p.stages["greedy"][0].with_hints == 1
    assert p.hint_reliance_30d == 1.0


def test_an_earlier_unaided_accept_still_counts_after_a_give_up() -> None:
    before = attempt("c", "accepted", days_ago=2)
    after = AttemptRecord("c", "done", "accepted", 0, NOW, after_give_up=True)
    p = compute_progress([before, after], PROBLEMS, STAGES, NOW)
    assert p.stages["greedy"][0].unaided_by_band == {"intro": 1}
