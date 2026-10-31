"""The PostgreSQL submission queue: claim, lease, finish, fail, release and sweep.

Database operations only. Nothing here runs, compiles or judges code, and nothing here
produces a verdict: `finish` stores a `SubmissionResult` the judging core computed. The
judge worker (judge/worker.py, judge/runner.py) is the only caller. With `NoJudge` (no
`JUDGE_LANGUAGES`), new submissions are `not_judged`, so nothing is queued.

Every operation is one short transaction from a session factory (`with db.begin()`): it
commits as a whole or, on any error, rolls back as a whole. Times are PostgreSQL's
`clock_timestamp()`, never the worker's clock.

Fencing. `claim` returns the submission's new `judge_attempts` as a token. Every later write
(`renew`, `finish`, `fail`, `release`) requires all of: the same id, `state = 'judging'`, the
same `judge_attempts`, and `lease_expires_at > clock_timestamp()`. So a worker whose lease
has expired cannot write, even before the sweeper runs; one whose job was claimed again has
an old token; and the row lock taken by the UPDATE makes PostgreSQL re-check all of it after
any wait. A write that matches no row changes nothing and returns False / None.

Snapshots are only read and compared (`judge/snapshot.py`), never regenerated or rebound.
"""

import logging
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.judge import snapshot as snap
from app.modules.judge.core import SAMPLE_GROUP, TOTAL_POINTS, SubmissionResult
from app.modules.olympiad import service as olympiad
from app.modules.olympiad.models import Language, Scoring, SubmissionState, Verdict

SessionFactory = Callable[[], Session]
logger = logging.getLogger("kyrgyzstem.judging")

SWEEP_LIMIT = 50  # expired leases recovered per sweep (E2)
COMPILE_MESSAGE_LIMIT = 4096  # characters, as the submissions CHECK allows
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # keeps \t and \n
# The only log fields this module writes: no source, tests, compiler output, exception
# messages, user identifiers or secrets.
LOG_FIELDS = frozenset({"submission", "attempt", "category", "state", "verdict", "duration_ms"})


class QueueDatabaseError(RuntimeError):
    """A database error in a queue operation. The driver's error is dropped, not chained:
    PostgreSQL's error detail can quote row values (submission source, test data, compiler
    output), so it is never logged, returned or attached as a cause."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"judge queue database error during {operation}")
        self.operation = operation


class InvalidResult(ValueError):
    """A result that does not fit the claimed job's verified snapshot. Raised before any
    write; the worker records it as `invalid_test_data` (a judge fault, never a verdict)."""


class Failure(StrEnum):
    """Internal error categories (logs only; users see the generic `judge_error`)."""

    unverifiable_snapshot = "unverifiable_snapshot"  # NULL stored hash or invalid data
    stale_snapshot = "stale_snapshot"  # the test set or judging rules changed
    attempts_exhausted = "attempts_exhausted"  # queued at or past the attempt limit
    invalid_test_data = "invalid_test_data"
    judge_unavailable = "judge_unavailable"
    judge_timeout = "judge_timeout"
    judge_internal = "judge_internal"
    worker_stopping = "worker_stopping"  # released by a worker shutting down
    unexpected = "unexpected"


@dataclass(frozen=True)
class QueueConfig:
    lease_seconds: int
    max_attempts: int
    # The languages this worker can execute (it has a Judge0 language id for each). `claim`
    # takes only these; a submission in any other language stays queued, untouched.
    languages: frozenset[Language]

    @classmethod
    def from_settings(cls, settings: Settings, languages: Iterable[Language]) -> "QueueConfig":
        return cls(settings.judge_lease_seconds, settings.judge_max_attempts, frozenset(languages))


@dataclass(frozen=True)
class Claim:
    submission_id: uuid.UUID
    token: int  # the attempt number this worker owns


@dataclass(frozen=True)
class Job:
    """A claimed submission with its verified snapshot, all read in one database snapshot.
    Expected outputs stay with the worker; only inputs will ever go to the judge.

    The submitted source and the test data are left out of `repr()` (and so of `str()` and
    of anything that formats a job), so a stray log line or exception message cannot leak
    them; they still take part in equality like every other field."""

    claim: Claim
    language: Language
    source_code: str = field(repr=False)
    snapshot: snap.JudgingSnapshot
    inputs: Mapping[tuple[int, int], str] = field(repr=False)  # (group, test) -> input
    expected_outputs: Mapping[tuple[int, int], str] = field(repr=False)
    group_ids: Mapping[int, uuid.UUID]  # group ordinal -> test_groups.id at load time


def _log(level: int, event: str, **fields: object) -> None:
    if not set(fields) <= LOG_FIELDS:
        raise ValueError(f"log fields not allowed: {sorted(set(fields) - LOG_FIELDS)}")
    logger.log(level, event, extra={"fields": {k: str(v) for k, v in fields.items()}})


# Fencing, in every write: "id = :id AND state = 'judging' AND judge_attempts = :token AND
# lease_expires_at > clock_timestamp()". The statements are written out in full (no string
# assembly); tests/test_judge_queue.py checks each carries the whole fence.
# Every queued row in one of the worker's languages is claimable, also one already at or past
# the attempt limit (the limit was lowered, or a requeue kept its count): `claim` then ends it
# through the fenced `fail`, so it can never sit in the queue forever and never becomes a
# usable job. A row in another language is never touched: no attempt is used up on it.
_CLAIM = text(
    "UPDATE submissions SET state = 'judging', judge_attempts = judge_attempts + 1, "
    "lease_expires_at = clock_timestamp() + make_interval(secs => :lease) "
    "WHERE id = (SELECT id FROM submissions WHERE state = 'queued' "
    "AND language = ANY(:languages) "
    "ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1) "
    "RETURNING id, judge_attempts"
)
_RENEW = text(
    "UPDATE submissions SET lease_expires_at = clock_timestamp() + make_interval(secs => :lease) "
    "WHERE id = :id AND state = 'judging' AND judge_attempts = :token "
    "AND lease_expires_at > clock_timestamp() RETURNING id"
)
_FINISH = text(
    "UPDATE submissions SET state = 'done', verdict = :verdict, score = :score, "
    "compile_message = :compile_message, judged_at = clock_timestamp(), lease_expires_at = NULL "
    "WHERE id = :id AND state = 'judging' AND judge_attempts = :token "
    "AND lease_expires_at > clock_timestamp() RETURNING id"
)
_INSERT_RESULT = text(
    "INSERT INTO submission_group_results (submission_id, group_ordinal, points, group_id, "
    "verdict, score, max_time_ms, max_memory_kb) VALUES (:id, :ordinal, :points, "
    # Link the group only if it still exists for this problem (a re-import may have
    # replaced it); the copied ordinal and points keep the result meaningful either way.
    "(SELECT g.id FROM test_groups g JOIN submissions s ON s.problem_id = g.problem_id "
    "WHERE g.id = :group_id AND s.id = :id), "
    ":verdict, :score, :max_time_ms, :max_memory_kb)"
)
_FAIL = text(
    "UPDATE submissions SET state = 'judge_error', lease_expires_at = NULL "
    "WHERE id = :id AND state = 'judging' AND judge_attempts = :token "
    "AND lease_expires_at > clock_timestamp() RETURNING id"
)
# Below the attempt limit back to the queue, at the limit a terminal judge_error.
_RELEASE = text(
    "UPDATE submissions SET state = CASE WHEN judge_attempts >= :max THEN 'judge_error' "
    "ELSE 'queued' END, lease_expires_at = NULL "
    "WHERE id = :id AND state = 'judging' AND judge_attempts = :token "
    "AND lease_expires_at > clock_timestamp() RETURNING state"
)
_SWEEP = text(
    "UPDATE submissions s SET state = CASE WHEN s.judge_attempts >= :max THEN 'judge_error' "
    "ELSE 'queued' END, lease_expires_at = NULL "
    "FROM (SELECT id FROM submissions WHERE state = 'judging' "
    "AND lease_expires_at <= clock_timestamp() ORDER BY lease_expires_at, id "
    "FOR UPDATE SKIP LOCKED LIMIT :limit) expired "
    "WHERE s.id = expired.id RETURNING s.id, s.judge_attempts, s.state"
)
_OWNED = text(
    "SELECT state, judge_attempts, lease_expires_at > clock_timestamp() AS leased, "
    "tests_hash, language, source_code, problem_id FROM submissions WHERE id = :id"
)
_TEST_DATA = text(
    "SELECT g.id, g.ordinal, c.ordinal, c.input, c.expected_output FROM test_cases c "
    "JOIN test_groups g ON g.id = c.group_id AND g.problem_id = c.problem_id "
    "WHERE c.problem_id = :problem_id"
)


def _run[T](
    factory: SessionFactory,
    operation: str,
    work: Callable[[Session], T],
    claim_: Claim | None = None,
) -> T:
    """Run `work` in one transaction: committed if it returns, rolled back on any error. A
    database error is replaced by `QueueDatabaseError`, raised outside the handler so the
    original (and its row detail) is neither logged nor chained."""
    try:
        with factory() as db, db.begin():
            return work(db)
    except SQLAlchemyError:
        pass  # rolled back by db.begin(); the driver's message is dropped here
    ids: dict[str, object] = {}
    if claim_ is not None:
        ids = {"submission": claim_.submission_id, "attempt": claim_.token}
    _log(logging.ERROR, "judge.database_error", category=operation, **ids)
    raise QueueDatabaseError(operation)


def claim(factory: SessionFactory, config: QueueConfig) -> Claim | None:
    """Take the oldest queued submission in one of `config.languages` that may still be
    judged, or None (always None without languages). Rows locked by another claimer are
    skipped (SKIP LOCKED), never waited for; rows in other languages stay queued.

    A queued row already at or past `max_attempts` is claimed too (`queued -> judging`, the
    only way out of the queue) and at once ended with the fenced `fail` (`judging ->
    judge_error`): it is never returned, so no source or test data is loaded for it and it
    is never judged. Its attempt count is not reset. Up to `SWEEP_LIMIT` such rows are ended
    per call before the next usable one is returned."""
    params = {
        "lease": config.lease_seconds,
        "languages": sorted(language.value for language in config.languages),
    }
    for _ in range(SWEEP_LIMIT + 1):
        row = _run(factory, "claim", lambda db: db.execute(_CLAIM, params).one_or_none())
        if row is None:
            return None
        claimed = Claim(row[0], row[1])
        _log(logging.INFO, "judge.claimed", submission=claimed.submission_id, attempt=claimed.token)
        if claimed.token <= config.max_attempts:
            return claimed
        fail(factory, claimed, Failure.attempts_exhausted)
    return None


def _fenced(
    factory: SessionFactory, operation: str, statement: object, claim_: Claim, **params: object
) -> object | None:
    values = {"id": claim_.submission_id, "token": claim_.token, **params}
    row = _run(
        factory,
        operation,
        lambda db: db.execute(statement, values).one_or_none(),  # type: ignore[call-overload]
        claim_,
    )
    return None if row is None else row[0]


def _lease_lost(claim_: Claim) -> None:
    _log(
        logging.WARNING,
        "judge.lease_lost",
        submission=claim_.submission_id,
        attempt=claim_.token,
    )


def renew(factory: SessionFactory, config: QueueConfig, claim_: Claim) -> bool:
    """Extend the lease. False when this attempt no longer owns a valid lease."""
    owned = _fenced(factory, "renew", _RENEW, claim_, lease=config.lease_seconds) is not None
    if not owned:
        _lease_lost(claim_)
    return owned


def clean_compile_message(message: str | None) -> str | None:
    """Compiler output as stored: control characters (except tab and newline) removed and
    cut to the column's limit. It is never logged."""
    if message is None:
        return None
    return _CONTROL.sub("", message)[:COMPILE_MESSAGE_LIMIT]


def validate_result(job: Job, result: SubmissionResult, compile_message: str | None) -> None:
    """Check a proposed result against the job's verified snapshot, following the judging
    core's own rules (`core.judge_submission`): every snapshot group exactly once with its
    points; a group's score is its points if it passed (`accepted`), else 0; `accepted`
    means every group passed; the total is the sum of the scored groups (ioi) or 100 when
    every group passed, else 0 (binary); a compile error has score 0 and no groups and is
    the only result that may carry a compiler message. Raises `InvalidResult`."""
    if result.state != SubmissionState.done or result.verdict is None or result.score is None:
        raise InvalidResult("finish stores done results only; use fail() for judge errors")
    if result.verdict == Verdict.compile_error:
        if result.score != 0 or result.groups:
            raise InvalidResult("a compile error has score 0 and no group results")
        return
    if compile_message is not None:
        raise InvalidResult("a compile message belongs to a compile_error result only")
    points = {g.ordinal: g.points for g in job.snapshot.groups}
    ordinals = [g.ordinal for g in result.groups]
    if len(set(ordinals)) != len(ordinals) or set(ordinals) != set(points):
        raise InvalidResult("the result's groups are not exactly the snapshot's groups")
    for group in result.groups:
        if group.points != points[group.ordinal]:
            raise InvalidResult("a group's points differ from the snapshot")
        if group.score != (group.points if group.verdict == Verdict.accepted else 0):
            raise InvalidResult("a group's score does not match its verdict and points")
    all_passed = all(g.verdict == Verdict.accepted for g in result.groups)
    if (result.verdict == Verdict.accepted) != all_passed:
        raise InvalidResult("the verdict does not match the group results")
    if Scoring(job.snapshot.scoring) == Scoring.binary:
        total = TOTAL_POINTS if all_passed else 0
    else:
        total = sum(g.score for g in result.groups if g.ordinal != SAMPLE_GROUP)
    if result.score != total:
        raise InvalidResult("the total score does not match the group results")


def finish(
    factory: SessionFactory,
    job: Job,
    result: SubmissionResult,
    compile_message: str | None = None,
) -> bool:
    """Store a judged result: the submission and its group results in one transaction, only
    while this attempt holds a valid lease. The result is first checked against the job's
    snapshot (`validate_result`); an invalid one raises `InvalidResult` before any write.
    Any database error rolls everything back and raises `QueueDatabaseError`; the
    submission then stays `judging` until its lease expires and the sweeper recovers it."""
    validate_result(job, result, compile_message)
    claim_ = job.claim
    verdict = result.verdict
    if verdict is None:  # checked by validate_result; narrows the type
        raise InvalidResult("a done result has a verdict")

    def write(db: Session) -> bool:
        row = db.execute(
            _FINISH,
            {
                "id": claim_.submission_id,
                "token": claim_.token,
                "verdict": verdict.value,
                "score": result.score,
                "compile_message": clean_compile_message(compile_message),
            },
        ).one_or_none()
        if row is None:
            return False
        for group in result.groups:
            db.execute(
                _INSERT_RESULT,
                {
                    "id": claim_.submission_id,
                    "ordinal": group.ordinal,
                    "points": group.points,
                    "group_id": job.group_ids.get(group.ordinal),
                    "verdict": group.verdict.value,
                    "score": group.score,
                    "max_time_ms": group.max_time_ms,
                    "max_memory_kb": group.max_memory_kb,
                },
            )
        return True

    if not _run(factory, "finish", write, claim_):
        _lease_lost(claim_)
        return False
    _log(
        logging.INFO,
        "judge.finished",
        submission=claim_.submission_id,
        attempt=claim_.token,
        state=SubmissionState.done.value,
        verdict=verdict.value,
    )
    return True


def fail(factory: SessionFactory, claim_: Claim, category: Failure) -> bool:
    """Terminal `judge_error`: no verdict, score or group results."""
    if _fenced(factory, "fail", _FAIL, claim_) is None:
        _lease_lost(claim_)
        return False
    _log(
        logging.WARNING,
        "judge.failed",
        submission=claim_.submission_id,
        attempt=claim_.token,
        category=category.value,
        state=SubmissionState.judge_error.value,
    )
    return True


def release(
    factory: SessionFactory, config: QueueConfig, claim_: Claim, category: Failure
) -> SubmissionState | None:
    """Give a job back after a temporary failure: `queued` below the attempt limit, else a
    terminal `judge_error`. None when this attempt no longer owns the lease."""
    state = _fenced(factory, "release", _RELEASE, claim_, max=config.max_attempts)
    if state is None:
        _lease_lost(claim_)
        return None
    released = SubmissionState(str(state))
    _log(
        logging.WARNING,
        "judge.released",
        submission=claim_.submission_id,
        attempt=claim_.token,
        category=category.value,
        state=released.value,
    )
    return released


def sweep(factory: SessionFactory, config: QueueConfig, limit: int = SWEEP_LIMIT) -> int:
    """Recover expired leases (crashed or stalled workers). Rows another transaction holds
    are skipped; a live lease is never touched."""
    params = {"max": config.max_attempts, "limit": limit}
    rows = _run(factory, "sweep", lambda db: list(db.execute(_SWEEP, params).all()))
    for submission_id, attempt, state in rows:
        _log(
            logging.WARNING,
            "judge.swept",
            submission=submission_id,
            attempt=attempt,
            state=state,
        )
    return len(rows)


def load_job(factory: SessionFactory, claim_: Claim) -> Job | Failure | None:
    """Read everything the judge needs in one REPEATABLE READ, read-only transaction, so the
    submission, its stored hash, the problem's snapshot and the test data are one consistent
    view, and verify it. Returns:

    * None when this attempt no longer owns a valid lease (the caller stops);
    * `Failure.unverifiable_snapshot` for a NULL stored hash or invalid problem data;
    * `Failure.stale_snapshot` when the problem's snapshot no longer matches the stored hash;
    * a `Job` otherwise. The stored hash is only compared, never rewritten."""

    def read(db: Session) -> Job | Failure | None:
        db.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
        owned = db.execute(_OWNED, {"id": claim_.submission_id}).one_or_none()
        if (
            owned is None
            or owned.state != SubmissionState.judging.value
            or owned.judge_attempts != claim_.token
            or not owned.leased
        ):
            return None
        if owned.tests_hash is None:
            return Failure.unverifiable_snapshot
        try:
            current = olympiad.judging_snapshot(db, owned.problem_id)
            if current is None:
                return Failure.unverifiable_snapshot
            if not snap.matches(owned.tests_hash, current):
                return Failure.stale_snapshot
            rows = db.execute(_TEST_DATA, {"problem_id": owned.problem_id}).all()
            inputs = {(g, o): i for _, g, o, i, _e in rows}
            expected = {(g, o): e for _, g, o, _i, e in rows}
            group_ids = {g: gid for gid, g, *_ in rows}
            snap.group_specs(current, expected)  # exactly the snapshot's tests, or raises
        except snap.SnapshotError:
            return Failure.unverifiable_snapshot
        return Job(
            claim=claim_,
            language=Language(owned.language),
            source_code=owned.source_code,
            snapshot=current,
            inputs=inputs,
            expected_outputs=expected,
            group_ids=group_ids,
        )

    return _run(factory, "load_job", read, claim_)
