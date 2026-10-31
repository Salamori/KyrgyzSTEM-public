"""Judge0 CE adapter: an `Executor` for the judge worker (Step 5.4.1).

Constructed only by the judge worker's entry point (scripts/judge_worker.py); the API never
uses it. Built and tested offline only (tests use `httpx.MockTransport`); no Judge0
instance has been contacted.

`run()` executes **one test** per call, as the worker asks for it: the worker walks the tests
in order, compares each output with the expected one and stops a group at its first failure,
so this adapter never decides verdicts and never sees expected outputs. What Judge0 receives
per call: the language id, the source and that test's input (both base64), and limits; never
submission, user or problem identifiers. Nothing here logs: request and response bodies,
the token and the URL never reach a log or an exception message, and HTTP errors are
replaced by detail-free exceptions raised outside the handler (nothing chained).

API contract used (Judge0 CE documentation; to be confirmed on the live instance):
`POST /submissions/batch?base64_encoded=true` with `{"submissions": [...]}` returns one
`{"token": ...}` per item; `GET /submissions/batch?tokens=a,b&base64_encoded=true&fields=...`
returns `{"submissions": [...]}` in token order; `X-Auth-Token` authenticates. Status ids:
1 in queue, 2 processing, 3 accepted, 4 wrong answer (only with an expected output, which is
never sent), 5 time limit, 6 compilation error, 7-12 runtime errors (7 SIGSEGV, 8 SIGXFSZ,
9 SIGFPE, 10 SIGABRT, 11 NZEC, 12 other), 13 internal error, 14 exec format error.

Unverified and isolated here (to be checked by the live security suite before enabling):
* Memory-limit exceeded has no status of its own. A runtime error whose reported memory
  reached the limit is treated as `memory_limit`; this inference is NOT verified.
* Output-limit exceeded (status 8, SIGXFSZ, or stdout larger than `max_output_bytes`) is
  reported as `runtime_error` (decision E5, provisional).
* Language ids depend on the installation: there are no defaults; an unconfigured language
  raises `Judge0NotConfigured`.
"""

import base64
import binascii
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import httpx

from app.core.config import Settings
from app.modules.judge.core import RunStatus, TestRun
from app.modules.judge.worker import (
    CompileFailure,
    Heartbeat,
    JudgeTimeout,
    JudgeUnavailable,
    LeaseLost,
    TestOutcome,
    TestRequest,
)
from app.modules.olympiad.models import Language

IN_QUEUE, PROCESSING = 1, 2
ACCEPTED, WRONG_ANSWER = 3, 4
TIME_LIMIT_EXCEEDED = 5
COMPILATION_ERROR = 6
SIGXFSZ = 8  # file (output) size limit exceeded
RUNTIME_ERRORS = frozenset({7, 8, 9, 10, 11, 12})
_PENDING = frozenset({IN_QUEUE, PROCESSING})
MIN_WALL_SECONDS = 1.0  # Submission validation in Judge0 CE v1.13.x: wall_time_limit >= 1
_FIELDS = "token,status_id,stdout,time,memory,compile_output"


class Judge0Error(JudgeUnavailable):
    """Judge0 could not be used; the worker releases the job (retried, then judge_error)."""


class Judge0NotConfigured(Judge0Error):
    pass


class Judge0AuthError(Judge0Error):
    pass


class Judge0ProtocolError(Judge0Error):
    """A response that does not follow the expected contract (or is too large)."""


@dataclass(frozen=True)
class Judge0Config:
    url: str
    token: str = field(repr=False)
    timeout_seconds: float
    poll_seconds: float
    max_wait_seconds: float
    language_ids: Mapping[Language, int]
    time_factors: Mapping[Language, float]
    memory_factors: Mapping[Language, float]
    wall_time_factor: float
    max_output_bytes: int
    max_response_bytes: int

    @classmethod
    def from_settings(cls, s: Settings) -> "Judge0Config":
        if not s.judge0_url:
            raise Judge0NotConfigured("Judge0 is not configured")
        language_ids = {
            language: value
            for language, value in (
                (Language.cpp, s.judge0_language_id_cpp),
                (Language.python, s.judge0_language_id_python),
            )
            if value is not None
        }
        return cls(
            url=s.judge0_url,
            token=s.judge0_auth_token.get_secret_value(),
            timeout_seconds=s.judge0_timeout_seconds,
            poll_seconds=s.judge0_poll_seconds,
            max_wait_seconds=s.judge0_max_wait_seconds,
            language_ids=language_ids,
            time_factors={
                Language.cpp: s.judge0_time_factor_cpp,
                Language.python: s.judge0_time_factor_python,
            },
            memory_factors={
                Language.cpp: s.judge0_memory_factor_cpp,
                Language.python: s.judge0_memory_factor_python,
            },
            wall_time_factor=s.judge0_wall_time_factor,
            max_output_bytes=s.judge0_max_output_bytes,
            max_response_bytes=s.judge0_max_response_bytes,
        )


@dataclass(frozen=True)
class Limits:
    cpu_seconds: float
    wall_seconds: float
    memory_kb: int
    max_file_kb: int


@dataclass(frozen=True)
class _Result:
    token: str
    status: int
    stdout: bytes | None = field(repr=False)
    time_ms: int | None
    memory_kb: int | None
    compile_output: str | None = field(repr=False)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _unb64(value: object) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("not a base64 string")
    # Judge0 may wrap base64 lines; whitespace is not part of the data.
    return base64.b64decode("".join(value.split()), validate=True)


class Judge0Executor:
    """`Executor` backed by a Judge0 CE server. Tests inject an `httpx.Client` built on
    `httpx.MockTransport`; `sleep` and `clock` are injectable so polling needs no waiting."""

    def __init__(
        self,
        config: Judge0Config,
        client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.url, timeout=config.timeout_seconds, follow_redirects=False
        )
        self._sleep, self._clock = sleep, clock
        self._headers = {"X-Auth-Token": config.token} if config.token else {}

    def close(self) -> None:
        self._client.close()

    def probe(self) -> bool:
        """Readiness: Judge0 answers `GET /languages` with our credentials and offers every
        configured language id. Nothing is submitted or run. False on any failure (no
        detail is kept). UNVERIFIED on a live instance: the endpoint's response shape."""
        if not self._config.language_ids:
            return False
        try:
            data = self._send("GET", "/languages", {}, None)
        except (JudgeUnavailable, JudgeTimeout):
            return False
        if not isinstance(data, list):
            return False
        offered = {
            item.get("id")
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        return set(self._config.language_ids.values()) <= offered

    def limits(self, request: TestRequest) -> Limits:
        cpu = request.time_limit_ms / 1000 * self._config.time_factors[request.language]
        return Limits(
            cpu_seconds=round(cpu, 3),
            # Judge0 CE >= 1.13.0 refuses a wall time below 1 s (422), which would turn every
            # submission to a short-limit problem into judge_error.
            wall_seconds=max(MIN_WALL_SECONDS, round(cpu * self._config.wall_time_factor, 3)),
            memory_kb=int(
                request.memory_limit_mb * 1024 * self._config.memory_factors[request.language]
            ),
            max_file_kb=math.ceil(self._config.max_output_bytes / 1024),
        )

    # ------------------------------------------------------------------ Executor

    def run(self, request: TestRequest, heartbeat: Heartbeat) -> TestOutcome:
        """Run one test: submit it, poll (beating the heart; `LeaseLost` when the lease is
        gone) and map Judge0's result. A compilation error is returned as `CompileFailure`."""
        language_id = self._config.language_ids.get(request.language)
        if language_id is None:
            raise Judge0NotConfigured("no Judge0 language id is configured for this language")
        limits = self.limits(request)
        [token] = self._submit(request.source_code, language_id, limits, [request.input])
        [result] = self._wait([token], heartbeat)
        if result.status == COMPILATION_ERROR:
            return CompileFailure(result.compile_output or "")
        return self._run(result, limits)

    # ------------------------------------------------------------------ mapping

    def _run(self, result: _Result, limits: Limits) -> TestRun:
        status = result.status
        if status in (ACCEPTED, WRONG_ANSWER):
            stdout = result.stdout or b""
            if len(stdout) > self._config.max_output_bytes:
                return TestRun(RunStatus.runtime_error, "", result.time_ms, result.memory_kb)
            return TestRun(
                RunStatus.completed,
                stdout.decode("utf-8", errors="replace"),
                result.time_ms,
                result.memory_kb,
            )
        if status == TIME_LIMIT_EXCEEDED:
            return TestRun(RunStatus.time_limit, "", result.time_ms, result.memory_kb)
        if status == SIGXFSZ:  # output limit: runtime_error for now (E5)
            return TestRun(RunStatus.runtime_error, "", result.time_ms, result.memory_kb)
        if status in RUNTIME_ERRORS:
            # UNVERIFIED inference: Judge0 has no memory-limit status.
            if result.memory_kb is not None and result.memory_kb >= limits.memory_kb:
                return TestRun(RunStatus.memory_limit, "", result.time_ms, result.memory_kb)
            return TestRun(RunStatus.runtime_error, "", result.time_ms, result.memory_kb)
        # 13 internal error, 14 exec format error, or anything unknown: the judge's fault.
        return TestRun(RunStatus.internal_error, "", result.time_ms, result.memory_kb)

    # ------------------------------------------------------------------ HTTP

    def _submit(
        self, source: str, language_id: int, limits: Limits, inputs: Sequence[str]
    ) -> list[str]:
        encoded_source = _b64(source)
        body = {
            "submissions": [
                {
                    "language_id": language_id,
                    "source_code": encoded_source,
                    "stdin": _b64(stdin),
                    "cpu_time_limit": limits.cpu_seconds,
                    "wall_time_limit": limits.wall_seconds,
                    "memory_limit": limits.memory_kb,
                    "max_file_size": limits.max_file_kb,
                    "enable_network": False,
                    "redirect_stderr_to_stdout": False,
                }
                for stdin in inputs
            ]
        }
        data = self._send("POST", "/submissions/batch", {"base64_encoded": "true"}, body)
        if not isinstance(data, list) or len(data) != len(inputs):
            raise Judge0ProtocolError("unexpected submission response")
        tokens = [item.get("token") if isinstance(item, dict) else None for item in data]
        if not all(isinstance(t, str) and t for t in tokens):
            raise Judge0ProtocolError("unexpected submission response")
        return [str(t) for t in tokens]

    def _wait(self, tokens: Sequence[str], heartbeat: Heartbeat) -> list[_Result]:
        deadline = self._clock() + self._config.max_wait_seconds
        params = {"tokens": ",".join(tokens), "base64_encoded": "true", "fields": _FIELDS}
        while True:
            data = self._send("GET", "/submissions/batch", params, None)
            results = self._parse(data, tokens)
            if all(r.status not in _PENDING for r in results):
                return results
            if not heartbeat():
                raise LeaseLost
            if self._clock() >= deadline:
                raise JudgeTimeout
            self._sleep(self._config.poll_seconds)

    def _parse(self, data: object, tokens: Sequence[str]) -> list[_Result]:
        items = data.get("submissions") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != len(tokens):
            raise Judge0ProtocolError("unexpected status response")
        results: list[_Result] = []
        try:
            for token, item in zip(tokens, items, strict=True):
                if not isinstance(item, dict) or item.get("token") != token:
                    raise ValueError("token mismatch")
                status = item.get("status_id")
                if not isinstance(status, int) or isinstance(status, bool):
                    raise ValueError("bad status")
                raw_time, memory = item.get("time"), item.get("memory")
                time_ms = None if raw_time is None else round(float(raw_time) * 1000)
                if memory is not None and (not isinstance(memory, int) or isinstance(memory, bool)):
                    raise ValueError("bad memory")
                compile_output = _unb64(item.get("compile_output"))
                results.append(
                    _Result(
                        token=token,
                        status=status,
                        stdout=_unb64(item.get("stdout")),
                        time_ms=time_ms,
                        memory_kb=memory,
                        compile_output=None
                        if compile_output is None
                        else compile_output.decode("utf-8", errors="replace"),
                    )
                )
        except (ValueError, TypeError, binascii.Error):
            results = []
        if len(results) != len(tokens):
            raise Judge0ProtocolError("unexpected status response")
        return results

    def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, str],
        body: object | None,
    ) -> object:
        """One bounded request. Transport errors become detail-free exceptions raised
        outside the handler; nothing about the request, response or token is kept."""
        failure = ""  # "timeout" or "unreachable"; the httpx error itself is dropped
        status_code = 0
        payload = bytearray()
        too_large = False
        # httpx times each read separately; a server trickling bytes is also cut off once
        # the whole response has taken longer than one timeout.
        deadline = self._clock() + self._config.timeout_seconds
        try:
            with self._client.stream(
                method,
                path,
                params=params,
                json=body,
                headers=self._headers,
            ) as response:
                status_code = response.status_code
                for chunk in response.iter_bytes():
                    payload += chunk
                    if len(payload) > self._config.max_response_bytes:
                        too_large = True
                        break
                    if self._clock() > deadline:
                        failure = "timeout"
                        break
        except httpx.TimeoutException:
            failure = "timeout"
        except httpx.HTTPError:
            failure = "unreachable"
        if failure == "timeout":
            raise JudgeTimeout
        if failure:
            raise Judge0Error("Judge0 could not be reached")
        if status_code in (401, 403):
            raise Judge0AuthError("Judge0 rejected the credentials")
        if status_code == 429 or status_code >= 500:
            raise Judge0Error("Judge0 is unavailable")
        if not 200 <= status_code < 300:
            raise Judge0ProtocolError("Judge0 refused the request")
        if too_large:
            raise Judge0ProtocolError("Judge0 response too large")
        try:
            return json.loads(payload)
        except ValueError:
            pass
        raise Judge0ProtocolError("Judge0 response is not JSON")
