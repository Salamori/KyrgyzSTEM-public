"""The persistent judge worker: `process_one` in a loop, with readiness, backoff and shutdown.

Entry point: scripts/judge_worker.py. It runs as its own process, never inside the API, and
it never runs submitted code: that is the executor's job (an isolated Judge0 server).

* **Readiness first.** Before each claim the executor's `probe()` must succeed (Judge0
  answers and offers the configured languages). While it fails, nothing is claimed, so an
  outage does not use up the queued jobs' attempts; the worker backs off and probes again.
* **Pacing.** After a job, the next claim follows at once (more work may be queued). With
  nothing queued, it waits `JUDGE_POLL_SECONDS`. After a temporary failure (a released job,
  a database error, an unforeseen error) it backs off exponentially from the poll interval
  up to `JUDGE_BACKOFF_MAX_SECONDS`, and a success resets the backoff. It never spins.
* **Shutdown.** `Stop.request()` (SIGTERM or SIGINT in the entry point) stops all further
  claims at once. A job in flight may finish for up to `JUDGE_SHUTDOWN_GRACE_SECONDS`; after
  that it is released at its next heartbeat (`worker_stopping`). A second signal ends the
  process at once (the entry point lets it raise): the job then keeps its lease until it
  expires, and the sweeper of any worker requeues it. Either way no stale result is stored:
  every write is fenced by the claim (queue.py).
* **Health.** `Health` keeps a small file for liveness and readiness (no network endpoint):
  when it was last updated (by the loop and by every heartbeat of a job in flight) and
  whether the judge was ready at the last probe. It holds no identifiers or secrets.

Logs go through the queue's field allowlist (`queue.LOG_FIELDS`), like the rest of judging.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.modules.judge import queue, worker
from app.modules.judge.worker import Executor, Outcome, WorkerConfig

_AGAIN = frozenset({Outcome.finished, Outcome.failed, Outcome.lease_lost})
_BACK_OFF = frozenset({Outcome.released, Outcome.database_error, Outcome.unexpected})


@dataclass(frozen=True)
class RunnerConfig:
    poll_seconds: float
    backoff_max_seconds: float
    shutdown_grace_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "RunnerConfig":
        return cls(
            settings.judge_poll_seconds,
            settings.judge_backoff_max_seconds,
            settings.judge_shutdown_grace_seconds,
        )

    def backoff(self, failures: int) -> float:
        """The wait after `failures` consecutive failures (1, 2, ...): doubling, capped."""
        return float(min(self.backoff_max_seconds, self.poll_seconds * 2 ** min(failures - 1, 20)))


class Stop:
    """Shutdown request, safe to set from a signal handler. `wait()` returns early when a
    stop is requested, so an idle or backing-off worker stops at once."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._event = threading.Event()
        self._clock = clock
        self.requested_at: float | None = None

    def request(self) -> None:
        if self.requested_at is None:
            self.requested_at = self._clock()
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def grace_over(self, grace_seconds: float) -> bool:
        return self.requested_at is not None and self._clock() - self.requested_at >= grace_seconds

    def wait(self, seconds: float) -> None:
        self._event.wait(seconds)


class Health:
    """The worker's health file: `{"updated": <unix time>, "ready": <bool>}`, written
    atomically and at most every `every_seconds`. No file when `path` is None."""

    def __init__(
        self,
        path: Path | None,
        every_seconds: float = 5.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path, self._every, self._clock = path, every_seconds, clock
        self._written_at: float | None = None
        self.ready = False

    def touch(self, *, force: bool = False) -> None:
        if self._path is None:
            return
        now = self._clock()
        if not force and self._written_at is not None and now - self._written_at < self._every:
            return
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps({"updated": now, "ready": self.ready}), encoding="utf-8")
        tmp.replace(self._path)
        self._written_at = now

    def set_ready(self, ready: bool) -> None:
        changed = ready != self.ready
        self.ready = ready
        self.touch(force=changed)


def read_health(path: Path, max_age_seconds: float, now: float) -> tuple[bool, bool]:
    """(alive, ready) from a health file: alive when updated within `max_age_seconds`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        updated, ready = float(data["updated"]), data["ready"] is True
    except (OSError, ValueError, KeyError, TypeError):
        return False, False
    alive = 0 <= now - updated <= max_age_seconds
    return alive, alive and ready


def _log(event: str, **fields: object) -> None:
    queue._log(logging.INFO, event, **fields)  # the queue's allowlisted logger


def run(
    factory: queue.SessionFactory,
    executor: Executor,
    probe: Callable[[], bool],
    worker_config: WorkerConfig,
    config: RunnerConfig,
    stop: Stop,
    health: Health | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Process jobs until `stop` is requested. Returns 0 (the process's exit status)."""
    health = health or Health(None)

    def stopping() -> bool:
        health.touch()  # a job in flight keeps the worker visibly alive
        return stop.grace_over(config.shutdown_grace_seconds)

    _log("judge.worker_started")
    failures = 0
    while not stop.requested:
        health.touch()
        if not probe():
            if health.ready or failures == 0:
                _log("judge.worker_not_ready", category="judge_unavailable")
            health.set_ready(False)
            failures += 1
            stop.wait(config.backoff(failures))
            continue
        if not health.ready:
            _log("judge.worker_ready")
            health.set_ready(True)
        outcome = worker.process_one(factory, executor, worker_config, clock, stopping)
        if outcome in _AGAIN:
            failures = 0
        elif outcome == Outcome.idle:
            failures = 0
            stop.wait(config.poll_seconds)
        elif outcome in _BACK_OFF:
            failures += 1
            wait = config.backoff(failures)
            _log("judge.worker_backoff", category=outcome.value, duration_ms=int(wait * 1000))
            stop.wait(wait)
    health.set_ready(False)
    _log("judge.worker_stopped")
    return 0
