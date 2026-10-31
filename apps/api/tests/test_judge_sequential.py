"""Sequential test execution in the worker (`worker._execute`, `worker._process`), without a
database.

The worker runs one test per executor call, in group and test order, compares outputs itself
and stops a group at its first failure. Executors here are fakes, or the Judge0 adapter over
`httpx.MockTransport`: nothing is compiled or run and nothing reaches a network.
"""

import base64
import dataclasses
import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.judge import queue, worker
from app.modules.judge import snapshot as snap
from app.modules.judge.core import RunStatus, SubmissionResult, TestRun
from app.modules.judge.judge0 import Judge0Executor
from app.modules.judge.queue import Claim, Failure, Job, QueueConfig
from app.modules.judge.worker import (
    CompileFailure,
    Execution,
    Heartbeat,
    LeaseLost,
    TestOutcome,
    TestRequest,
)
from app.modules.olympiad.models import Language, Scoring, SubmissionState, Verdict
from tests.test_judge0_adapter import ALLOWED_KEYS, TOKEN, URL, FakeJudge0, config

SOURCE = "SOURCE-SENTINEL-7c2"
# Samples (group 0), then 40 and 60 points: (group, test) -> input / expected output.
TESTS = [(0, 1), (0, 2), (1, 1), (1, 2), (1, 3), (2, 1), (2, 2)]
INPUTS = {key: f"in-{key[0]}-{key[1]}" for key in TESTS}
EXPECTED = {key: f"EXPECTED-{key[0]}-{key[1]}" for key in TESTS}


def job(scoring: Scoring = Scoring.ioi, inputs: Mapping[tuple[int, int], str] = INPUTS) -> Job:
    snapshot = snap.JudgingSnapshot(
        tests_hash_v1="0" * 64,
        scoring=scoring,
        time_limit_ms=1000,
        memory_limit_mb=256,
        groups=(snap.SnapshotGroup(0, 0), snap.SnapshotGroup(1, 40), snap.SnapshotGroup(2, 60)),
        tests=tuple(snap.SnapshotTest(g, t, g == 0) for g, t in TESTS),
    )
    return Job(
        claim=Claim(uuid.uuid4(), 1),
        language=Language.python,
        source_code=SOURCE,
        snapshot=snapshot,
        inputs=inputs,
        expected_outputs=EXPECTED,
        group_ids={g: uuid.uuid4() for g in (0, 1, 2)},
    )


class FakeExecutor:
    """Answers by input: the expected output unless `answers` says otherwise. Runs nothing."""

    def __init__(self, answers: Mapping[tuple[int, int], TestOutcome] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[TestRequest] = []

    def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
        self.requests.append(request)
        [key] = [k for k, v in INPUTS.items() if v == request.input]
        return self.answers.get(key, TestRun(RunStatus.completed, EXPECTED[key]))

    @property
    def ran(self) -> list[tuple[int, int]]:
        return [k for r in self.requests for k, v in INPUTS.items() if v == r.input]


def alive() -> bool:
    return True


def execute(fake: FakeExecutor, j: Job | None = None, heartbeat: Heartbeat = alive) -> Execution:
    outcome = worker._execute(j or job(), fake, heartbeat)
    assert isinstance(outcome, Execution)
    return outcome


def verdict(j: Job, outcome: Execution) -> tuple[Verdict | None, int | None, list[Verdict]]:
    result, _ = worker._aggregate(j, outcome)
    return result.verdict, result.score, [g.verdict for g in result.groups]


WRONG = TestRun(RunStatus.completed, "not it")


# ------------------------------------------------------------------ the order of execution


def test_correct_answers_run_every_test_in_order() -> None:
    fake = FakeExecutor()
    outcome = execute(fake)
    assert fake.ran == TESTS
    assert {g: len(r) for g, r in outcome.runs.items()} == {0: 2, 1: 3, 2: 2}
    assert verdict(job(), outcome) == (Verdict.accepted, 100, [Verdict.accepted] * 3)


def test_a_wrong_answer_on_the_first_test_stops_the_group() -> None:
    fake = FakeExecutor({(1, 1): WRONG})
    outcome = execute(fake)
    assert fake.ran == [(0, 1), (0, 2), (1, 1), (2, 1), (2, 2)]  # (1, 2) and (1, 3) never ran
    assert outcome.runs[1] == [WRONG]
    assert verdict(job(), outcome) == (
        Verdict.partial,
        60,
        [Verdict.accepted, Verdict.wrong_answer, Verdict.accepted],
    )


def test_no_test_after_a_wrong_answer_is_executed_or_aggregated() -> None:
    fake = FakeExecutor({(1, 2): WRONG})
    outcome = execute(fake)
    assert (1, 3) not in fake.ran
    assert [r.output for r in outcome.runs[1]] == ["EXPECTED-1-1", "not it"]
    assert verdict(job(), outcome)[2][1] == Verdict.wrong_answer


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RunStatus.runtime_error, Verdict.runtime_error),
        (RunStatus.time_limit, Verdict.time_limit),
        (RunStatus.memory_limit, Verdict.memory_limit),
    ],
)
def test_an_execution_error_stops_the_group(status: RunStatus, expected: Verdict) -> None:
    fake = FakeExecutor({(2, 1): TestRun(status)})
    outcome = execute(fake)
    assert (2, 2) not in fake.ran
    assert verdict(job(), outcome) == (
        Verdict.partial,
        40,
        [Verdict.accepted, Verdict.accepted, expected],
    )


def test_a_failing_group_does_not_stop_the_next_group() -> None:
    fake = FakeExecutor({(0, 1): WRONG, (1, 1): WRONG})  # samples and group 1 fail
    outcome = execute(fake)
    assert fake.ran == [(0, 1), (1, 1), (2, 1), (2, 2)]
    assert verdict(job(), outcome)[:2] == (Verdict.partial, 60)


def test_a_failed_sample_with_full_points_is_partial() -> None:  # D4
    outcome = execute(FakeExecutor({(0, 2): WRONG}))
    assert verdict(job(), outcome)[:2] == (Verdict.partial, 100)


def test_binary_scoring_after_a_stopped_group() -> None:
    j = job(Scoring.binary)
    outcome = execute(FakeExecutor({(2, 1): TestRun(RunStatus.time_limit)}), j)
    assert verdict(j, outcome)[:2] == (Verdict.time_limit, 0)


def test_outputs_are_compared_token_wise() -> None:
    fake = FakeExecutor({(1, 1): TestRun(RunStatus.completed, "  EXPECTED-1-1 \n\n")})
    outcome = execute(fake)
    assert len(outcome.runs[1]) == 3  # same tokens: accepted, the group goes on


# ------------------------------------------------------------------ stopping everything


def test_a_compile_failure_ends_execution_at_once() -> None:
    fake = FakeExecutor({(0, 1): CompileFailure("bad")})
    outcome = worker._execute(job(), fake, alive)
    assert outcome == CompileFailure("bad") and fake.ran == [(0, 1)]
    result, message = worker._aggregate(job(), outcome)
    assert (result.verdict, result.score, message) == (Verdict.compile_error, 0, "bad")


def test_a_judge_internal_error_stops_everything() -> None:
    fake = FakeExecutor({(1, 2): TestRun(RunStatus.internal_error)})
    with pytest.raises(worker._InternalRun):
        worker._execute(job(), fake, alive)
    assert fake.ran == [(0, 1), (0, 2), (1, 1), (1, 2)]


def test_a_heartbeat_is_checked_before_every_test() -> None:
    beats: list[int] = []

    def heartbeat() -> bool:
        beats.append(len(fake.requests))
        return len(fake.requests) < 3  # the lease is lost after three tests

    fake = FakeExecutor()
    with pytest.raises(LeaseLost):
        worker._execute(job(), fake, heartbeat)
    assert fake.ran == [(0, 1), (0, 2), (1, 1)]
    assert beats == [0, 1, 2, 3]


def test_a_lease_lost_before_the_first_test_runs_nothing() -> None:
    fake = FakeExecutor()
    with pytest.raises(LeaseLost):
        worker._execute(job(), fake, lambda: False)
    assert fake.requests == []


def test_inputs_that_do_not_fit_the_snapshot_run_nothing() -> None:
    fake = FakeExecutor()
    inputs = {k: v for k, v in INPUTS.items() if k != (2, 2)}
    with pytest.raises(snap.SnapshotError):
        worker._execute(job(inputs=inputs), fake, alive)
    assert fake.requests == []


# ------------------------------------------------------------------ through the Judge0 adapter


def _stdout(answers: Mapping[str, str]) -> Callable[[str], dict[str, object]]:
    def outcome(stdin: str) -> dict[str, object]:
        key = next(k for k, v in INPUTS.items() if v == stdin)
        out = answers.get(stdin, EXPECTED[key])
        return {"status_id": 3, "stdout": base64.b64encode(out.encode()).decode()}

    return outcome


def adapter(fake: FakeJudge0) -> Judge0Executor:
    client = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url=URL)
    return Judge0Executor(config(), client, sleep=lambda _s: None, clock=lambda: 0.0)


ENCODED = {"source_code", "stdin"}  # the base64 fields of a submission
HEADERS = {"host", "accept", "accept-encoding", "connection", "user-agent", "x-auth-token"}
BODY_HEADERS = {"content-length", "content-type"}


def _plaintext(request: httpx.Request) -> list[str]:
    """Everything a request carries, as text: URL parameters, header values and, for a
    submission, every field with the base64 ones decoded. A value hidden inside a longer
    field shows up here, whatever its offset in the encoding."""
    texts = [request.url.path, *request.url.params.values(), *request.headers.values()]
    if request.content:
        texts.append(request.content.decode())
        for item in json.loads(request.content)["submissions"]:
            for key, value in item.items():
                texts.append(base64.b64decode(value).decode() if key in ENCODED else str(value))
    return texts


def _leaks(request: httpx.Request) -> bool:
    return any(e in text for text in _plaintext(request) for e in EXPECTED.values())


def test_the_leak_check_finds_an_expected_output_inside_a_larger_encoded_field() -> None:
    hidden = "prefix-" + EXPECTED[(1, 2)] + "-suffix"  # mid-field, misaligned in base64
    body = {"submissions": [{"source_code": base64.b64encode(b"x").decode(), "stdin": ""}]}
    body["submissions"][0]["stdin"] = base64.b64encode(hidden.encode()).decode()
    assert base64.b64encode(EXPECTED[(1, 2)].encode()) not in json.dumps(body).encode()
    assert _leaks(httpx.Request("POST", URL + "/submissions/batch", json=body))
    assert not _leaks(httpx.Request("GET", URL + "/submissions/batch?tokens=t"))


def test_judge0_gets_one_input_per_test_and_never_an_expected_output() -> None:
    fake = FakeJudge0(_stdout({"in-1-1": "wrong"}))
    outcome = worker._execute(job(), adapter(fake), alive)
    assert isinstance(outcome, Execution)
    stdins = []
    for post in fake.posts:
        assert post.method == "POST" and post.url.path == "/submissions/batch"
        assert dict(post.url.params) == {"base64_encoded": "true"}
        assert set(post.headers) == HEADERS | BODY_HEADERS
        assert post.headers["x-auth-token"] == TOKEN
        body = json.loads(post.content)
        assert set(body) == {"submissions"}
        [item] = body["submissions"]  # exactly one test per request
        assert set(item) == ALLOWED_KEYS
        assert base64.b64decode(item["source_code"]).decode() == SOURCE
        stdins.append(base64.b64decode(item["stdin"]).decode())
        assert {k: item[k] for k in ALLOWED_KEYS - ENCODED} == {
            "language_id": 1002,
            "cpu_time_limit": 1.0,
            "wall_time_limit": 3.0,
            "memory_limit": 256 * 1024,
            "max_file_size": 1024,
            "enable_network": False,
            "redirect_stderr_to_stdout": False,
        }
        assert not _leaks(post)
    for get in fake.gets:
        assert get.method == "GET" and get.content == b""
        assert set(get.url.params) == {"tokens", "base64_encoded", "fields"}
        assert set(get.headers) == HEADERS
        assert not _leaks(get)
    assert stdins == ["in-0-1", "in-0-2", "in-1-1", "in-2-1", "in-2-2"]  # group 1 stopped
    assert verdict(job(), outcome)[:2] == (Verdict.partial, 60)


def test_judge0_execution_error_stops_the_group() -> None:
    def outcome(stdin: str) -> dict[str, object]:
        if stdin == "in-1-2":
            return {"status_id": 11}  # runtime error (NZEC)
        return _stdout({})(stdin)

    fake = FakeJudge0(outcome)
    result = worker._execute(job(), adapter(fake), alive)
    assert isinstance(result, Execution)
    assert len(fake.posts) == 6  # in-1-3 never submitted
    assert [r.status for r in result.runs[1]] == [RunStatus.completed, RunStatus.runtime_error]
    final, _ = worker._aggregate(job(), result)
    assert final.state == SubmissionState.done and final.verdict == Verdict.partial


# ------------------------------------------------------------------ one iteration, queue faked
#
# `worker._process` with the queue functions replaced by in-memory fakes: no database. The
# real fenced writes, attempt limits and lease SQL are covered in test_judge_worker.py and
# test_judge_queue.py (which need PostgreSQL).

CONFIG = worker.WorkerConfig(
    QueueConfig(lease_seconds=120, max_attempts=3, languages=frozenset(Language)), renew_seconds=30
)


class FakeQueue:
    """Records every queue call; `renew_ok` decides whether a renewal keeps the lease."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, j: Job) -> None:
        self.job, self.renew_ok = j, True
        self.calls: list[tuple[str, object]] = []
        monkeypatch.setattr(queue, "load_job", lambda _f, _c: self.job)
        monkeypatch.setattr(queue, "renew", self._renew)
        monkeypatch.setattr(queue, "release", self._release)
        monkeypatch.setattr(queue, "fail", self._fail)
        monkeypatch.setattr(queue, "finish", self._finish)

    def _renew(self, _f: object, _cfg: object, _c: object) -> bool:
        self.calls.append(("renew", self.renew_ok))
        return self.renew_ok

    def _release(self, _f: object, _cfg: object, _c: object, category: Failure) -> object:
        self.calls.append(("release", category))
        return SubmissionState.queued

    def _fail(self, _f: object, _c: object, category: Failure) -> bool:
        self.calls.append(("fail", category))
        return True

    def _finish(self, _f: object, _j: object, result: object, message: str | None) -> bool:
        self.calls.append(("finish", (result, message)))
        return True

    def writes(self) -> list[tuple[str, object]]:
        return [c for c in self.calls if c[0] != "renew"]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def process(
    fake: Any, config: worker.WorkerConfig = CONFIG, clock: Clock | None = None
) -> worker.Outcome:
    j = job()
    return worker._process(cast(Any, None), fake, config, j.claim, clock or Clock())


def test_a_compile_failure_on_the_first_test_is_the_students_compile_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = FakeQueue(monkeypatch, job())
    fake = FakeExecutor({(0, 1): CompileFailure("error: bad")})
    assert process(fake) == worker.Outcome.finished
    [(name, payload)] = q.writes()
    result, message = cast(tuple[SubmissionResult, str | None], payload)
    assert name == "finish" and message == "error: bad"
    assert (result.verdict, result.score) == (Verdict.compile_error, 0)
    assert fake.ran == [(0, 1)]


@pytest.mark.parametrize("later", [(0, 2), (1, 1), (2, 2)])
def test_a_compile_failure_after_a_test_ran_is_an_internal_judge_failure(
    monkeypatch: pytest.MonkeyPatch, later: tuple[int, int]
) -> None:
    q = FakeQueue(monkeypatch, job())
    fake = FakeExecutor({later: CompileFailure("COMPILER-FLAKE-1d")})
    assert process(fake) == worker.Outcome.released
    assert q.writes() == [("release", Failure.judge_internal)]  # retried, never stored
    assert fake.ran == TESTS[: TESTS.index(later) + 1]  # nothing ran after it


def test_a_later_compile_failure_raises_the_internal_run_error() -> None:
    fake = FakeExecutor({(1, 1): CompileFailure("x")})
    with pytest.raises(worker._InternalRun):
        worker._execute(job(), fake, alive)
    assert fake.ran == [(0, 1), (0, 2), (1, 1)]


def test_a_lease_lost_during_the_final_test_discards_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = FakeQueue(monkeypatch, job())
    clock = Clock()

    class LosesTheLeaseOnTheLastTest(FakeExecutor):
        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            outcome = super().run(request, heartbeat)
            if request.input == INPUTS[TESTS[-1]]:
                q.renew_ok, clock.now = False, clock.now + 60
                assert not heartbeat()  # lost while running, ignored: a result comes back
            return outcome

    fake = LosesTheLeaseOnTheLastTest()
    assert process(fake, clock=clock) == worker.Outcome.lease_lost
    assert fake.ran == TESTS  # no test after it, so only the post-execution check sees it
    assert q.writes() == []  # nothing finished, released or failed


def test_inputs_that_do_not_fit_fail_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = FakeQueue(monkeypatch, job(inputs={k: v for k, v in INPUTS.items() if k != (1, 3)}))
    fake = FakeExecutor()
    assert process(fake) == worker.Outcome.failed
    assert q.writes() == [("fail", Failure.invalid_test_data)] and fake.requests == []


def test_a_snapshot_error_raised_by_the_executor_is_released_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = FakeQueue(monkeypatch, job())

    class Raises(FakeExecutor):
        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            raise snap.SnapshotError("from the executor")

    assert process(Raises()) == worker.Outcome.released
    assert q.writes() == [("release", Failure.unexpected)]  # retried, not terminal


def test_no_test_starts_after_the_judging_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    q = FakeQueue(monkeypatch, job())
    clock = Clock()

    class Slow(FakeExecutor):
        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            clock.now += 700
            return super().run(request, heartbeat)

    fake = Slow()
    config = dataclasses.replace(CONFIG, max_judging_seconds=1800)
    assert process(fake, config, clock) == worker.Outcome.released
    assert len(fake.requests) == 3  # started at 0, 700 and 1400 s; not at 2100 s
    assert q.writes() == [("release", Failure.judge_timeout)]


def test_judging_within_the_deadline_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    q = FakeQueue(monkeypatch, job())
    clock = Clock()

    class Steady(FakeExecutor):
        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            clock.now += 100
            return super().run(request, heartbeat)

    assert process(Steady(), clock=clock) == worker.Outcome.finished
    assert [c[0] for c in q.writes()] == ["finish"]
    assert ("renew", True) in q.calls  # the heartbeat still renews between tests


@pytest.mark.parametrize("seconds", [59, 21601])
def test_the_judging_deadline_setting_is_bounded(seconds: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, judge_max_judging_seconds=seconds)  # never reads .env


def test_the_judging_deadline_comes_from_settings() -> None:
    settings = Settings(_env_file=None, judge_max_judging_seconds=900)
    assert worker.WorkerConfig.from_settings(settings, Language).max_judging_seconds == 900
    assert Settings(_env_file=None).judge_max_judging_seconds == 1800


def test_the_deadline_also_stops_a_test_that_is_still_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = FakeQueue(monkeypatch, job())
    clock = Clock()

    class Hangs(FakeExecutor):
        """Waits on the judge, beating the heart, as Judge0Executor does while polling."""

        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            self.requests.append(request)
            for _ in range(100):
                clock.now += 60
                heartbeat()
            raise AssertionError("the deadline should have stopped this wait")

    fake = Hangs()
    config = dataclasses.replace(CONFIG, max_judging_seconds=600)
    assert process(fake, config, clock) == worker.Outcome.released
    assert len(fake.requests) == 1 and clock.now - 1000.0 <= 660  # stopped within one beat
    assert [c for c in q.writes()] == [("release", Failure.judge_timeout)]
