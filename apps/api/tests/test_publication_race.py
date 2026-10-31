"""Publication, review and import never interleave on one problem: each takes the problem row
lock (FOR NO KEY UPDATE) first, so the later one waits and then acts on the other's committed
state.

Every waiting case is synchronized on PostgreSQL's own lock state (`pg_blocking_pids`), not on
timing: the test proceeds only once the second transaction is verifiably blocked by the first.
Each test uses its own scratch database: a race needs real, independently committing
connections, which the rolled-back per-test transaction of the other tests cannot provide.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import replace

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError
from app.modules.audit.models import AuditLog
from app.modules.olympiad import content, service
from app.modules.olympiad.importer import ContentImportError, import_curriculum, import_problem
from app.modules.olympiad.models import Editorial, Problem, ProblemStatus, SelfReport
from app.modules.olympiad.schemas import (
    DifficultyUpdate,
    EditorialReview,
    HintReview,
    RightsVerification,
)
from app.modules.users.models import Locale, Role, User
from tests.test_migrations import _migrate, scratch_engine  # noqa: F401

SLUG = "sample-sum"
IP = "127.0.0.1"
LIMIT = 15.0  # seconds before a synchronization step counts as failed
CHANGED = "Changed editorial."

Ready = tuple[Engine, content.ProblemContent, User]


@pytest.fixture
def ready(scratch_engine: Engine) -> Ready:  # noqa: F811
    """sample-sum imported and fully reviewed (committed), plus its reviewer."""
    _migrate(scratch_engine, "upgrade", "head")
    settings = get_settings()
    problem = content.load_problem(settings.content_dir / "problems" / SLUG)
    with Session(scratch_engine, expire_on_commit=False) as db:
        import_curriculum(db, content.load_curriculum(settings.content_dir / "curriculum.toml"))
        reviewer = User(
            email="race-reviewer@example.com",
            password_hash="x",
            display_name="[Synthetic] Reviewer",
            role=Role.admin,
            is_synthetic=True,
        )
        db.add(reviewer)
        import_problem(db, problem)
        db.commit()
        attest = {"attest_not_author": True}
        update = DifficultyUpdate(band=problem.band, rationale="Race test: confirmed band.")
        service.review_difficulty(db, SLUG, update, reviewer, IP)
        service.verify_rights(db, SLUG, RightsVerification(**attest), reviewer, IP)
        for loc in problem.statements:
            service.review_translation(db, SLUG, Locale(loc), reviewer, IP)
        for loc in problem.editorials:
            service.review_editorial(db, SLUG, Locale(loc), EditorialReview(**attest), reviewer, IP)
        for loc in problem.hints:
            service.review_locale_hints(db, SLUG, Locale(loc), HintReview(**attest), reviewer, IP)
        db.commit()
        row = db.scalars(select(Problem).where(Problem.slug == SLUG)).one()
        assert service.publish_blockers(db, row, reviewer) == []
    return scratch_engine, problem, reviewer


def _pid(db: Session) -> int:
    return int(db.execute(text("SELECT pg_backend_pid()")).scalar_one())


class _Waiter:
    """Runs `action(db)` in its own session and thread, then commits (or rolls back and
    keeps the exception)."""

    def __init__(self, engine: Engine, action: Callable[[Session], object]) -> None:
        self.engine = engine
        self.pid: int | None = None
        self.outcome: list[object] = []
        self._started = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(action,))
        self.thread.start()

    def _run(self, action: Callable[[Session], object]) -> None:
        with Session(self.engine) as db:
            self.pid = _pid(db)
            self._started.set()
            try:
                self.outcome.append(action(db))
                db.commit()
            except Exception as exc:  # reported to the test
                db.rollback()
                self.outcome.append(exc)

    def wait_until_blocked_by(self, holder_pid: int) -> None:
        """Return once PostgreSQL reports this transaction waiting for `holder_pid`'s lock;
        fail if it finishes (or never blocks) instead."""
        assert self._started.wait(LIMIT)
        deadline = time.monotonic() + LIMIT
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as probe:
            while True:
                blockers = probe.execute(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": self.pid}
                ).scalar_one()
                if holder_pid in blockers:
                    return
                if not self.thread.is_alive():
                    pytest.fail(f"finished without waiting for the lock: {self.outcome}")
                if time.monotonic() > deadline:
                    pytest.fail("never waited for the lock")
                time.sleep(0.01)

    def result(self) -> object:
        self.thread.join(LIMIT)
        assert not self.thread.is_alive()
        [result] = self.outcome
        return result


def _changed(problem: content.ProblemContent) -> content.ProblemContent:
    return replace(problem, editorials={**problem.editorials, "ru": CHANGED})


def _row(engine: Engine) -> tuple[ProblemStatus, object, int]:
    with Session(engine) as db:
        row = db.scalars(select(Problem).where(Problem.slug == SLUG)).one()
        published = db.scalar(select(func.count()).where(AuditLog.action == "problem.published"))
        return row.status, row.published_at, published or 0


def test_import_waits_for_a_publish_and_then_refuses(ready: Ready) -> None:
    engine, problem, reviewer = ready
    with Session(engine) as publisher:
        service.publish(publisher, SLUG, reviewer, IP)  # holds the row lock, not committed
        waiter = _Waiter(engine, lambda db: import_problem(db, _changed(problem)))
        waiter.wait_until_blocked_by(_pid(publisher))
        publisher.commit()
    result = waiter.result()
    assert isinstance(result, ContentImportError) and "published" in str(result)
    status, _, published = _row(engine)
    assert status == ProblemStatus.published and published == 1
    with Session(engine) as db:
        row = db.scalars(select(Problem).where(Problem.slug == SLUG)).one()
        ru = db.get(Editorial, (row.id, Locale.ru))
        assert ru is not None and ru.body_md != CHANGED


def test_publish_waits_for_an_import_and_judges_the_new_content(ready: Ready) -> None:
    engine, problem, reviewer = ready
    with Session(engine) as importer:
        import_problem(importer, _changed(problem))  # holds the row lock, not committed
        waiter = _Waiter(engine, lambda db: service.publish(db, SLUG, reviewer, IP).status)
        waiter.wait_until_blocked_by(_pid(importer))
        importer.commit()
    result = waiter.result()
    assert isinstance(result, ApiError) and result.code == "publish_blocked"
    assert result.details == ["editorial_unreviewed"]
    status, published_at, published = _row(engine)
    assert (status, published_at, published) == (ProblemStatus.in_review, None, 0)


def test_concurrent_publishes_publish_once(ready: Ready) -> None:
    engine, _, reviewer = ready
    with Session(engine) as first:
        service.publish(first, SLUG, reviewer, IP)
        waiter = _Waiter(engine, lambda db: service.publish(db, SLUG, reviewer, IP))
        waiter.wait_until_blocked_by(_pid(first))
        first.commit()
    result = waiter.result()
    assert isinstance(result, ApiError) and result.details == ["already_published"]
    status, _, published = _row(engine)
    assert status == ProblemStatus.published and published == 1


def test_review_waiting_for_an_import_reviews_the_imported_text(ready: Ready) -> None:
    engine, problem, reviewer = ready
    review = EditorialReview(attest_not_author=True)
    with Session(engine) as importer:
        import_problem(importer, _changed(problem))
        waiter = _Waiter(
            engine,
            lambda db: service.review_editorial(db, SLUG, Locale.ru, review, reviewer, IP).slug,
        )
        waiter.wait_until_blocked_by(_pid(importer))
        importer.commit()
    assert waiter.result() == SLUG
    with Session(engine) as db:
        # The review was taken on the committed new text, so its evidence matches it.
        event = db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "problem.editorial_reviewed")
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        ).one()
        assert event.details is not None
        assert event.details["body_sha256"] == service.body_sha256(CHANGED)
        row = db.scalars(select(Problem).where(Problem.slug == SLUG)).one()
        assert service.publish_blockers(db, row, reviewer) == []


def test_lock_does_not_block_student_writes_that_reference_the_problem(ready: Ready) -> None:
    # FOR NO KEY UPDATE, unlike FOR UPDATE, does not conflict with the KEY SHARE lock a
    # foreign-key insert takes on the problem row.
    engine, _, reviewer = ready
    with Session(engine) as db:
        service.publish(db, SLUG, reviewer, IP)
        db.commit()
    with Session(engine) as holder:
        service.unpublish(holder, SLUG, reviewer, IP)  # holds the row lock, not committed
        with Session(engine) as student:
            student.execute(text("SET lock_timeout = '1s'"))  # would fail fast if blocked
            row_id = student.scalars(select(Problem.id).where(Problem.slug == SLUG)).one()
            student.add(SelfReport(user_id=reviewer.id, problem_id=row_id))
            student.commit()
        holder.rollback()
    status, _, _ = _row(engine)
    assert status == ProblemStatus.published


def test_admin_write_gives_up_after_the_lock_timeout_without_changes(
    ready: Ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, problem, reviewer = ready
    monkeypatch.setattr(service, "LOCK_TIMEOUT_MS", 200)
    with Session(engine) as importer:
        import_problem(importer, _changed(problem))  # holds the row lock
        with Session(engine) as publisher, pytest.raises(OperationalError) as timed_out:
            service.publish(publisher, SLUG, reviewer, IP)
        assert "lock timeout" in str(timed_out.value.orig)
        importer.rollback()
    status, published_at, published = _row(engine)
    assert (status, published_at, published) == (ProblemStatus.in_review, None, 0)
