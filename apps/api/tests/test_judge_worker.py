"""Step 5.3.4: one judge-worker iteration (`worker.process_one`).

The executor here is a fake that returns a canned result per test input: it never compiles
or runs anything (sequential execution without a database: test_judge_sequential.py). The
database is the scratch database of test_judge_queue.py (real, independently committing
connections), so leases, fencing and rollback are PostgreSQL's own behaviour.
"""

import ast
import dataclasses
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.judge import queue, worker
from app.modules.judge.core import GroupResult, RunStatus, SubmissionResult, TestRun
from app.modules.judge.queue import QueueConfig
from app.modules.judge.worker import (
    CompileFailure,
    Execution,
    Heartbeat,
    Outcome,
    TestOutcome,
    TestRequest,
    WorkerConfig,
)
from app.modules.olympiad.models import Language, SubmissionState, Verdict
from tests.test_judge_queue import (  # noqa: F401
    HIDDEN,
    SOURCE,
    _expire,
    _results,
    _row,
    _seed,
    engine,
    factory,
)

S = SubmissionState
CONFIG = WorkerConfig(
    QueueConfig(lease_seconds=120, max_attempts=3, languages=frozenset(Language)), renew_seconds=30
)
EXPECTED = {(0, 1): "3\n", (1, 1): "secret\n"}  # what the seeded problem expects


class FakeExecutor:
    """Returns a canned outcome per test input (or one outcome for every test, or raises)
    and records what it was given. It runs nothing. `during` is called on the first run."""

    def __init__(
        self,
        outcome: Mapping[str, TestOutcome] | TestOutcome | None = None,
        raises: BaseException | None = None,
        during: Callable[[Heartbeat], None] | None = None,
    ) -> None:
        self.outcome, self.raises, self.during = outcome, raises, during
        self.requests: list[TestRequest] = []

    def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
        self.requests.append(request)
        if self.during is not None and len(self.requests) == 1:
            self.during(heartbeat)
        if self.raises is not None:
            raise self.raises
        assert self.outcome is not None
        if isinstance(self.outcome, Mapping):
            return self.outcome[request.input]
        return self.outcome


def _ok(output: str) -> TestRun:
    return TestRun(RunStatus.completed, output)


ACCEPTED = {"1 2\n": _ok("3\n"), HIDDEN: _ok("secret\n")}  # by the seeded inputs


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ------------------------------------------------------------------ static safety


def test_worker_module_cannot_execute_code() -> None:
    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    banned_modules = {"subprocess", "multiprocessing", "ctypes", "importlib", "pty", "shutil", "os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned_modules
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_modules
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"exec", "eval", "compile", "__import__"}


def test_request_and_outcome_reprs_hide_source_inputs_and_compiler_output() -> None:
    request = TestRequest(Language.cpp, 1000, 256, "SENTINEL-SRC-1a", "SENTINEL-IN-2b")
    failure = CompileFailure("SENTINEL-CC-3c")
    shown = repr(request) + str(request) + repr(failure) + repr(Execution({1: []}))
    for secret in ("SENTINEL-SRC-1a", "SENTINEL-IN-2b", "SENTINEL-CC-3c"):
        assert secret not in shown
    assert "cpp" in shown and "1000" in shown


# ------------------------------------------------------------------ the happy paths


def test_idle_without_queued_work(factory: sessionmaker[Session]) -> None:  # noqa: F811
    fake = FakeExecutor(ACCEPTED)
    assert worker.process_one(factory, fake, CONFIG) == Outcome.idle
    assert fake.requests == []


def test_accepted_result_is_stored_and_the_executor_got_inputs_only(
    factory: sessionmaker[Session],  # noqa: F811
) -> None:
    [submission] = _seed(factory)
    fake = FakeExecutor(ACCEPTED)
    assert worker.process_one(factory, fake, CONFIG) == Outcome.finished
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.lease_expires_at) == (
        S.done,
        Verdict.accepted,
        100,
        None,
    )
    assert [(r[0], r[2], r[3]) for r in _results(factory, submission)] == [
        (0, Verdict.accepted, 0),
        (1, Verdict.accepted, 100),
    ]
    assert [r.input for r in fake.requests] == ["1 2\n", HIDDEN]  # one test per call, in order
    for request in fake.requests:
        assert request.source_code == SOURCE and request.language == Language.python
        assert (request.time_limit_ms, request.memory_limit_mb) == (1000, 256)
        assert not hasattr(request, "expected_output")  # expected outputs stay in the worker
        assert "secret\n" not in repr(request.__dict__.values())


def test_wrong_answer_is_judged_by_the_core(factory: sessionmaker[Session]) -> None:  # noqa: F811
    [submission] = _seed(factory)
    fake = FakeExecutor({"1 2\n": _ok("3\n"), HIDDEN: _ok("not it\n")})
    assert worker.process_one(factory, fake, CONFIG) == Outcome.finished
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, Verdict.wrong_answer, 0)
    assert [(r[0], r[2], r[3]) for r in _results(factory, submission)] == [
        (0, Verdict.accepted, 0),
        (1, Verdict.wrong_answer, 0),
    ]


def test_compile_failure_on_the_first_test_runs_nothing_else(
    factory: sessionmaker[Session],  # noqa: F811
) -> None:
    [submission] = _seed(factory)
    fake = FakeExecutor(CompileFailure("bad"))
    assert worker.process_one(factory, fake, CONFIG) == Outcome.finished
    assert len(fake.requests) == 1
    assert _row(factory, submission).verdict == Verdict.compile_error


def test_compile_failure_after_a_test_ran_is_released_not_stored(
    factory: sessionmaker[Session],  # noqa: F811
) -> None:
    [submission] = _seed(factory)
    fake = FakeExecutor({"1 2\n": _ok("3\n"), HIDDEN: CompileFailure("COMPILER-FLAKE-2e")})
    assert worker.process_one(factory, fake, CONFIG) == Outcome.released
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.compile_message) == (S.queued, None, None, None)
    assert row.judge_attempts == 1 and _results(factory, submission) == []


def test_compile_failure_stores_a_cleaned_bounded_message(factory: sessionmaker[Session]) -> None:  # noqa: F811
    [submission] = _seed(factory)
    fake = FakeExecutor(CompileFailure("error:\x00 bad\n" + "y" * 5000))
    assert worker.process_one(factory, fake, CONFIG) == Outcome.finished
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, Verdict.compile_error, 0)
    assert row.compile_message is not None and len(row.compile_message) == 4096
    assert "\x00" not in row.compile_message and _results(factory, submission) == []


# ------------------------------------------------------------------ snapshots: never executed


@pytest.mark.parametrize("defect", ["null_hash", "stale"])
def test_unverifiable_or_stale_snapshot_fails_without_executing(
    factory: sessionmaker[Session],  # noqa: F811
    defect: str,
) -> None:
    [submission] = _seed(factory, v1=None if defect == "null_hash" else "0123456789abcdef" * 4)
    if defect == "stale":
        with factory() as db, db.begin():
            db.execute(text("UPDATE problems SET memory_limit_mb = 512"))
    fake = FakeExecutor(ACCEPTED)
    assert worker.process_one(factory, fake, CONFIG) == Outcome.failed
    assert fake.requests == []  # the executor was never called
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judge_error, None, None)
    assert _results(factory, submission) == []


def test_inputs_that_do_not_fit_the_snapshot_fail_without_executing(
    factory: sessionmaker[Session],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [submission] = _seed(factory)
    load_job = queue.load_job

    def without_the_sample_input(*args: Any) -> Any:
        job = load_job(*args)
        assert isinstance(job, queue.Job)
        inputs = {k: v for k, v in job.inputs.items() if k != (0, 1)}
        return dataclasses.replace(job, inputs=inputs)

    monkeypatch.setattr(queue, "load_job", without_the_sample_input)
    fake = FakeExecutor(ACCEPTED)
    assert worker.process_one(factory, fake, CONFIG) == Outcome.failed
    assert fake.requests == []
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judge_error, None, None)
    assert _results(factory, submission) == []


# ------------------------------------------------------------------ temporary failures


@pytest.mark.parametrize(
    "raised", [worker.JudgeUnavailable(), worker.JudgeTimeout(), RuntimeError("boom")]
)
def test_temporary_failures_release_until_the_limit(
    factory: sessionmaker[Session],  # noqa: F811
    raised: BaseException,
) -> None:
    [submission] = _seed(factory)
    fake = FakeExecutor(raises=raised)
    for attempt in (1, 2):
        assert worker.process_one(factory, fake, CONFIG) == Outcome.released
        row = _row(factory, submission)
        assert (row.state, row.judge_attempts, row.lease_expires_at) == (S.queued, attempt, None)
    assert worker.process_one(factory, fake, CONFIG) == Outcome.released
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.judge_attempts) == (S.judge_error, None, None, 3)
    assert worker.process_one(factory, fake, CONFIG) == Outcome.idle


def test_internal_judge_error_is_released_not_shown_as_a_verdict(
    factory: sessionmaker[Session],  # noqa: F811
) -> None:
    [submission] = _seed(factory)
    broken = FakeExecutor({"1 2\n": _ok("3\n"), HIDDEN: TestRun(RunStatus.internal_error)})
    assert worker.process_one(factory, broken, CONFIG) == Outcome.released
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.queued, None, None)
    assert _results(factory, submission) == []


# ------------------------------------------------------------------ leases


def test_heartbeat_renews_the_lease_once_an_interval_has_passed(
    factory: sessionmaker[Session],  # noqa: F811
) -> None:
    [submission] = _seed(factory)
    clock = Clock()
    seen: list[Any] = []

    def during(heartbeat: Heartbeat) -> None:
        with factory() as db, db.begin():  # shorten the lease so a renewal is visible
            db.execute(
                text(
                    "UPDATE submissions SET lease_expires_at = clock_timestamp() + "
                    "interval '10 seconds' WHERE id = :id"
                ),
                {"id": submission},
            )
        assert heartbeat()  # too early: no renewal
        seen.append(_row(factory, submission).lease_expires_at)
        clock.now += 30
        assert heartbeat()  # due: renewed to a full lease
        seen.append(_row(factory, submission).lease_expires_at)

    fake = FakeExecutor(ACCEPTED, during=during)
    assert worker.process_one(factory, fake, CONFIG, clock=clock) == Outcome.finished
    assert seen[1] > seen[0]


def test_lost_lease_stops_the_executor_and_writes_nothing(factory: sessionmaker[Session]) -> None:  # noqa: F811
    [submission] = _seed(factory)
    clock = Clock()

    def during(heartbeat: Heartbeat) -> None:
        _expire(factory, submission)  # the lease ran out while judging
        clock.now += 30
        if not heartbeat():
            raise worker.LeaseLost

    fake = FakeExecutor(ACCEPTED, during=during)
    assert worker.process_one(factory, fake, CONFIG, clock=clock) == Outcome.lease_lost
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judging, None, None)
    assert _results(factory, submission) == []
    # The sweeper recovers it for the next attempt.
    assert worker.process_one(factory, FakeExecutor(ACCEPTED), CONFIG) == Outcome.finished
    assert _row(factory, submission).judge_attempts == 2


def test_late_result_after_an_ignored_lost_heartbeat_is_discarded(
    factory: sessionmaker[Session],  # noqa: F811
) -> None:
    [submission] = _seed(factory)
    clock = Clock()

    def during(heartbeat: Heartbeat) -> None:
        _expire(factory, submission)
        clock.now += 30
        assert not heartbeat()
        assert not heartbeat()  # a loss is remembered

    fake = FakeExecutor(ACCEPTED, during=during)  # returns a result anyway
    assert worker.process_one(factory, fake, CONFIG, clock=clock) == Outcome.lease_lost
    assert _row(factory, submission).state == S.judging and _results(factory, submission) == []


def test_lease_expiring_without_a_heartbeat_still_blocks_the_write(
    factory: sessionmaker[Session],  # noqa: F811
) -> None:
    [submission] = _seed(factory)
    fake = FakeExecutor(ACCEPTED, during=lambda _heartbeat: _expire(factory, submission))
    assert worker.process_one(factory, fake, CONFIG) == Outcome.lease_lost  # finish is fenced
    assert _row(factory, submission).state == S.judging and _results(factory, submission) == []


# ------------------------------------------------------------------ logs and database errors


def test_logs_carry_no_source_tests_compiler_output_or_exception_text(
    factory: sessionmaker[Session],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed(factory, 3)
    secret_error = "EXCEPTION-TEXT-9f2"
    with caplog.at_level(logging.DEBUG):
        worker.process_one(factory, FakeExecutor(ACCEPTED), CONFIG)
        worker.process_one(factory, FakeExecutor(CompileFailure("COMPILER-OUT-4d")), CONFIG)
        worker.process_one(factory, FakeExecutor(raises=RuntimeError(secret_error)), CONFIG)
    judging = [r for r in caplog.records if r.name == "kyrgyzstem.judging"]
    assert {r.getMessage() for r in judging} >= {"judge.claimed", "judge.processed"}
    for record in judging:
        assert set(record.__dict__["fields"]) <= queue.LOG_FIELDS
        assert record.exc_info is None  # no traceback, so no frames or locals
    everything = "".join(r.getMessage() + repr(r.__dict__.get("fields")) for r in caplog.records)
    for secret in (SOURCE, HIDDEN, "secret\n", "COMPILER-OUT-4d", secret_error):
        assert secret not in everything


def test_database_errors_end_the_iteration_without_raw_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    password = "NOT-A-REAL-PASSWORD-77"
    broken = create_engine(
        f"postgresql+psycopg://nobody:{password}@127.0.0.1:1/none",
        connect_args={"connect_timeout": 2},
    )
    try:
        with caplog.at_level(logging.DEBUG):
            outcome = worker.process_one(sessionmaker(bind=broken), FakeExecutor(ACCEPTED), CONFIG)
    finally:
        broken.dispose()
    assert outcome == Outcome.database_error
    assert all(password not in r.getMessage() for r in caplog.records)


def test_worker_config_comes_from_settings() -> None:
    from app.core.config import Settings

    settings = Settings(_env_file=None, judge_renew_seconds=20, judge_max_judging_seconds=900)
    # The queue claims only the languages the worker was given (its Judge0 language ids).
    assert WorkerConfig.from_settings(settings, [Language.cpp]) == WorkerConfig(
        QueueConfig(120, 3, frozenset({Language.cpp})), 20, 900
    )


# ------------------------------------------------------------------ containment (R1)

MARKER = "UNEXPECTED-MARKER-6b1f"


def _raise(exc: BaseException) -> Callable[..., Any]:
    def raiser(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return raiser


def _assert_no_leak(caplog: pytest.LogCaptureFixture, *markers: str) -> None:
    for record in caplog.records:
        assert record.exc_info is None  # no traceback, so no frames or locals
        text_ = record.getMessage() + repr(record.__dict__.get("fields"))
        for marker in (SOURCE, HIDDEN, *markers):
            assert marker not in text_


@pytest.mark.parametrize("where", ["load_job", "aggregate", "finish"])
def test_unexpected_errors_are_contained_and_the_job_released(
    factory: sessionmaker[Session],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    where: str,
) -> None:
    [submission] = _seed(factory)
    if where == "aggregate":
        monkeypatch.setattr(worker, "_aggregate", _raise(ValueError(MARKER)))
    else:
        monkeypatch.setattr(queue, where, _raise(ValueError(MARKER)))
    with caplog.at_level(logging.DEBUG):
        outcome = worker.process_one(factory, FakeExecutor(ACCEPTED), CONFIG)  # never raises
    assert outcome == Outcome.unexpected
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.lease_expires_at) == (S.queued, None, None, None)
    assert _results(factory, submission) == []
    events = {
        (r.getMessage(), r.__dict__["fields"].get("category"))
        for r in caplog.records
        if r.name == "kyrgyzstem.judging"
    }
    assert ("judge.released", "unexpected") in events
    assert ("judge.processed", "unexpected") in events
    _assert_no_leak(caplog, MARKER)


def test_a_failing_release_does_not_leak_or_escape(
    factory: sessionmaker[Session],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [submission] = _seed(factory)
    monkeypatch.setattr(queue, "load_job", _raise(ValueError(MARKER)))
    monkeypatch.setattr(queue, "release", _raise(RuntimeError("RELEASE-MARKER-0c3a")))
    with caplog.at_level(logging.DEBUG):
        assert worker.process_one(factory, FakeExecutor(ACCEPTED), CONFIG) == Outcome.unexpected
    # Not released: the lease stays and expires; the sweeper then recovers the job.
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judging, None, None)
    _assert_no_leak(caplog, MARKER, "RELEASE-MARKER-0c3a")
    _expire(factory, submission)
    assert worker.process_one(factory, FakeExecutor(ACCEPTED), CONFIG) == Outcome.unexpected
    assert _row(factory, submission).judge_attempts == 2  # swept and claimed again


def test_base_exceptions_are_not_swallowed(
    factory: sessionmaker[Session],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(factory)
    monkeypatch.setattr(queue, "load_job", _raise(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        worker.process_one(factory, FakeExecutor(ACCEPTED), CONFIG)


def test_an_invalid_result_ends_as_invalid_test_data(
    factory: sessionmaker[Session],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [submission] = _seed(factory)
    bad = SubmissionResult(  # the sample group is missing: does not fit the snapshot
        S.done, Verdict.accepted, 100, (GroupResult(1, 100, Verdict.accepted, 100, None, None),)
    )
    monkeypatch.setattr(worker, "_aggregate", lambda _job, _outcome: (bad, None))
    with caplog.at_level(logging.INFO, logger="kyrgyzstem.judging"):
        assert worker.process_one(factory, FakeExecutor(ACCEPTED), CONFIG) == Outcome.failed
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judge_error, None, None)
    assert _results(factory, submission) == []
    failed = [r for r in caplog.records if r.getMessage() == "judge.failed"]
    assert [r.__dict__["fields"]["category"] for r in failed] == ["invalid_test_data"]
