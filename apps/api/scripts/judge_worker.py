"""Run the persistent judge worker (judge/runner.py) against a Judge0 CE server.

Usage (from apps/api):
  uv run python -m scripts.judge_worker            run until SIGTERM/SIGINT
  uv run python -m scripts.judge_worker --check alive|ready
                                                   health check from JUDGE_WORKER_HEALTH_FILE

Runs as its own process (never inside the API) and never runs submitted code: tests run on
the Judge0 server only. Exit status: 0 after a clean stop; 2 when Judge0 is not configured
(JUDGE0_URL or every language id missing), without claiming anything; for `--check`, 0
healthy and 1 not. A first SIGTERM/SIGINT stops claiming and lets a job in flight finish for
up to JUDGE_SHUTDOWN_GRACE_SECONDS; a second one ends the process at once (that job is then
recovered after its lease expires). Settings come from the environment (`.env.example`).
"""

import argparse
import logging
import signal
import time
from pathlib import Path
from types import FrameType

from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import get_engine
from app.core.logs import configure_logging
from app.modules.judge import queue, runner
from app.modules.judge.judge0 import Judge0Config, Judge0Executor, Judge0NotConfigured
from app.modules.judge.worker import WorkerConfig

NOT_CONFIGURED = 2


def health_max_age(settings: Settings) -> float:
    """The longest a running worker can go without updating its health file: one backoff
    wait, or one Judge0 poll interval plus one HTTP request while a job is in flight."""
    return (
        max(settings.judge_backoff_max_seconds, settings.judge0_poll_seconds)
        + 2 * settings.judge0_timeout_seconds
        + 30
    )


def check(settings: Settings, what: str) -> int:
    if not settings.judge_worker_health_file:
        return 1
    alive, ready = runner.read_health(
        Path(settings.judge_worker_health_file), health_max_age(settings), time.time()
    )
    return 0 if (ready if what == "ready" else alive) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KyrgyzSTEM judge worker")
    parser.add_argument("--check", choices=("alive", "ready"))
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.check:
        return check(settings, args.check)

    configure_logging(settings.log_level, settings.log_format)
    try:
        judge0 = Judge0Config.from_settings(settings)
    except Judge0NotConfigured:
        judge0 = None
    if judge0 is None or not judge0.language_ids:
        queue._log(logging.ERROR, "judge.worker_not_configured", category="not_configured")
        return NOT_CONFIGURED

    stop = runner.Stop()

    def on_signal(_signum: int, _frame: FrameType | None) -> None:
        if stop.requested:
            raise SystemExit(1)  # second signal: stop now; the lease expires and is swept
        stop.request()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    path = settings.judge_worker_health_file
    health = runner.Health(Path(path) if path else None)
    engine = get_engine()
    executor = Judge0Executor(judge0)
    try:
        return runner.run(
            sessionmaker(bind=engine),
            executor,
            executor.probe,
            # Claim only what this worker can run: its configured Judge0 language ids.
            WorkerConfig.from_settings(settings, judge0.language_ids),
            runner.RunnerConfig.from_settings(settings),
            stop,
            health,
        )
    finally:
        executor.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
