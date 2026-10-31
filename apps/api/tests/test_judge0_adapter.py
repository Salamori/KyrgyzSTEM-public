"""Step 5.4.1: the Judge0 CE adapter, tested offline only.

Every request goes to an in-memory fake behind `httpx.MockTransport`; nothing reaches a
network or a Judge0 instance, and no code is compiled or run. Language ids here are
arbitrary test values, not real Judge0 ids. No database is used. `run()` executes one test;
the worker's sequential loop over it is tested in test_judge_sequential.py.
"""

import ast
import base64
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.judge import judge0
from app.modules.judge.core import RunStatus, TestRun
from app.modules.judge.judge0 import (
    Judge0AuthError,
    Judge0Config,
    Judge0Error,
    Judge0Executor,
    Judge0NotConfigured,
    Judge0ProtocolError,
)
from app.modules.judge.worker import (
    CompileFailure,
    JudgeTimeout,
    JudgeUnavailable,
    LeaseLost,
    TestRequest,
)
from app.modules.olympiad.models import Language

URL = "http://judge0.test"
TOKEN = "AUTH-TOKEN-SENTINEL-5e1"
SOURCE = "SOURCE-SENTINEL-print(input())"
ALLOWED_KEYS = {
    "language_id",
    "source_code",
    "stdin",
    "cpu_time_limit",
    "wall_time_limit",
    "memory_limit",
    "max_file_size",
    "enable_network",
    "redirect_stderr_to_stdout",
}


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def unb64(text: str) -> str:
    return base64.b64decode(text).decode()


def config(**changes: Any) -> Judge0Config:
    values: dict[str, Any] = {
        "url": URL,
        "token": TOKEN,
        "timeout_seconds": 5.0,
        "poll_seconds": 1.0,
        "max_wait_seconds": 60.0,
        "language_ids": {Language.cpp: 1001, Language.python: 1002},
        "time_factors": {Language.cpp: 1.0, Language.python: 1.0},
        "memory_factors": {Language.cpp: 1.0, Language.python: 1.0},
        "wall_time_factor": 3.0,
        "max_output_bytes": 1 << 20,
        "max_response_bytes": 16 << 20,
        **changes,
    }
    return Judge0Config(**values)


REQUEST = TestRequest(
    language=Language.python,
    time_limit_ms=1000,
    memory_limit_mb=256,
    source_code=SOURCE,
    input="in-a",
)


class FakeJudge0:
    """An in-memory Judge0: answers by test input. `outcome(stdin) -> dict` gives the final
    fields; the first `pending` polls of each batch report status 2 (processing)."""

    def __init__(
        self,
        outcome: Callable[[str], dict[str, Any]] | None = None,
        pending: int = 0,
    ) -> None:
        self.outcome = outcome or (
            lambda stdin: {"status_id": 3, "stdout": b64("out-" + stdin[3:])}
        )
        self.pending = pending
        self.posts: list[httpx.Request] = []
        self.gets: list[httpx.Request] = []
        self._stdin: dict[str, str] = {}
        self._polls: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.posts.append(request)
            items = json.loads(request.content)["submissions"]
            created = []
            for item in items:
                token = f"tok-{len(self._stdin) + 1}"
                self._stdin[token] = unb64(item["stdin"])
                created.append({"token": token})
            return httpx.Response(201, json=created)
        self.gets.append(request)
        tokens = request.url.params["tokens"].split(",")
        key = tokens[0]
        self._polls[key] = self._polls.get(key, 0) + 1
        if self._polls[key] <= self.pending:
            return httpx.Response(
                200, json={"submissions": [{"token": t, "status_id": 2} for t in tokens]}
            )
        return httpx.Response(
            200,
            json={
                "submissions": [
                    {"token": t, "time": "0.012", "memory": 1234, **self.outcome(self._stdin[t])}
                    for t in tokens
                ]
            },
        )


def executor(
    fake: FakeJudge0 | Callable[[httpx.Request], httpx.Response],
    cfg: Judge0Config | None = None,
    clock: Callable[[], float] | None = None,
    sleeps: list[float] | None = None,
) -> Judge0Executor:
    handler = fake.handler if isinstance(fake, FakeJudge0) else fake
    record = sleeps if sleeps is not None else []
    return Judge0Executor(
        cfg or config(),
        httpx.Client(transport=httpx.MockTransport(handler), base_url=URL),
        sleep=record.append,
        clock=clock or (lambda: 0.0),
    )


def always_alive() -> bool:
    return True


# ------------------------------------------------------------------ requests


def test_a_request_carries_source_one_input_and_limits_only() -> None:
    fake = FakeJudge0()
    executor(fake).run(REQUEST, always_alive)
    [post] = fake.posts  # one test per call
    assert post.url.path == "/submissions/batch"
    assert post.url.params["base64_encoded"] == "true"
    assert post.headers["X-Auth-Token"] == TOKEN
    body = json.loads(post.content)
    assert set(body) == {"submissions"}
    [item] = body["submissions"]
    assert set(item) == ALLOWED_KEYS  # no expected output, no identifiers
    assert unb64(item["source_code"]) == SOURCE
    assert unb64(item["stdin"]) == "in-a"
    assert item["language_id"] == 1002
    assert (item["cpu_time_limit"], item["wall_time_limit"]) == (1.0, 3.0)
    assert (item["memory_limit"], item["max_file_size"]) == (256 * 1024, 1024)
    assert item["enable_network"] is False
    assert b"expected" not in post.content
    [get] = fake.gets
    assert get.url.params["fields"] == "token,status_id,stdout,time,memory,compile_output"


@pytest.mark.parametrize(
    ("time_limit_ms", "cpu", "wall"),
    [(100, 0.1, 1.0), (333, 0.333, 1.0), (334, 0.334, 1.002), (2000, 2.0, 6.0)],
)
def test_wall_time_is_never_below_judge0s_one_second_minimum(
    time_limit_ms: int, cpu: float, wall: float
) -> None:
    fake = FakeJudge0()
    request = TestRequest(Language.cpp, time_limit_ms, 256, SOURCE, "in-a")
    executor(fake).run(request, always_alive)
    item = json.loads(fake.posts[0].content)["submissions"][0]
    assert (item["cpu_time_limit"], item["wall_time_limit"]) == (cpu, wall)


def test_language_factors_scale_the_limits() -> None:
    fake = FakeJudge0()
    cfg = config(
        time_factors={Language.cpp: 1.0, Language.python: 2.0},
        memory_factors={Language.cpp: 1.0, Language.python: 1.5},
        wall_time_factor=2.5,
    )
    executor(fake, cfg).run(REQUEST, always_alive)
    item = json.loads(fake.posts[0].content)["submissions"][0]
    assert (item["cpu_time_limit"], item["wall_time_limit"]) == (2.0, 5.0)
    assert item["memory_limit"] == int(256 * 1024 * 1.5)


def test_an_unconfigured_language_is_refused_before_any_request() -> None:
    fake = FakeJudge0()
    cfg = config(language_ids={Language.cpp: 1001})  # no Python id configured
    with pytest.raises(Judge0NotConfigured):
        executor(fake, cfg).run(REQUEST, always_alive)
    assert fake.posts == [] and fake.gets == []


# ------------------------------------------------------------------ results


def test_a_completed_run_returns_the_actual_output_uncompared() -> None:
    outcome = executor(FakeJudge0()).run(REQUEST, always_alive)
    assert outcome == TestRun(RunStatus.completed, "out-a", 12, 1234)


@pytest.mark.parametrize(
    ("status", "memory", "expected"),
    [
        (3, 1234, RunStatus.completed),
        (4, 1234, RunStatus.completed),  # only with an expected output, never sent: tolerated
        (5, 1234, RunStatus.time_limit),
        (7, 1234, RunStatus.runtime_error),
        (8, 1234, RunStatus.runtime_error),  # SIGXFSZ, output limit: runtime_error (E5)
        (9, 1234, RunStatus.runtime_error),
        (10, 1234, RunStatus.runtime_error),
        (11, 1234, RunStatus.runtime_error),
        (12, 1234, RunStatus.runtime_error),
        (11, 256 * 1024, RunStatus.memory_limit),  # UNVERIFIED inference: memory at the limit
        (8, 256 * 1024, RunStatus.runtime_error),  # SIGXFSZ stays an output-limit runtime_error
        (13, 1234, RunStatus.internal_error),
        (14, 1234, RunStatus.internal_error),
        (99, 1234, RunStatus.internal_error),  # unknown: the judge's fault, never the student's
    ],
)
def test_statuses_map_to_run_results(status: int, memory: int, expected: RunStatus) -> None:
    fake = FakeJudge0(lambda _: {"status_id": status, "stdout": b64("x"), "memory": memory})
    request = TestRequest(Language.cpp, 1000, 256, SOURCE, "in-a")
    run = executor(fake).run(request, always_alive)
    assert isinstance(run, TestRun)
    assert run.status == expected
    assert run.output == ("x" if expected == RunStatus.completed else "")


def test_output_above_the_limit_is_a_runtime_error() -> None:
    fake = FakeJudge0(lambda _: {"status_id": 3, "stdout": b64("y" * 2000)})
    outcome = executor(fake, config(max_output_bytes=1024)).run(REQUEST, always_alive)
    assert outcome == TestRun(RunStatus.runtime_error, "", 12, 1234)


def test_compile_error_returns_the_compiler_message() -> None:
    fake = FakeJudge0(lambda _: {"status_id": 6, "compile_output": b64("error: expected ';'")})
    outcome = executor(fake).run(REQUEST, always_alive)
    assert outcome == CompileFailure("error: expected ';'")
    assert len(fake.posts) == 1


def test_wrapped_base64_from_judge0_is_accepted() -> None:
    encoded = b64("line one\nline two\n")
    wrapped = encoded[:8] + "\n" + encoded[8:]
    fake = FakeJudge0(lambda _: {"status_id": 3, "stdout": wrapped})
    outcome = executor(fake).run(REQUEST, always_alive)
    assert isinstance(outcome, TestRun)
    assert outcome.output == "line one\nline two\n"


# ------------------------------------------------------------------ polling and leases


def test_polling_beats_the_heart_and_sleeps_between_polls() -> None:
    beats: list[bool] = []

    def heartbeat() -> bool:
        beats.append(True)
        return True

    sleeps: list[float] = []
    fake = FakeJudge0(pending=2)
    outcome = executor(fake, sleeps=sleeps).run(REQUEST, heartbeat)
    assert isinstance(outcome, TestRun)
    assert len(fake.gets) == 3  # 2 pending + 1 final
    assert sleeps == [1.0] * 2
    assert len(beats) == 2  # on every pending poll (the worker beats before each test)


def test_a_lost_lease_while_polling_stops_with_lease_lost() -> None:
    fake = FakeJudge0(pending=5)
    with pytest.raises(LeaseLost):
        executor(fake).run(REQUEST, lambda: False)  # lost on the first pending poll
    assert len(fake.gets) == 1 and len(fake.posts) == 1


def test_a_trickling_response_is_cut_off_after_one_timeout() -> None:
    now = [0.0]

    def clock() -> float:
        now[0] += 3.0  # every read of the clock is 3 s later
        return now[0]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, content=iter([b"[", b'{"token"', b': "t"}', b"]"]))

    adapter = Judge0Executor(
        config(timeout_seconds=5.0),
        httpx.Client(transport=httpx.MockTransport(handler), base_url=URL),
        sleep=lambda _s: None,
        clock=clock,
    )
    with pytest.raises(JudgeTimeout):
        adapter.run(REQUEST, always_alive)


def test_production_accepts_a_judge0_url_with_a_token() -> None:
    s = _settings(
        judge0_url=URL, judge0_auth_token=TOKEN, app_env="production", session_cookie_secure=True
    )
    assert Judge0Config.from_settings(s).token == TOKEN


def test_waiting_is_bounded() -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    fake = FakeJudge0(pending=10_000)
    adapter = Judge0Executor(
        config(max_wait_seconds=10.0, poll_seconds=1.0),
        httpx.Client(transport=httpx.MockTransport(fake.handler), base_url=URL),
        sleep=sleep,
        clock=lambda: now[0],
    )
    with pytest.raises(JudgeTimeout):
        adapter.run(REQUEST, always_alive)
    assert len(fake.gets) <= 12


# ------------------------------------------------------------------ HTTP failures


def _status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(code, text="server says no")


@pytest.mark.parametrize("code", [401, 403])
def test_authentication_failures(code: int) -> None:
    with pytest.raises(Judge0AuthError) as raised:
        executor(_status(code)).run(REQUEST, always_alive)
    assert isinstance(raised.value, JudgeUnavailable)  # released and retried by the worker


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_server_errors_are_temporary(code: int) -> None:
    with pytest.raises(Judge0Error) as raised:
        executor(_status(code)).run(REQUEST, always_alive)
    assert not isinstance(raised.value, (Judge0AuthError, Judge0ProtocolError))


@pytest.mark.parametrize("code", [400, 404, 422])
def test_refused_requests_are_protocol_errors(code: int) -> None:
    with pytest.raises(Judge0ProtocolError):
        executor(_status(code)).run(REQUEST, always_alive)


def _raises(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (httpx.ConnectError("refused " + TOKEN), Judge0Error),
        (httpx.ReadTimeout("slow " + TOKEN), JudgeTimeout),
        (httpx.ConnectTimeout("slow " + TOKEN), JudgeTimeout),
        (httpx.RemoteProtocolError("broken " + TOKEN), Judge0Error),
    ],
)
def test_transport_failures_are_contained(raised: Exception, expected: type[Exception]) -> None:
    with pytest.raises(expected) as caught:
        executor(_raises(raised)).run(REQUEST, always_alive)
    exc = caught.value
    assert exc.__cause__ is None and exc.__context__ is None  # the httpx error is dropped
    assert TOKEN not in str(exc) and URL not in str(exc) and SOURCE not in repr(exc)


def _json(payload: Any, post: Any = None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=post if post is not None else [{"token": "t"}])
        return httpx.Response(200, json=payload)

    return handler


GOOD = {"token": "t", "status_id": 3, "stdout": b64("o"), "time": "0.1", "memory": 1}


@pytest.mark.parametrize(
    "handler",
    [
        lambda _r: httpx.Response(201, text="not json"),
        _json({}, post={"token": "t"}),  # POST answer is not a list
        _json({}, post=[{"no": "token"}]),
        _json({}, post=[{"token": "t"}, {"token": "u"}]),  # more tokens than tests
        _json({"submissions": []}),
        _json([GOOD]),  # GET answer is not an object
        _json({"submissions": [{**GOOD, "token": "other"}]}),
        _json({"submissions": [{**GOOD, "status_id": "3"}]}),
        _json({"submissions": [{**GOOD, "stdout": "@@not base64@@"}]}),
        _json({"submissions": [{**GOOD, "memory": "1"}]}),
        _json({"submissions": [{**GOOD, "time": "fast"}]}),
    ],
)
def test_malformed_responses_are_protocol_errors(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    request = TestRequest(Language.cpp, 1000, 256, SOURCE, "in-a")
    with pytest.raises(Judge0ProtocolError):
        executor(handler).run(request, always_alive)


def test_oversized_responses_are_refused() -> None:
    fake = FakeJudge0(lambda _: {"status_id": 3, "stdout": b64("z" * 200_000)})
    with pytest.raises(Judge0ProtocolError):
        executor(fake, config(max_response_bytes=1 << 16)).run(REQUEST, always_alive)


# ------------------------------------------------------------------ settings and secrecy


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # _env_file=None: never reads .env


def test_settings_defaults_leave_judge0_unconfigured() -> None:
    s = _settings()
    assert s.judge0_url == "" and s.judge0_auth_token.get_secret_value() == ""
    assert s.judge0_language_id_cpp is None and s.judge0_language_id_python is None
    with pytest.raises(Judge0NotConfigured):
        Judge0Config.from_settings(s)


def test_settings_build_a_config_without_exposing_the_token() -> None:
    s = _settings(
        judge0_url=URL,
        judge0_auth_token=TOKEN,
        judge0_language_id_cpp=1001,
        judge0_time_factor_python=2.0,
    )
    cfg = Judge0Config.from_settings(s)
    assert cfg.url == URL and cfg.token == TOKEN
    assert cfg.language_ids == {Language.cpp: 1001}  # Python left unconfigured
    assert cfg.time_factors[Language.python] == 2.0
    assert TOKEN not in repr(s) and TOKEN not in repr(cfg)


@pytest.mark.parametrize(
    "values",
    [
        {"judge0_url": "ftp://judge0.test"},
        {"judge0_url": "judge0.test"},
        {"judge0_timeout_seconds": 0.5},
        {"judge0_timeout_seconds": 121},
        {"judge0_poll_seconds": 0.1},
        {"judge0_max_wait_seconds": 5},
        {"judge0_language_id_cpp": 0},
        {"judge0_time_factor_python": 0.5},
        {"judge0_memory_factor_cpp": 11},
        {"judge0_wall_time_factor": 0.9},
        {"judge0_max_output_bytes": 100},
        {"judge0_max_response_bytes": 1024},
        {"judge0_url": "http://user:pass@judge0.test"},  # credentials belong in the token
        {"judge0_url": "http://judge0.test/?token=x"},
        {"judge0_url": "http://judge0.test/#x"},
        {"judge0_url": "http://"},
        {"judge0_url": URL, "app_env": "production", "session_cookie_secure": True},
    ],
)
def test_settings_reject_out_of_bounds_values(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _settings(**values)


def test_nothing_sensitive_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeJudge0(pending=1)
    with caplog.at_level(logging.DEBUG):
        executor(fake).run(REQUEST, always_alive)
        with pytest.raises(Judge0AuthError):
            executor(_status(401)).run(REQUEST, always_alive)
    for record in caplog.records:
        text = record.getMessage() + repr(getattr(record, "fields", ""))
        for secret in (TOKEN, SOURCE, "in-a", "out-a", b64(SOURCE)):
            assert secret not in text


def test_adapter_module_cannot_execute_code() -> None:
    tree = ast.parse(Path(judge0.__file__).read_text(encoding="utf-8"))
    banned = {"subprocess", "multiprocessing", "ctypes", "importlib", "pty", "shutil", "os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"exec", "eval", "compile", "__import__"}
