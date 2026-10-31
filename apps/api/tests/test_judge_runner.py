"""The persistent judge worker loop (judge/runner.py) and its entry point, without a database.

`process_one` is either replaced by a scripted fake or run for real over an in-memory queue
(the queue functions monkeypatched). Waiting is recorded, never slept; nothing is compiled
or run and nothing reaches a network.
"""

import ast
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.judge import queue, runner, worker
from app.modules.judge.queue import Claim, Failure, QueueConfig
from app.modules.judge.runner import Health, RunnerConfig, Stop
from app.modules.judge.worker import Heartbeat, Outcome, TestOutcome, TestRequest, WorkerConfig
from app.modules.olympiad.models import Language
from scripts import judge_worker
from tests.test_judge0_adapter import URL, config, executor
from tests.test_judge_sequential import FakeExecutor, FakeQueue, job

CONFIG = RunnerConfig(poll_seconds=2.0, backoff_max_seconds=30.0, shutdown_grace_seconds=10.0)
WORKER = WorkerConfig(
    QueueConfig(lease_seconds=120, max_attempts=3, languages=frozenset(Language)), renew_seconds=30
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ScriptedStop(Stop):
    """Records every wait (advancing the clock instead of sleeping) and requests a stop
    once `after` waits have happened."""

    def __init__(self, clock: Clock, after: int) -> None:
        super().__init__(clock)
        self.clock, self.after = clock, after
        self.waits: list[float] = []

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock.now += seconds
        if len(self.waits) >= self.after:
            self.request()


def scripted(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[Outcome], stop: Stop
) -> list[Callable[[], bool]]:
    """Replace process_one with one that returns `outcomes` in turn, then stops."""
    seen: list[Callable[[], bool]] = []
    it: Iterator[Outcome] = iter(outcomes)

    def fake(_f: Any, _e: Any, _c: Any, _clock: Any, stopping: Callable[[], bool]) -> Outcome:
        seen.append(stopping)
        try:
            return next(it)
        except StopIteration:
            stop.request()
            return Outcome.idle

    monkeypatch.setattr(worker, "process_one", fake)
    return seen


def loop(stop: Stop, probe: Callable[[], bool] = lambda: True, **kw: Any) -> int:
    return runner.run(
        lambda: None,  # type: ignore[arg-type,return-value]
        FakeExecutor(),
        probe,
        WORKER,
        kw.pop("config", CONFIG),
        stop,
        kw.pop("health", None),
        kw.pop("clock", Clock()),
    )


# ------------------------------------------------------------------ pacing


def test_backoff_doubles_from_the_poll_interval_and_is_capped() -> None:
    assert [CONFIG.backoff(n) for n in range(1, 7)] == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert CONFIG.backoff(10_000) == 30.0  # no overflow


def test_idle_polling_waits_the_poll_interval_and_never_spins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    stop = ScriptedStop(clock, after=5)
    calls = scripted(monkeypatch, [Outcome.idle] * 10, stop)
    assert loop(stop, clock=clock) == 0
    assert stop.waits == [2.0] * 5 and len(calls) == 5


def test_after_a_job_the_next_claim_follows_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    stop = ScriptedStop(clock, after=1)
    calls = scripted(
        monkeypatch, [Outcome.finished, Outcome.failed, Outcome.lease_lost, Outcome.idle], stop
    )
    loop(stop, clock=clock)
    assert len(calls) == 4 and stop.waits == [2.0]  # only the idle round waited


@pytest.mark.parametrize("failure", [Outcome.released, Outcome.database_error, Outcome.unexpected])
def test_temporary_failures_back_off_and_a_success_resets(
    monkeypatch: pytest.MonkeyPatch, failure: Outcome
) -> None:
    clock = Clock()
    stop = ScriptedStop(clock, after=6)
    scripted(
        monkeypatch, [failure, failure, failure, Outcome.finished, failure, Outcome.idle], stop
    )
    loop(stop, clock=clock)
    assert stop.waits[:5] == [2.0, 4.0, 8.0, 2.0, 2.0]  # backoff, reset, then idle poll


def test_nothing_is_claimed_while_the_judge_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    stop = ScriptedStop(clock, after=100)
    answers = iter([False, False, False, True])
    calls = scripted(monkeypatch, [Outcome.finished], stop)
    health = Health(None)
    with caplog.at_level(logging.INFO, logger="kyrgyzstem.judging"):
        loop(stop, probe=lambda: next(answers, True), clock=clock, health=health)
    assert stop.waits[:3] == [2.0, 4.0, 8.0]  # backing off, no claims
    assert len(calls) == 2  # the finished job, then the final idle round
    events = [r.getMessage() for r in caplog.records]
    assert events.count("judge.worker_not_ready") == 1  # logged once, not every probe
    assert "judge.worker_ready" in events and events[-1] == "judge.worker_stopped"
    for record in caplog.records:
        assert set(record.__dict__.get("fields", {})) <= queue.LOG_FIELDS


# ------------------------------------------------------------------ shutdown


def test_a_stop_while_idle_ends_the_loop_without_another_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    stop = ScriptedStop(clock, after=1)
    calls = scripted(monkeypatch, [Outcome.idle, Outcome.finished], stop)
    assert loop(stop, clock=clock) == 0
    assert len(calls) == 1


def test_stopping_is_false_until_the_grace_period_is_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    stop = Stop(clock)
    seen: list[bool] = []

    def fake(_f: Any, _e: Any, _c: Any, _clock: Any, stopping: Callable[[], bool]) -> Outcome:
        seen.append(stopping())  # no stop requested
        stop.request()
        seen.append(stopping())  # requested, grace running
        clock.now += 10.0
        seen.append(stopping())  # grace over
        return Outcome.released

    monkeypatch.setattr(worker, "process_one", fake)
    loop(stop, clock=clock)
    assert seen == [False, False, True]


def test_a_job_in_flight_at_shutdown_is_released_after_the_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    stop = Stop(clock)
    q = FakeQueue(monkeypatch, job())
    claims = iter([job().claim])
    monkeypatch.setattr(queue, "sweep", lambda _f, _c: 0)
    monkeypatch.setattr(queue, "claim", lambda _f, _c: next(claims, None))

    class StopsMidJob(FakeExecutor):
        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            if len(self.requests) == 2:
                stop.request()  # SIGTERM arrives during the third test
                clock.now += 11.0  # it outlives the 10 s grace period
            return super().run(request, heartbeat)

    fake = StopsMidJob()
    assert runner.run(lambda: None, fake, lambda: True, WORKER, CONFIG, stop, None, clock) == 0  # type: ignore[arg-type,return-value]
    assert len(fake.requests) == 3  # nothing ran after the grace period
    assert q.writes() == [("release", Failure.worker_stopping)]  # given back, nothing stored


def test_a_job_finishing_within_the_grace_period_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    stop = Stop(clock)
    q = FakeQueue(monkeypatch, job())
    claims = iter([job().claim])
    monkeypatch.setattr(queue, "sweep", lambda _f, _c: 0)
    monkeypatch.setattr(queue, "claim", lambda _f, _c: next(claims, None))

    class StopsEarly(FakeExecutor):
        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            stop.request()  # within the grace period: the job may finish
            return super().run(request, heartbeat)

    runner.run(lambda: None, StopsEarly(), lambda: True, WORKER, CONFIG, stop, None, clock)  # type: ignore[arg-type,return-value]
    assert [w[0] for w in q.writes()] == ["finish"]


def test_a_lost_lease_is_not_retried_by_this_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    stop = Stop(clock)
    q = FakeQueue(monkeypatch, job())
    claims = iter([job().claim])
    monkeypatch.setattr(queue, "sweep", lambda _f, _c: 0)

    def claim(_f: Any, _c: Any) -> Claim | None:
        claimed = next(claims, None)
        if claimed is None:
            stop.request()  # one job only
        return claimed

    monkeypatch.setattr(queue, "claim", claim)

    class LosesTheLease(FakeExecutor):
        def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
            q.renew_ok, clock.now = False, clock.now + 60
            return super().run(request, heartbeat)

    runner.run(lambda: None, LosesTheLease(), lambda: True, WORKER, CONFIG, stop, None, clock)  # type: ignore[arg-type,return-value]
    assert q.writes() == []  # nothing finished, failed or released by the stale worker


def test_base_exceptions_end_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*_a: Any) -> Outcome:
        raise SystemExit(1)  # a second signal

    monkeypatch.setattr(worker, "process_one", interrupted)
    with pytest.raises(SystemExit):
        loop(Stop(Clock()))


# ------------------------------------------------------------------ health


def test_health_file_reports_alive_and_ready(tmp_path: Path) -> None:
    path, now = tmp_path / "health.json", [100.0]
    health = Health(path, every_seconds=5.0, clock=lambda: now[0])
    health.touch()
    assert runner.read_health(path, 60, 100.0) == (True, False)
    health.set_ready(True)  # a change is written at once
    assert runner.read_health(path, 60, 100.0) == (True, True)
    assert set(json.loads(path.read_text())) == {"updated", "ready"}  # nothing else
    assert runner.read_health(path, 60, 161.0) == (False, False)  # stale: neither


def test_health_writes_are_rate_limited(tmp_path: Path) -> None:
    path, now = tmp_path / "health.json", [100.0]
    health = Health(path, every_seconds=5.0, clock=lambda: now[0])
    health.touch()
    now[0] = 103.0
    health.touch()
    assert json.loads(path.read_text())["updated"] == 100.0
    now[0] = 106.0
    health.touch()
    assert json.loads(path.read_text())["updated"] == 106.0


@pytest.mark.parametrize("content", [None, "", "{", '{"ready": true}', '{"updated": "x"}'])
def test_a_missing_or_malformed_health_file_is_unhealthy(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "health.json"
    if content is not None:
        path.write_text(content)
    assert runner.read_health(path, 60, 100.0) == (False, False)


def test_a_job_in_flight_keeps_the_health_file_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = [0.0]
    health = Health(tmp_path / "h.json", every_seconds=5.0, clock=lambda: now[0])
    stop = Stop(Clock())

    def long_job(_f: Any, _e: Any, _c: Any, _clock: Any, stopping: Callable[[], bool]) -> Outcome:
        for _ in range(10):  # heartbeats while waiting on the judge
            now[0] += 6.0
            stopping()
        stop.request()
        return Outcome.finished

    monkeypatch.setattr(worker, "process_one", long_job)
    loop(stop, health=health)
    assert json.loads((tmp_path / "h.json").read_text())["updated"] == 60.0


# ------------------------------------------------------------------ entry point and settings


def test_the_entry_point_refuses_to_start_without_judge0(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(judge_worker, "get_settings", lambda: Settings(_env_file=None))

    def no_database() -> None:
        raise AssertionError("must not connect")

    monkeypatch.setattr(judge_worker, "get_engine", no_database)
    with caplog.at_level(logging.ERROR, logger="kyrgyzstem.judging"):
        assert judge_worker.main([]) == judge_worker.NOT_CONFIGURED
    assert [r.getMessage() for r in caplog.records] == ["judge.worker_not_configured"]


def test_the_entry_point_refuses_judge0_without_any_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, judge0_url=URL)  # no language ids
    monkeypatch.setattr(judge_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(judge_worker, "get_engine", lambda: pytest.fail("must not connect"))
    assert judge_worker.main([]) == judge_worker.NOT_CONFIGURED


def test_the_check_command_reads_the_health_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "h.json"
    settings = Settings(_env_file=None, judge_worker_health_file=str(path))
    monkeypatch.setattr(judge_worker, "get_settings", lambda: settings)
    assert judge_worker.main(["--check", "alive"]) == 1  # no file yet
    health = Health(path)
    health.set_ready(False)
    assert judge_worker.main(["--check", "alive"]) == 0
    assert judge_worker.main(["--check", "ready"]) == 1  # alive but Judge0 not ready
    health.set_ready(True)
    assert judge_worker.main(["--check", "ready"]) == 0


def test_the_check_command_without_a_health_file_setting_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(judge_worker, "get_settings", lambda: Settings(_env_file=None))
    assert judge_worker.main(["--check", "alive"]) == 1


def test_runner_settings_and_bounds() -> None:
    s = Settings(_env_file=None)
    assert RunnerConfig.from_settings(s) == RunnerConfig(2.0, 60.0, 30.0)
    assert s.judged_languages() == frozenset()
    s = Settings(_env_file=None, judge_languages=" python , cpp ")
    assert s.judged_languages() == {"python", "cpp"} == {lang.value for lang in Language}


@pytest.mark.parametrize(
    "values",
    [
        {"judge_backoff_max_seconds": 0.5},
        {"judge_backoff_max_seconds": 601},
        {"judge_backoff_max_seconds": 1, "judge_poll_seconds": 2},  # below the poll interval
        {"judge_shutdown_grace_seconds": -1},
        {"judge_shutdown_grace_seconds": 601},
        {"judge_languages": "java"},
        {"judge_languages": "python,c++"},
    ],
)
def test_runner_settings_reject_invalid_values(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


# ------------------------------------------------------------------ readiness probe


def _languages(payload: Any, code: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/languages"
        assert request.content == b""  # nothing is submitted
        return httpx.Response(code, json=payload)

    return handler


def test_probe_is_ready_when_every_configured_language_is_offered() -> None:
    offered = [{"id": 1001, "name": "C++"}, {"id": 1002, "name": "Python"}, {"id": 7}]
    assert executor(_languages(offered)).probe() is True


@pytest.mark.parametrize(
    "handler",
    [
        _languages([{"id": 1001}]),  # Python missing
        _languages({"id": 1001}),  # not a list
        _languages([{"id": "1001"}, {"id": "1002"}]),
        _languages([], code=500),
        _languages([], code=401),
    ],
)
def test_probe_is_not_ready_otherwise(handler: Callable[[httpx.Request], httpx.Response]) -> None:
    assert executor(handler).probe() is False


def test_probe_contains_transport_failures() -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert executor(refuse).probe() is False


def test_probe_without_any_language_id_is_not_ready() -> None:
    assert executor(_languages([]), config(language_ids={})).probe() is False


# ------------------------------------------------------------------ static safety


@pytest.mark.parametrize("module", [runner.__file__, judge_worker.__file__])
def test_runner_and_entry_point_cannot_execute_code(module: str) -> None:
    tree = ast.parse(Path(module).read_text(encoding="utf-8"))
    banned = {"subprocess", "multiprocessing", "ctypes", "importlib", "pty", "shutil", "os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"exec", "eval", "compile", "__import__"}


def test_claim_type_is_reused() -> None:
    assert job().claim.__class__ is Claim  # the loop reuses the queue's own types


def test_an_empty_language_id_variable_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE0_LANGUAGE_ID_CPP", "")  # as written in .env.example
    monkeypatch.setenv("JUDGE0_LANGUAGE_ID_PYTHON", " ")
    s = Settings(_env_file=None)
    assert s.judge0_language_id_cpp is None and s.judge0_language_id_python is None
    monkeypatch.setenv("JUDGE0_LANGUAGE_ID_PYTHON", "71")
    assert Settings(_env_file=None).judge0_language_id_python == 71
