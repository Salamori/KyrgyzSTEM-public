"""Step 5.3.3: the PostgreSQL submission queue (claim, lease, finish, fail, release, sweep).

These tests use their own scratch database (created next to TEST_DATABASE_URL's, dropped at
the end) because claiming and locking need real, independently committing connections.
Submissions are created through the service, so their snapshot binding is real. No code is
judged or executed: results are computed by the pure judging core from synthetic outputs.
"""

import ast
import logging
import threading
import time
import traceback
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.modules.judge import core, queue
from app.modules.judge.core import GroupResult, RunStatus, SubmissionResult, TestRun
from app.modules.judge.queue import Claim, Failure, QueueConfig, QueueDatabaseError
from app.modules.judge.snapshot import group_specs
from app.modules.olympiad import service
from app.modules.olympiad.models import (
    Band,
    Language,
    Origin,
    Problem,
    ProblemStatus,
    ProblemTranslation,
    Scoring,
    Submission,
    SubmissionGroupResult,
    SubmissionState,
    TestCase,
    TestGroup,
    Verdict,
)
from app.modules.olympiad.schemas import SubmissionCreate
from app.modules.users.models import Locale, Role, User
from tests.conftest import TEST_DATABASE_URL
from tests.test_migrations import _migrate

CONFIG = QueueConfig(lease_seconds=120, max_attempts=3, languages=frozenset(Language))
V1 = "0123456789abcdef" * 4
SOURCE = "SECRET-SOURCE-print(42)"
HIDDEN = "HIDDEN-INPUT-never-logged"
S = SubmissionState


# ------------------------------------------------------------------ settings (no database)


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # _env_file=None: never reads .env


def test_judge_settings_defaults() -> None:
    s = _settings()
    assert (s.judge_lease_seconds, s.judge_renew_seconds, s.judge_max_attempts) == (120, 30, 3)
    assert s.judge_poll_seconds == 2.0
    assert QueueConfig.from_settings(s, Language) == CONFIG
    assert QueueConfig.from_settings(s, [Language.cpp]).languages == {Language.cpp}


@pytest.mark.parametrize(
    "values",
    [
        {"judge_lease_seconds": 30, "judge_renew_seconds": 15},  # smallest lease
        {"judge_lease_seconds": 31, "judge_renew_seconds": 15},  # odd lease: floor of half
        {"judge_lease_seconds": 600, "judge_renew_seconds": 300},  # largest lease
        {"judge_max_attempts": 1},
        {"judge_max_attempts": 10},
        {"judge_poll_seconds": 0.5},
        {"judge_poll_seconds": 60},
    ],
)
def test_judge_settings_accept_the_bounds(values: dict[str, Any]) -> None:
    _settings(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"judge_lease_seconds": 29},
        {"judge_lease_seconds": 601, "judge_renew_seconds": 30},
        {"judge_renew_seconds": 4},
        {"judge_lease_seconds": 30, "judge_renew_seconds": 16},  # more than half the lease
        {"judge_lease_seconds": 31, "judge_renew_seconds": 16},
        {"judge_renew_seconds": 61},  # with the default 120 s lease
        {"judge_max_attempts": 0},
        {"judge_max_attempts": 11},
        {"judge_poll_seconds": 0.4},
        {"judge_poll_seconds": 61},
    ],
)
def test_judge_settings_reject_out_of_bounds_values(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _settings(**values)


# ------------------------------------------------------------------ static safety


@pytest.mark.parametrize("module", ["queue.py", "snapshot.py"])
def test_queue_modules_cannot_execute_code(module: str) -> None:
    path = Path(queue.__file__).with_name(module)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned_modules = {"subprocess", "multiprocessing", "ctypes", "importlib", "pty", "shutil"}
    banned_builtins = {"exec", "eval", "compile", "__import__"}  # as bare calls
    banned_methods = {"system", "popen", "spawnl", "spawnv", "execv", "execve", "fork"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned_modules
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_modules
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in banned_builtins, node.func.id
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in banned_methods, node.func.attr


def test_every_write_statement_carries_the_whole_fence() -> None:
    fence = (
        "id = :id AND state = 'judging' AND judge_attempts = :token "
        "AND lease_expires_at > clock_timestamp()"
    )
    for statement in (queue._RENEW, queue._FINISH, queue._FAIL, queue._RELEASE):
        assert fence in str(statement)


def test_claim_is_limited_to_the_workers_languages_by_a_bound_parameter() -> None:
    # A bound array, never assembled into the SQL text.
    assert "AND language = ANY(:languages)" in str(queue._CLAIM)


# ------------------------------------------------------------------ database fixtures


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    base = make_url(TEST_DATABASE_URL)
    name = f"{base.database}_queue_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(base.set(database=name), hide_parameters=True)
    try:
        _migrate(eng, "upgrade", "head")
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE submission_group_results, submissions, test_cases, test_groups, "
                "problem_translations, problems, users CASCADE"
            )
        )
    yield sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def impatient(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Sessions that give up after 1 s of lock waiting: proves SKIP LOCKED never waits."""
    eng = create_engine(engine.url, connect_args={"options": "-c lock_timeout=1000"})
    yield sessionmaker(bind=eng, expire_on_commit=False)
    eng.dispose()


def _problem(db: Session, slug: str = "q", v1: str | None = V1) -> Problem:
    problem = Problem(
        slug=slug,
        status=ProblemStatus.published,
        published_at=func.now(),
        difficulty_band=Band.easy,
        difficulty_rationale="Synthetic queue test problem.",
        time_limit_ms=1000,
        memory_limit_mb=256,
        scoring=Scoring.ioi,
        origin=Origin.original,
        license="CC-BY-4.0",
        attribution_md="Synthetic.",
        authors_md="Test suite",
        generated_with_ai=False,
        tests_hash=v1,
        translations=[
            ProblemTranslation(
                locale=Locale.en, title=slug, legend_md="L", input_md="I", output_md="O"
            )
        ],
    )
    db.add(problem)
    db.flush()
    samples = TestGroup(problem_id=problem.id, ordinal=0, points=0, description_md="Ex")
    full = TestGroup(problem_id=problem.id, ordinal=1, points=100, description_md="All")
    db.add_all([samples, full])
    db.flush()
    db.add_all(
        [
            TestCase(
                problem_id=problem.id,
                group_id=samples.id,
                ordinal=1,
                is_sample=True,
                input="1 2\n",
                expected_output="3\n",
            ),
            TestCase(
                problem_id=problem.id,
                group_id=full.id,
                ordinal=1,
                is_sample=False,
                input=HIDDEN,
                expected_output="secret\n",
            ),
        ]
    )
    db.flush()
    return problem


def _submit(
    factory: sessionmaker[Session], slug: str = "q", language: Language = Language.python
) -> uuid.UUID:
    """A queued submission, created and bound through the service, committed."""
    with factory() as db, db.begin():
        user = db.scalar(select(User).where(User.email == "queue@example.com"))
        if user is None:
            user = User(
                email="queue@example.com",
                password_hash="x",
                display_name="[Synthetic] Queue",
                role=Role.student,
                is_synthetic=True,
            )
            db.add(user)
            db.flush()
        data = SubmissionCreate(problem_slug=slug, language=language, source_code=SOURCE)
        submission = service.create_submission(db, user, data, judge_available=True)
        if submission.tests_hash is None:
            # The API stores an unbindable submission as not_judged. Queue it anyway, as a
            # row from before binding (or a direct database edit) would be: the worker must
            # still fail it closed.
            assert submission.state == S.not_judged
            submission.state = S.queued
        assert submission.state == S.queued
        return submission.id


def _seed(factory: sessionmaker[Session], count: int = 1, v1: str | None = V1) -> list[uuid.UUID]:
    with factory() as db, db.begin():
        _problem(db, v1=v1)
    return [_submit(factory) for _ in range(count)]


def _row(factory: sessionmaker[Session], submission_id: uuid.UUID) -> Submission:
    with factory() as db:
        row = db.get(Submission, submission_id)
        assert row is not None
        db.expunge(row)
        return row


def _results(factory: sessionmaker[Session], submission_id: uuid.UUID) -> list[tuple[Any, ...]]:
    with factory() as db:
        rows = db.execute(
            select(
                SubmissionGroupResult.group_ordinal,
                SubmissionGroupResult.points,
                SubmissionGroupResult.verdict,
                SubmissionGroupResult.score,
                SubmissionGroupResult.group_id.is_not(None),
            )
            .where(SubmissionGroupResult.submission_id == submission_id)
            .order_by(SubmissionGroupResult.group_ordinal)
        ).all()
    return [tuple(r) for r in rows]


def _expire(factory: sessionmaker[Session], submission_id: uuid.UUID) -> None:
    """Simulate a crashed or stalled worker: its lease ran out (PostgreSQL time)."""
    with factory() as db, db.begin():
        db.execute(
            text(
                "UPDATE submissions SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": submission_id},
        )


def _accepted(job: queue.Job) -> SubmissionResult:
    """What the judging core makes of runs whose outputs equal the expected ones."""
    specs = group_specs(job.snapshot, job.expected_outputs)
    runs = {
        g.ordinal: [TestRun(RunStatus.completed, out) for out in g.expected_outputs] for g in specs
    }
    return core.judge_submission(job.snapshot.scoring, specs, runs)


def _job(factory: sessionmaker[Session], claim_: Claim) -> queue.Job:
    job = queue.load_job(factory, claim_)
    assert isinstance(job, queue.Job), job
    return job


# ------------------------------------------------------------------ claim


def test_claim_takes_the_oldest_queued_submission_and_starts_a_lease(
    factory: sessionmaker[Session],
) -> None:
    first, second = _seed(factory, 2)
    claimed = queue.claim(factory, CONFIG)
    assert claimed == Claim(first, 1)
    row = _row(factory, first)
    assert row.state == S.judging and row.judge_attempts == 1
    with factory() as db:
        remaining = db.scalar(
            text("SELECT lease_expires_at - clock_timestamp() FROM submissions WHERE id = :id"),
            {"id": first},
        )
    assert 110 < remaining.total_seconds() <= 120
    assert _row(factory, second).state == S.queued


def test_claim_returns_none_without_queued_work(factory: sessionmaker[Session]) -> None:
    assert queue.claim(factory, CONFIG) is None


CPP_ONLY = QueueConfig(lease_seconds=120, max_attempts=3, languages=frozenset({Language.cpp}))


def test_claim_leaves_a_language_the_worker_cannot_run_queued(
    factory: sessionmaker[Session],
) -> None:
    # A worker with only a C++ id must not take a Python job: claiming it would use up its
    # attempts on Judge0NotConfigured and end it as judge_error.
    (python_job,) = _seed(factory, 1)  # _submit's default language is Python
    assert queue.claim(factory, CPP_ONLY) is None
    row = _row(factory, python_job)
    assert row.state == S.queued
    assert row.judge_attempts == 0 and row.lease_expires_at is None
    no_languages = QueueConfig(lease_seconds=120, max_attempts=3, languages=frozenset())
    assert queue.claim(factory, no_languages) is None  # nothing at all without a language
    assert _row(factory, python_job).state == S.queued


def test_claim_skips_other_languages_and_takes_the_oldest_supported_job(
    factory: sessionmaker[Session],
) -> None:
    (older_python,) = _seed(factory, 1)
    newer_cpp = _submit(factory, language=Language.cpp)
    assert queue.claim(factory, CPP_ONLY) == Claim(newer_cpp, 1)
    assert _row(factory, older_python).state == S.queued
    assert _row(factory, older_python).judge_attempts == 0
    # A worker that runs both still takes the older Python job (ordering unchanged).
    assert queue.claim(factory, CONFIG) == Claim(older_python, 1)


def _set_attempts(factory: sessionmaker[Session], submission_id: uuid.UUID, n: int) -> None:
    with factory() as db, db.begin():
        db.execute(
            text("UPDATE submissions SET judge_attempts = :n WHERE id = :id"),
            {"n": n, "id": submission_id},
        )


@pytest.mark.parametrize("attempts", [3, 4, 7])  # at, just past and well past the limit of 3
def test_queued_job_past_the_attempt_limit_ends_in_judge_error_unjudged(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture, attempts: int
) -> None:
    # e.g. JUDGE_MAX_ATTEMPTS was lowered while the job waited: it must not stay queued.
    [submission] = _seed(factory)
    _set_attempts(factory, submission, attempts)
    with caplog.at_level(logging.INFO, logger="kyrgyzstem.judging"):
        assert queue.claim(factory, CONFIG) is None  # never handed out as a usable job
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.lease_expires_at) == (
        S.judge_error,
        None,
        None,
        None,
    )
    assert row.judge_attempts == attempts + 1  # counted, never reset
    assert _results(factory, submission) == []
    # Through judging only: claimed (queued -> judging), then the fenced fail.
    events = [(r.getMessage(), r.__dict__["fields"]) for r in caplog.records]
    assert [e for e, _ in events] == ["judge.claimed", "judge.failed"]
    assert events[1][1]["category"] == "attempts_exhausted"
    # Its payload is never loadable: the attempt no longer owns a judging lease.
    assert queue.load_job(factory, Claim(submission, attempts + 1)) is None
    assert queue.claim(factory, CONFIG) is None  # and it never comes back


def test_over_limit_jobs_do_not_block_the_next_usable_job(
    factory: sessionmaker[Session],
) -> None:
    exhausted, normal = _seed(factory, 2)  # the exhausted one is older, so claimed first
    _set_attempts(factory, exhausted, 3)
    claimed = queue.claim(factory, CONFIG)
    assert claimed == Claim(normal, 1)  # the below-limit job, as before
    assert _row(factory, exhausted).state == S.judge_error
    job = _job(factory, claimed)
    assert queue.finish(factory, job, _accepted(job))
    assert _row(factory, normal).verdict == Verdict.accepted


def test_job_just_below_the_limit_is_still_judged(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    _set_attempts(factory, submission, 2)  # one attempt left
    claimed = queue.claim(factory, CONFIG)
    assert claimed == Claim(submission, 3)
    assert isinstance(queue.load_job(factory, claimed), queue.Job)


# What `queue.claim` binds for CONFIG, for the tests that hold a claim's transaction open.
CLAIM_PARAMS = {"lease": 120, "languages": sorted(lang.value for lang in CONFIG.languages)}


def test_two_workers_never_claim_the_same_job(
    factory: sessionmaker[Session], impatient: sessionmaker[Session]
) -> None:
    [only] = _seed(factory)
    with factory() as holder:  # worker A claims, its transaction still open
        holder.begin()
        row = holder.execute(queue._CLAIM, CLAIM_PARAMS).one()
        assert row[0] == only
        # Worker B skips the locked row immediately (lock_timeout would fail a wait).
        assert queue.claim(impatient, CONFIG) is None
        holder.commit()
    assert queue.claim(impatient, CONFIG) is None  # judging now, nothing left
    assert _row(factory, only).judge_attempts == 1


def test_two_workers_take_different_jobs(
    factory: sessionmaker[Session], impatient: sessionmaker[Session]
) -> None:
    first, second = _seed(factory, 2)
    with factory() as holder:
        holder.begin()
        assert holder.execute(queue._CLAIM, CLAIM_PARAMS).one()[0] == first
        assert queue.claim(impatient, CONFIG) == Claim(second, 1)
        holder.commit()


# ------------------------------------------------------------------ renew


def test_renew_extends_only_a_live_owned_lease(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    assert queue.renew(factory, CONFIG, claimed)
    assert not queue.renew(factory, CONFIG, Claim(submission, 2))  # wrong token
    _expire(factory, submission)
    before = _row(factory, submission).lease_expires_at
    assert not queue.renew(factory, CONFIG, claimed)  # expired, not yet swept
    assert _row(factory, submission).lease_expires_at == before  # untouched


# ------------------------------------------------------------------ finish


def test_finish_stores_the_submission_and_its_groups_together(
    factory: sessionmaker[Session],
) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    assert queue.finish(factory, job, _accepted(job))
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, Verdict.accepted, 100)
    assert row.lease_expires_at is None and row.judged_at is not None
    assert _results(factory, submission) == [
        (0, 0, Verdict.accepted, 0, True),
        (1, 100, Verdict.accepted, 100, True),
    ]


def test_finish_after_the_lease_expired_writes_nothing(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    _expire(factory, submission)  # the sweeper has not run yet
    assert not queue.finish(factory, job, _accepted(job))
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judging, None, None)
    assert _results(factory, submission) == []


def test_stale_worker_cannot_write_after_a_newer_claim(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    old = queue.claim(factory, CONFIG)
    assert old is not None
    old_job = _job(factory, old)
    _expire(factory, submission)  # worker crashed or stalled
    assert queue.sweep(factory, CONFIG) == 1
    new = queue.claim(factory, CONFIG)
    assert new == Claim(submission, 2)
    for write in (
        lambda: queue.finish(factory, old_job, _accepted(old_job)),
        lambda: queue.fail(factory, old, Failure.judge_internal),
        lambda: queue.release(factory, CONFIG, old, Failure.judge_timeout),
        lambda: queue.renew(factory, CONFIG, old),
    ):
        assert write() in (False, None)
    assert _row(factory, submission).state == S.judging and _results(factory, submission) == []
    new_job = _job(factory, new)
    assert queue.finish(factory, new_job, _accepted(new_job))
    assert _row(factory, submission).verdict == Verdict.accepted


def test_duplicate_finish_writes_once(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    assert queue.finish(factory, job, _accepted(job))
    assert not queue.finish(factory, job, _accepted(job))
    assert len(_results(factory, submission)) == 2


def test_failed_result_insertion_rolls_the_whole_finish_back(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    # A valid result, but the group insert fails in the database (score above points),
    # after the submission row was already updated in the same transaction.
    failing = str(queue._INSERT_RESULT).replace(":verdict, :score,", ":verdict, :score + 1000,")
    assert failing != str(queue._INSERT_RESULT)
    monkeypatch.setattr(queue, "_INSERT_RESULT", text(failing))
    with pytest.raises(QueueDatabaseError):
        queue.finish(factory, job, _accepted(job))
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.judge_attempts) == (S.judging, None, None, 1)
    assert row.lease_expires_at is not None and _results(factory, submission) == []


def test_finish_refuses_a_judge_error_result(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    with pytest.raises(ValueError):
        queue.finish(factory, _job(factory, claimed), core.internal_error())
    assert _row(factory, submission).state == S.judging


def test_finish_refuses_a_compile_message_without_a_compile_error(
    factory: sessionmaker[Session],
) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    with pytest.raises(ValueError):
        queue.finish(factory, job, _accepted(job), compile_message="x")
    assert _row(factory, submission).state == S.judging  # nothing was written


def test_finish_stores_a_cleaned_and_bounded_compile_message(
    factory: sessionmaker[Session],
) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    message = "error:\tline 1\n\x00\x1b[31m" + "x" * 5000
    job = _job(factory, claimed)
    assert queue.finish(factory, job, core.compile_error(), compile_message=message)
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, Verdict.compile_error, 0)
    assert row.compile_message is not None and len(row.compile_message) == 4096
    assert row.compile_message.startswith("error:\tline 1\n[31m")  # tab/newline kept
    assert _results(factory, submission) == []


# ------------------------------------------------------------------ fail and release


def test_fail_is_terminal_with_no_verdict_score_or_results(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    assert queue.fail(factory, claimed, Failure.invalid_test_data)
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.lease_expires_at) == (
        S.judge_error,
        None,
        None,
        None,
    )
    assert _results(factory, submission) == []
    assert queue.claim(factory, CONFIG) is None


def test_release_requeues_until_the_attempt_limit(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    for attempt in (1, 2):
        claimed = queue.claim(factory, CONFIG)
        assert claimed == Claim(submission, attempt)
        assert queue.release(factory, CONFIG, claimed, Failure.judge_unavailable) == S.queued
        assert _row(factory, submission).lease_expires_at is None
    last = queue.claim(factory, CONFIG)
    assert last == Claim(submission, 3)
    assert queue.release(factory, CONFIG, last, Failure.judge_unavailable) == S.judge_error
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judge_error, None, None)
    assert queue.claim(factory, CONFIG) is None


# ------------------------------------------------------------------ sweep


def test_sweep_recovers_only_expired_leases(factory: sessionmaker[Session]) -> None:
    expired, live = _seed(factory, 2)
    first = queue.claim(factory, CONFIG)
    second = queue.claim(factory, CONFIG)
    assert first is not None and second is not None
    _expire(factory, expired)
    assert queue.sweep(factory, CONFIG) == 1
    assert (
        _row(factory, expired).state == S.queued and _row(factory, expired).lease_expires_at is None
    )
    assert _row(factory, live).state == S.judging  # a live lease is never touched


def test_sweep_ends_an_exhausted_job_in_judge_error(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    for _ in range(3):  # three workers crash in a row
        claimed = queue.claim(factory, CONFIG)
        assert claimed is not None
        _expire(factory, submission)
        queue.sweep(factory, CONFIG)
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.judge_attempts) == (S.judge_error, None, None, 3)
    assert queue.claim(factory, CONFIG) is None


def test_sweep_skips_a_row_another_transaction_holds(
    factory: sessionmaker[Session], impatient: sessionmaker[Session]
) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    _expire(factory, submission)
    with factory() as holder:
        holder.begin()
        holder.execute(
            text("SELECT id FROM submissions WHERE id = :id FOR UPDATE"), {"id": submission}
        )
        assert queue.sweep(impatient, CONFIG) == 0  # skipped, not waited for
        holder.rollback()
    assert queue.sweep(impatient, CONFIG) == 1


# ------------------------------------------------------------------ snapshot loading


def test_load_job_returns_the_verified_snapshot_and_test_data(
    factory: sessionmaker[Session],
) -> None:
    [submission] = _seed(factory)
    stored = _row(factory, submission).tests_hash
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    assert job.language == Language.python and job.source_code == SOURCE
    assert job.inputs == {(0, 1): "1 2\n", (1, 1): HIDDEN}
    assert job.expected_outputs == {(0, 1): "3\n", (1, 1): "secret\n"}
    assert set(job.group_ids) == {0, 1}
    assert _row(factory, submission).tests_hash == stored  # only compared, never rewritten


def test_load_job_requires_a_live_owned_lease(factory: sessionmaker[Session]) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    assert queue.load_job(factory, Claim(submission, 2)) is None  # not this attempt
    _expire(factory, submission)
    assert queue.load_job(factory, claimed) is None  # lease ran out


@pytest.mark.parametrize("defect", ["null_hash", "stale", "malformed", "missing_output"])
def test_bad_snapshots_end_in_judge_error_without_results(
    factory: sessionmaker[Session], defect: str
) -> None:
    [submission] = _seed(factory, v1=None if defect == "null_hash" else V1)
    stored = _row(factory, submission).tests_hash
    with factory() as db, db.begin():
        if defect == "stale":
            db.execute(text("UPDATE problems SET time_limit_ms = 2000"))
        elif defect == "malformed":
            db.execute(text("UPDATE test_cases SET is_sample = false"))
        elif defect == "missing_output":
            db.execute(text("DELETE FROM test_cases WHERE NOT is_sample"))
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    expected = Failure.stale_snapshot if defect == "stale" else Failure.unverifiable_snapshot
    assert queue.load_job(factory, claimed) == expected
    assert queue.fail(factory, claimed, expected)
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.judge_error, None, None)
    assert row.tests_hash == stored  # never regenerated or rebound
    assert _results(factory, submission) == []


# ------------------------------------------------------------------ logging


def test_logs_carry_only_allowed_fields_and_no_content(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    first, second, third = _seed(factory, 3)
    with caplog.at_level(logging.INFO, logger="kyrgyzstem.judging"):
        a = queue.claim(factory, CONFIG)
        assert a is not None
        job = _job(factory, a)
        queue.finish(factory, job, _accepted(job))
        b = queue.claim(factory, CONFIG)
        assert b is not None
        queue.fail(factory, b, Failure.stale_snapshot)
        c = queue.claim(factory, CONFIG)
        assert c is not None
        queue.release(factory, CONFIG, c, Failure.judge_timeout)
        queue.renew(factory, CONFIG, c)  # lost: logs judge.lease_lost
        d = queue.claim(factory, CONFIG)
        assert d is not None
        _expire(factory, third)
        queue.sweep(factory, CONFIG)
    records = [r for r in caplog.records if r.name == "kyrgyzstem.judging"]
    events = {r.getMessage() for r in records}
    assert events >= {
        "judge.claimed",
        "judge.finished",
        "judge.failed",
        "judge.released",
        "judge.lease_lost",
        "judge.swept",
    }
    for record in records:
        fields = record.__dict__["fields"]
        assert set(fields) <= queue.LOG_FIELDS
        text_ = record.getMessage() + repr(fields)
        for secret in (SOURCE, HIDDEN, "secret\n", "queue@example.com"):
            assert secret not in text_
    assert {first, second, third}  # opaque ids are the only reference


def test_log_helper_rejects_unapproved_fields() -> None:
    with pytest.raises(ValueError):
        queue._log(logging.INFO, "judge.claimed", source_code="x")


# ------------------------------------------------------------------ database error containment


def _exposed(exc: BaseException) -> str:
    """Everything a caller could print or log about an exception."""
    return "".join(traceback.format_exception(exc)) + repr(exc) + str(exc)


def test_database_errors_never_expose_row_detail(
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PostgreSQL's error detail for this CHECK violation quotes the whole submissions row,
    # source code included. The queue must neither log nor return it.
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    # The submission update itself breaks a CHECK (score 101): the detail quotes its row.
    failing = str(queue._FINISH).replace("score = :score", "score = :score + 1")
    assert failing != str(queue._FINISH)
    monkeypatch.setattr(queue, "_FINISH", text(failing))
    with caplog.at_level(logging.DEBUG), pytest.raises(QueueDatabaseError) as raised:
        queue.finish(factory, job, _accepted(job))
    exc = raised.value
    assert exc.operation == "finish"
    assert exc.__cause__ is None and exc.__context__ is None  # nothing chained
    assert SOURCE not in _exposed(exc) and HIDDEN not in _exposed(exc)
    logged = "".join(r.getMessage() + repr(r.__dict__.get("fields")) for r in caplog.records)
    assert SOURCE not in logged and HIDDEN not in logged
    [record] = [r for r in caplog.records if r.getMessage() == "judge.database_error"]
    assert record.__dict__["fields"] == {
        "category": "finish",
        "submission": str(submission),
        "attempt": "1",
    }
    assert _row(factory, submission).state == S.judging  # rolled back, nothing written


def test_connection_errors_are_contained_too(caplog: pytest.LogCaptureFixture) -> None:
    secret = "NOT-A-REAL-PASSWORD-3f9c"
    broken = create_engine(
        f"postgresql+psycopg://nobody:{secret}@127.0.0.1:1/none",
        connect_args={"connect_timeout": 2},
    )
    try:
        with caplog.at_level(logging.DEBUG), pytest.raises(QueueDatabaseError) as raised:
            queue.claim(sessionmaker(bind=broken), CONFIG)
    finally:
        broken.dispose()
    assert raised.value.operation == "claim"
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert secret not in _exposed(raised.value)
    assert all(secret not in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ Job representation


def _sentinel_job(source: str, test_input: str, expected: str) -> queue.Job:
    from app.modules.judge.snapshot import JudgingSnapshot, SnapshotGroup, SnapshotTest

    return queue.Job(
        claim=Claim(uuid.UUID(int=7), 2),
        language=Language.cpp,
        source_code=source,
        snapshot=JudgingSnapshot(
            tests_hash_v1=V1,
            scoring=Scoring.ioi,
            time_limit_ms=1000,
            memory_limit_mb=256,
            groups=(SnapshotGroup(1, 100),),
            tests=(SnapshotTest(1, 1, False),),
        ),
        inputs={(1, 1): test_input},
        expected_outputs={(1, 1): expected},
        group_ids={1: uuid.UUID(int=9)},
    )


def test_job_repr_and_str_never_show_source_or_test_data() -> None:
    source, test_input, expected = (
        "SENTINEL-SOURCE-7c1e",
        "SENTINEL-INPUT-52ab",
        "SENTINEL-EXPECTED-e94d",
    )
    job = _sentinel_job(source, test_input, expected)
    for shown in (repr(job), str(job), f"{job}", f"{job!r}"):
        assert source not in shown and test_input not in shown and expected not in shown
        # The safe metadata stays visible for debugging.
        assert "Claim(" in shown and str(uuid.UUID(int=7)) in shown and "cpp" in shown
        assert "group_ids" in shown and "snapshot=" in shown
    # The hidden fields are still there and still compared.
    assert job.source_code == source and job.inputs[(1, 1)] == test_input
    assert job == _sentinel_job(source, test_input, expected)
    assert job != _sentinel_job("other", test_input, expected)
    assert job != _sentinel_job(source, test_input, "other")


def test_loaded_job_repr_never_shows_source_or_test_data(factory: sessionmaker[Session]) -> None:
    _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    shown = repr(_job(factory, claimed))
    for secret in (SOURCE, HIDDEN, "secret\n", "1 2\n", "3\n"):
        assert secret not in shown


# ------------------------------------------------------------------ result validation (R2)


def _g(ordinal: int, points: int, verdict: Verdict, score: int) -> GroupResult:
    return GroupResult(ordinal, points, verdict, score, None, None)


def _done(verdict: Verdict, score: int, *groups: GroupResult) -> SubmissionResult:
    return SubmissionResult(S.done, verdict, score, tuple(groups))


A, WA = Verdict.accepted, Verdict.wrong_answer
# The seeded problem: samples (group 0, 0 points) and one scored group of 100 points.
INVALID_RESULTS = {
    "missing_group": _done(A, 100, _g(1, 100, A, 100)),
    "extra_group": _done(A, 100, _g(0, 0, A, 0), _g(1, 100, A, 100), _g(2, 0, A, 0)),
    "duplicate_group": _done(A, 100, _g(0, 0, A, 0), _g(1, 100, A, 100), _g(1, 100, A, 100)),
    "wrong_points": _done(A, 90, _g(0, 0, A, 0), _g(1, 90, A, 90)),
    "score_above_points": _done(A, 100, _g(0, 0, A, 0), _g(1, 100, A, 150)),
    "passed_group_without_its_points": _done(A, 0, _g(0, 0, A, 0), _g(1, 100, A, 0)),
    "failed_group_with_points": _done(WA, 100, _g(0, 0, A, 0), _g(1, 100, WA, 100)),
    "inconsistent_total": _done(A, 90, _g(0, 0, A, 0), _g(1, 100, A, 100)),
    "accepted_with_a_failed_group": _done(A, 100, _g(0, 0, WA, 0), _g(1, 100, A, 100)),
    "compile_error_with_groups": _done(Verdict.compile_error, 0, _g(0, 0, WA, 0)),
    "compile_error_with_a_score": _done(Verdict.compile_error, 10),
}


@pytest.mark.parametrize("case", sorted(INVALID_RESULTS))
def test_invalid_results_are_rejected_before_any_write(
    factory: sessionmaker[Session], case: str
) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    with pytest.raises(queue.InvalidResult):
        queue.finish(factory, job, INVALID_RESULTS[case])
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.judged_at) == (S.judging, None, None, None)
    assert row.lease_expires_at is not None and _results(factory, submission) == []


@pytest.mark.parametrize(
    ("result", "stored"),
    [
        (_done(A, 100, _g(0, 0, A, 0), _g(1, 100, A, 100)), (A, 100)),
        # D4: every scored group passes but a sample fails -> partial with 100 points.
        (_done(Verdict.partial, 100, _g(0, 0, WA, 0), _g(1, 100, A, 100)), (Verdict.partial, 100)),
        (_done(WA, 0, _g(0, 0, A, 0), _g(1, 100, WA, 0)), (WA, 0)),
        (_done(Verdict.compile_error, 0), (Verdict.compile_error, 0)),
    ],
)
def test_valid_ioi_results_still_finish(
    factory: sessionmaker[Session], result: SubmissionResult, stored: tuple[Verdict, int]
) -> None:
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    assert queue.finish(factory, _job(factory, claimed), result)
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, *stored)
    assert len(_results(factory, submission)) == len(result.groups)


def _binary_job(factory: sessionmaker[Session]) -> tuple[uuid.UUID, queue.Job]:
    with factory() as db, db.begin():
        _problem(db)
        db.execute(text("UPDATE problems SET scoring = 'binary'"))  # before binding
    submission = _submit(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    return submission, _job(factory, claimed)


def test_binary_results_follow_the_core_rules(factory: sessionmaker[Session]) -> None:
    submission, job = _binary_job(factory)
    assert job.snapshot.scoring == Scoring.binary
    # D3: any failure scores 0 overall, while a passed group keeps its points.
    with pytest.raises(queue.InvalidResult):  # 100 overall despite a failed group
        queue.finish(factory, job, _done(WA, 100, _g(0, 0, WA, 0), _g(1, 100, A, 100)))
    assert _results(factory, submission) == []
    assert queue.finish(factory, job, _done(WA, 0, _g(0, 0, WA, 0), _g(1, 100, A, 100)))
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, WA, 0)
    assert [r[3] for r in _results(factory, submission)] == [0, 100]


# ------------------------------------------------------------------ R3: one consistent view


def test_load_job_reads_one_consistent_snapshot_despite_a_concurrent_commit(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A test input is changed and committed by another connection after load_job has read
    # the problem's snapshot but before it reads the test data. REPEATABLE READ must keep
    # the job on the view it started with; READ COMMITTED would mix old and new data.
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    real_snapshot = service.judging_snapshot  # the module queue.py calls through
    committed: list[str] = []

    def snapshot_then_concurrent_commit(db: Session, problem_id: uuid.UUID) -> Any:
        result = real_snapshot(db, problem_id)
        with factory() as other, other.begin():  # a different pooled connection
            other.execute(
                text("UPDATE test_cases SET input = 'CHANGED-AFTER-SNAPSHOT' WHERE NOT is_sample")
            )
        committed.append("yes")
        return result

    monkeypatch.setattr(service, "judging_snapshot", snapshot_then_concurrent_commit)
    job = queue.load_job(factory, claimed)
    assert committed == ["yes"]
    assert isinstance(job, queue.Job)
    assert job.inputs[(1, 1)] == HIDDEN  # the view load_job started with
    with factory() as db:  # the change really was committed
        assert db.scalar(text("SELECT input FROM test_cases WHERE NOT is_sample")) == (
            "CHANGED-AFTER-SNAPSHOT"
        )
    assert _row(factory, submission).state == S.judging


def test_pooled_connection_is_reset_after_the_read_only_transaction(engine: Engine) -> None:
    # One pooled connection only, so the next session reuses exactly the connection that
    # load_job set to REPEATABLE READ, READ ONLY.
    single = create_engine(engine.url, pool_size=1, max_overflow=0, hide_parameters=True)
    one = sessionmaker(bind=single, expire_on_commit=False)
    try:
        with one() as db, db.begin():
            db.execute(
                text(
                    "TRUNCATE submission_group_results, submissions, test_cases, test_groups, "
                    "problem_translations, problems, users CASCADE"
                )
            )
        _seed(one)
        claimed = queue.claim(one, CONFIG)
        assert claimed is not None

        def backend_state() -> tuple[int, str, str]:
            with one() as db, db.begin():
                return (
                    int(db.scalar(text("SELECT pg_backend_pid()"))),
                    str(db.scalar(text("SHOW transaction_isolation"))),
                    str(db.scalar(text("SHOW transaction_read_only"))),
                )

        pid_before, isolation_before, read_only_before = backend_state()
        job = queue.load_job(one, claimed)
        assert isinstance(job, queue.Job)
        pid_after, isolation_after, read_only_after = backend_state()
        assert pid_after == pid_before  # the very same pooled connection
        assert (isolation_before, read_only_before) == ("read committed", "off")
        assert (isolation_after, read_only_after) == ("read committed", "off")  # reset
        assert queue.finish(one, job, _accepted(job))  # and it can write again
    finally:
        single.dispose()


# ------------------------------------------------------------------ R4: finish and sweep


def _short_lease(factory: sessionmaker[Session], submission_id: uuid.UUID, seconds: float) -> None:
    with factory() as db, db.begin():
        db.execute(
            text(
                "UPDATE submissions SET lease_expires_at = clock_timestamp() + "
                "make_interval(secs => :s) WHERE id = :id"
            ),
            {"s": seconds, "id": submission_id},
        )


def _write_finish(db: Session, job: queue.Job, result: SubmissionResult) -> bool:
    """finish()'s own two statements, in a transaction the test controls."""
    row = db.execute(
        queue._FINISH,
        {
            "id": job.claim.submission_id,
            "token": job.claim.token,
            "verdict": result.verdict.value if result.verdict else None,
            "score": result.score,
            "compile_message": None,
        },
    ).one_or_none()
    if row is None:
        return False
    for group in result.groups:
        db.execute(
            queue._INSERT_RESULT,
            {
                "id": job.claim.submission_id,
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


def test_finish_after_an_uncommitted_sweep_fails_fast_and_writes_nothing(
    factory: sessionmaker[Session], impatient: sessionmaker[Session]
) -> None:
    # Sweep first: it holds the row lock, uncommitted. A finish for that attempt cannot wait
    # for it: finish needs an unexpired lease and the sweep only takes expired ones, so the
    # row already fails finish's condition and PostgreSQL skips it without a lock wait (the
    # 1 s lock_timeout would fail a wait). The sweep's outcome then stands.
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    _expire(factory, submission)
    with factory() as sweeper:
        sweeper.begin()
        swept = sweeper.execute(queue._SWEEP, {"max": 3, "limit": 50}).all()
        assert [r[0] for r in swept] == [submission]
        started = time.monotonic()
        assert not queue.finish(impatient, job, _accepted(job))
        assert time.monotonic() - started < 1.0  # no lock wait
        sweeper.commit()
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score, row.lease_expires_at) == (S.queued, None, None, None)
    assert _results(factory, submission) == []


def test_sweep_skips_a_finish_in_progress_and_the_finish_commits(
    factory: sessionmaker[Session], impatient: sessionmaker[Session]
) -> None:
    # Finish first: it passed its fence and holds the row lock, uncommitted; then the lease
    # runs out. The sweep sees an expired, locked row and skips it (SKIP LOCKED, no wait).
    # The finish commits its verdict and results; the next sweep finds nothing.
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    _short_lease(factory, submission, 1.0)
    with factory() as finisher:
        finisher.begin()
        assert _write_finish(finisher, job, _accepted(job))
        time.sleep(1.5)  # the committed row now shows an expired lease
        with factory() as db:
            expired = db.scalar(
                text(
                    "SELECT lease_expires_at <= clock_timestamp() FROM submissions WHERE id = :id"
                ),
                {"id": submission},
            )
        assert expired is True
        started = time.monotonic()
        assert queue.sweep(impatient, CONFIG) == 0  # skipped, not waited for
        assert time.monotonic() - started < 1.0
        finisher.commit()
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, Verdict.accepted, 100)
    assert len(_results(factory, submission)) == 2
    assert queue.sweep(factory, CONFIG) == 0


def test_a_second_finish_waits_for_the_first_and_writes_nothing(
    factory: sessionmaker[Session],
) -> None:
    # Duplicate delivery of one attempt's result: the second finish genuinely waits on the
    # first one's row lock (verified with pg_blocking_pids), then PostgreSQL re-checks its
    # condition on the committed row (state done) and it writes nothing.
    [submission] = _seed(factory)
    claimed = queue.claim(factory, CONFIG)
    assert claimed is not None
    job = _job(factory, claimed)
    outcome: list[object] = []
    second_pid: list[int] = []
    started = threading.Event()

    def second() -> None:
        def write(db: Session) -> bool:
            second_pid.append(int(db.scalar(text("SELECT pg_backend_pid()"))))
            started.set()
            return _write_finish(db, job, _accepted(job))

        try:
            outcome.append(queue._run(factory, "finish", write, job.claim))
        except Exception as exc:  # reported to the test
            outcome.append(exc)

    with factory() as first:
        first.begin()
        first_pid = int(first.scalar(text("SELECT pg_backend_pid()")))
        assert _write_finish(first, job, _accepted(job))
        thread = threading.Thread(target=second)
        thread.start()
        assert started.wait(15)
        deadline = time.monotonic() + 15
        with factory() as probe:
            while True:
                blockers = probe.execute(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid[0]}
                ).scalar_one()
                probe.rollback()
                if first_pid in blockers:
                    break
                assert thread.is_alive(), outcome
                assert time.monotonic() < deadline, "the second finish never waited"
                time.sleep(0.01)
        first.commit()
    thread.join(15)
    assert not thread.is_alive()
    assert outcome == [False]
    row = _row(factory, submission)
    assert (row.state, row.verdict, row.score) == (S.done, Verdict.accepted, 100)
    assert len(_results(factory, submission)) == 2  # no duplicate group results
