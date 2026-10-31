"""One judge-worker iteration: sweep, claim, verify, execute elsewhere, aggregate, store.

The worker orchestrates only. It never runs, compiles or evaluates submitted code: that is
the `Executor`'s job, an isolated judge service (judge/judge0.py; tests use a fake). The
executor runs **one test per call** and receives the language, the source, the limits and
that test's **input** only. Expected outputs never leave the worker: it runs the tests of
each group in ordinal order, compares every output itself (`core.check_output`, the
platform's `content.same_output`) and stops the group at its first failure, a wrong answer
included, so no further test of that group is executed (D1).

Nothing here logs exception messages, tracebacks, source code, test data or compiler output:
every event goes through the queue's field allowlist (`queue.LOG_FIELDS`). Exceptions raised
by an executor are classified by type only and never formatted.

This is one iteration (`process_one`); the long-running loop is judge/runner.py and its
entry point scripts/judge_worker.py.
"""

import contextlib
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.core.config import Settings
from app.modules.judge import core, queue
from app.modules.judge import snapshot as snap
from app.modules.judge.core import (
    GroupSpec,
    JudgeDataError,
    RunStatus,
    SubmissionResult,
    TestRun,
)
from app.modules.judge.queue import (
    Claim,
    Failure,
    InvalidResult,
    QueueConfig,
    QueueDatabaseError,
)
from app.modules.olympiad.models import Language, SubmissionState

Heartbeat = Callable[[], bool]


class LeaseLost(Exception):
    """The lease is gone: stop. Raised by an executor when the heartbeat reports a loss while
    it waits, and by the worker itself when the heartbeat before a test fails."""


class JudgeUnavailable(Exception):
    """The judge service could not be reached (temporary: the job is released)."""


class JudgeTimeout(Exception):
    """The judge service did not answer in time, or a submission's tests ran past
    `JUDGE_MAX_JUDGING_SECONDS` (temporary: the job is released)."""


@dataclass(frozen=True)
class TestRequest:
    """One test, as an executor gets it: no expected output and no user, submission or
    problem identifier. Source and input are left out of repr()."""

    __test__ = False  # not a pytest class

    language: Language
    time_limit_ms: int
    memory_limit_mb: int
    source_code: str = field(repr=False)
    input: str = field(repr=False)


@dataclass(frozen=True)
class CompileFailure:
    message: str = field(repr=False)  # stored cleaned and bounded, never logged


@dataclass(frozen=True)
class Execution:
    """Built by the worker: per group ordinal, the runs of its tests in order, stopping
    after the first failure (so a group's last run is its only failing one)."""

    runs: Mapping[int, Sequence[TestRun]] = field(repr=False)


ExecutionOutcome = CompileFailure | Execution
TestOutcome = CompileFailure | TestRun


class Executor(Protocol):
    def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
        """Compile and run one test in isolation and return its actual output and status
        (never compared: the worker does that). Call `heartbeat()` regularly while
        waiting; when it returns False, stop and raise `LeaseLost`."""
        ...


class _InternalRun(Exception):
    """A run the judge itself failed (`RunStatus.internal_error`): stop executing."""


class _Stopping(Exception):
    """The worker is shutting down and the in-flight grace period is over: give the job
    back instead of finishing it."""


class Outcome(StrEnum):
    idle = "idle"  # nothing claimable
    finished = "finished"  # a verdict was stored
    failed = "failed"  # terminal judge_error
    released = "released"  # given back after a temporary failure (or judge_error at limit)
    lease_lost = "lease_lost"  # another attempt owns the job now; nothing was written
    database_error = "database_error"  # the queue could not reach the database
    unexpected = "unexpected"  # an unforeseen error, contained; the job was given back


@dataclass(frozen=True)
class WorkerConfig:
    queue: QueueConfig
    renew_seconds: int
    max_judging_seconds: int = 1800  # no new test starts after this; checked between tests

    @classmethod
    def from_settings(cls, settings: Settings, languages: Iterable[Language]) -> "WorkerConfig":
        """`languages`: those the executor can run (Judge0Config.language_ids)."""
        return cls(
            QueueConfig.from_settings(settings, languages),
            settings.judge_renew_seconds,
            settings.judge_max_judging_seconds,
        )


class _Lease:
    """The heartbeat given to the executor: renews when a renewal interval has passed and
    remembers a loss, so a lost lease is never reported as held again."""

    def __init__(
        self,
        factory: queue.SessionFactory,
        config: WorkerConfig,
        claim_: Claim,
        clock: Callable[[], float],
    ) -> None:
        self._factory, self._config, self._claim, self._clock = factory, config, claim_, clock
        self._renewed_at = clock()
        self.lost = False

    def __call__(self) -> bool:
        if self.lost:
            return False
        if self._clock() - self._renewed_at >= self._config.renew_seconds:
            if queue.renew(self._factory, self._config.queue, self._claim):
                self._renewed_at = self._clock()
            else:
                self.lost = True
        return not self.lost


def _check_inputs(job: queue.Job) -> list[GroupSpec]:
    """The groups to run, by ordinal. Raises `SnapshotError` when the expected outputs or
    the inputs do not cover exactly the snapshot's tests."""
    specs = snap.group_specs(job.snapshot, job.expected_outputs)
    if set(job.inputs) != set(job.expected_outputs):
        raise snap.SnapshotError("inputs do not match the snapshot's tests")
    return specs


def _never() -> bool:
    return False


def _execute(
    job: queue.Job,
    executor: Executor,
    heartbeat: Heartbeat,
    expired: Callable[[], bool] = _never,
    stopping: Callable[[], bool] = _never,
) -> ExecutionOutcome:
    """Run the job's tests one by one, group by group in ordinal order, and stop a group at
    its first failure: a run that did not complete, or completed with a wrong output (the
    expected outputs are compared here and never given to the executor).

    A compile failure on the first test ends everything as the student's compile error. One
    after a test has already run is the judge's fault (the same source compiled before) and
    raises `_InternalRun`, as does a judge internal error. Before each test, and whenever the
    executor beats the heart while it waits, `expired()` must be false (`JudgeTimeout`) and
    the heartbeat must hold (`LeaseLost`). Test data that does not fit the snapshot raises
    `SnapshotError` before anything runs. `stopping()` true at a beat raises `_Stopping`."""
    specs = _check_inputs(job)

    def beat() -> bool:
        # Given to the executor too, so a test still waiting on the judge also stops at the
        # deadline (the overrun is then at most one poll interval plus one HTTP timeout).
        if stopping():
            raise _Stopping
        if expired():
            raise JudgeTimeout
        return heartbeat()

    runs: dict[int, list[TestRun]] = {}
    executed = 0
    for spec in specs:
        ordinals = sorted(t.ordinal for t in job.snapshot.tests if t.group_ordinal == spec.ordinal)
        group_runs: list[TestRun] = []
        for ordinal, expected in zip(ordinals, spec.expected_outputs, strict=True):
            if not beat():
                raise LeaseLost
            executed += 1
            outcome = executor.run(
                TestRequest(
                    language=job.language,
                    time_limit_ms=job.snapshot.time_limit_ms,
                    memory_limit_mb=job.snapshot.memory_limit_mb,
                    source_code=job.source_code,
                    input=job.inputs[(spec.ordinal, ordinal)],
                ),
                beat,
            )
            if isinstance(outcome, CompileFailure):
                if executed > 1:  # never shown to the student as a compile error
                    raise _InternalRun
                return outcome
            if outcome.status == RunStatus.internal_error:
                raise _InternalRun
            group_runs.append(outcome)
            if outcome.status != RunStatus.completed or not core.check_output(
                outcome.output, expected
            ):
                break  # the rest of this group is not executed
        runs[spec.ordinal] = group_runs
    return Execution(runs)


def _aggregate(job: queue.Job, outcome: ExecutionOutcome) -> tuple[SubmissionResult, str | None]:
    """The verdict, from the judging core and the snapshot that was verified and hashed."""
    if isinstance(outcome, CompileFailure):
        return core.compile_error(), outcome.message
    specs = snap.group_specs(job.snapshot, job.expected_outputs)
    return core.judge_submission(job.snapshot.scoring, specs, outcome.runs), None


def _log(
    event: str, claim_: Claim, outcome: Outcome, started: float, clock: Callable[[], float]
) -> None:
    queue._log(  # the queue's allowlisted logger
        logging.INFO,
        event,
        submission=claim_.submission_id,
        attempt=claim_.token,
        category=outcome.value,
        duration_ms=int((clock() - started) * 1000),
    )


def process_one(
    factory: queue.SessionFactory,
    executor: Executor,
    config: WorkerConfig,
    clock: Callable[[], float] = time.monotonic,
    stopping: Callable[[], bool] = _never,
) -> Outcome:
    """Recover expired leases, then judge at most one submission. Every write is fenced by
    the claim (queue.py), so a lost lease never stores anything. `stopping()` is asked at
    every heartbeat: once true, the job is released (`worker_stopping`) instead of finished.

    No ordinary exception escapes once a job is claimed: an unforeseen one is caught
    without being bound, logged as the outcome `unexpected` only (never its message or
    traceback, whose frames hold the job's source and test data), and the job is given
    back with a best-effort fenced release. `BaseException` (interrupts, exits) is not
    caught."""
    try:
        queue.sweep(factory, config.queue)
        claimed = queue.claim(factory, config.queue)
    except QueueDatabaseError:
        return Outcome.database_error  # already logged, without the driver's message
    if claimed is None:
        return Outcome.idle
    started = clock()
    outcome = _contained(factory, executor, config, claimed, clock, stopping)
    _log("judge.processed", claimed, outcome, started, clock)
    return outcome


def _contained(
    factory: queue.SessionFactory,
    executor: Executor,
    config: WorkerConfig,
    claimed: Claim,
    clock: Callable[[], float],
    stopping: Callable[[], bool],
) -> Outcome:
    contained = False
    try:
        return _process(factory, executor, config, claimed, clock, stopping)
    except QueueDatabaseError:
        return Outcome.database_error  # already logged, without the driver's message
    except Exception:  # unbound on purpose: the error and its frames are dropped here
        contained = True
    if not contained:  # unreachable: every path above returns
        raise RuntimeError("worker outcome missing")
    # Outside the handler, so nothing is chained. A failing release must not leak either:
    # the lease then simply expires and the sweeper recovers the job.
    with contextlib.suppress(Exception):
        _release(factory, config, claimed, Failure.unexpected)
    return Outcome.unexpected


def _process(
    factory: queue.SessionFactory,
    executor: Executor,
    config: WorkerConfig,
    claimed: Claim,
    clock: Callable[[], float],
    stopping: Callable[[], bool] = _never,
) -> Outcome:
    loaded = queue.load_job(factory, claimed)
    if loaded is None:
        return Outcome.lease_lost
    if isinstance(loaded, Failure):  # NULL, invalid or stale snapshot: never executed
        return Outcome.failed if queue.fail(factory, claimed, loaded) else Outcome.lease_lost
    job = loaded
    try:
        _check_inputs(job)
    except snap.SnapshotError:  # test data that does not fit the snapshot: nothing runs
        return (
            Outcome.failed
            if queue.fail(factory, claimed, Failure.invalid_test_data)
            else Outcome.lease_lost
        )

    lease = _Lease(factory, config, claimed, clock)
    started = clock()
    try:
        result = _execute(
            job,
            executor,
            lease,
            lambda: clock() - started >= config.max_judging_seconds,
            stopping,
        )
    except _InternalRun:  # an internal error in the judge: retried, never a verdict
        return _release(factory, config, claimed, Failure.judge_internal)
    except _Stopping:  # shutting down: given back for another worker (counts as an attempt)
        return _release(factory, config, claimed, Failure.worker_stopping)
    except LeaseLost:
        return Outcome.lease_lost
    except QueueDatabaseError:
        raise  # a heartbeat could not reach the database
    except JudgeUnavailable:
        return _release(factory, config, claimed, Failure.judge_unavailable)
    except JudgeTimeout:
        return _release(factory, config, claimed, Failure.judge_timeout)
    except Exception:  # classified by type only; the message is never used
        return _release(factory, config, claimed, Failure.unexpected)
    if lease.lost:  # the executor ignored the heartbeat: its late result is discarded
        return Outcome.lease_lost

    try:
        verdict, compile_message = _aggregate(job, result)
    except (JudgeDataError, snap.SnapshotError):
        # Runs that do not fit the verified snapshot: a judge fault, never a verdict.
        return (
            Outcome.failed
            if queue.fail(factory, claimed, Failure.invalid_test_data)
            else (Outcome.lease_lost)
        )
    if verdict.state == SubmissionState.judge_error:  # an internal error in the judge
        return _release(factory, config, claimed, Failure.judge_internal)
    try:
        stored = queue.finish(factory, job, verdict, compile_message)
    except InvalidResult:
        # A result that does not fit the verified snapshot: a judge fault, never a verdict.
        return (
            Outcome.failed
            if queue.fail(factory, claimed, Failure.invalid_test_data)
            else Outcome.lease_lost
        )
    return Outcome.finished if stored else Outcome.lease_lost


def _release(
    factory: queue.SessionFactory, config: WorkerConfig, claimed: Claim, category: Failure
) -> Outcome:
    state = queue.release(factory, config.queue, claimed, category)
    if state is None:
        return Outcome.lease_lost
    return Outcome.released
