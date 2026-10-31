"""Step 5.3.2: a new submission is bound to the v2 judging snapshot of its problem.

Only rows are written and read here: no submitted code is judged or executed.
"""

import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.judge import snapshot as snap
from app.modules.olympiad import content, service
from app.modules.olympiad.importer import import_problem
from app.modules.olympiad.models import (
    Language,
    Problem,
    Scoring,
    Submission,
    SubmissionState,
    TestCase,
    TestGroup,
)
from app.modules.olympiad.schemas import SubmissionCreate
from app.modules.users.models import Role, User
from tests.test_olympiad import (  # noqa: F401
    FIXTURE_TESTS_HASH,
    MakeProblem,
    SignedIn,
    curriculum,
    make_problem,
)

SAMPLE_SUM = get_settings().content_dir / "problems" / "sample-sum"
BODY = {"language": "python", "source_code": "print(1)"}


def _expected(problem: Problem, **changes: Any) -> str:
    """The fixture problem's snapshot, written out by hand: samples (group 0, 0 points, one
    sample test) and one scored group of 100 points with one hidden test."""
    values: dict[str, Any] = {
        "tests_hash_v1": FIXTURE_TESTS_HASH,
        "scoring": Scoring.ioi,
        "time_limit_ms": 1000,
        "memory_limit_mb": 256,
        "groups": (snap.SnapshotGroup(0, 0), snap.SnapshotGroup(1, 100)),
        "tests": (snap.SnapshotTest(0, 1, True), snap.SnapshotTest(1, 1, False)),
        **changes,
    }
    return snap.snapshot_hash(snap.JudgingSnapshot(**values))


def _submit(client: TestClient, slug: str) -> Submission:
    res = client.post("/api/v1/submissions", json={"problem_slug": slug, **BODY})
    assert res.status_code == 201, res.text
    return Submission(id=uuid.UUID(res.json()["id"]))


def _stored(db: Session, submission_id: uuid.UUID) -> str | None:
    db.expire_all()
    row = db.get(Submission, submission_id)
    assert row is not None
    return row.tests_hash


# ------------------------------------------------------------------ binding at creation


def test_not_judged_submission_is_bound_to_the_snapshot(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    problem = make_problem("bind")
    student, _ = signed_in(Role.student)
    created = _submit(student, "bind")
    row = db.get(Submission, created.id)
    assert row is not None and row.state == SubmissionState.not_judged
    assert row.tests_hash == _expected(problem)
    # The response shape is unchanged: the hash is not exposed.
    detail = student.get(f"/api/v1/submissions/{created.id}").json()
    assert "tests_hash" not in detail


def test_queued_submission_is_bound_to_the_snapshot(
    make_problem: MakeProblem,  # noqa: F811
    make_user: Any,
    db: Session,
) -> None:
    problem = make_problem("bindq")
    user: User = make_user()
    data = SubmissionCreate(problem_slug="bindq", language=Language.cpp, source_code="int main(){}")
    submission = service.create_submission(db, user, data, judge_available=True)
    db.commit()
    assert submission.state == SubmissionState.queued
    assert submission.tests_hash == _expected(problem)


def test_binding_matches_an_independently_built_snapshot_of_real_content(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    import_problem(db, content.load_problem(SAMPLE_SUM))
    db.commit()
    row = db.scalars(select(Problem).where(Problem.slug == "sample-sum")).one()
    groups = {g.id: g for g in db.scalars(select(TestGroup).where(TestGroup.problem_id == row.id))}
    cases = db.scalars(select(TestCase).where(TestCase.problem_id == row.id))
    independent = snap.JudgingSnapshot(
        tests_hash_v1=str(row.tests_hash),
        scoring=row.scoring,
        time_limit_ms=row.time_limit_ms,
        memory_limit_mb=row.memory_limit_mb,
        groups=tuple(snap.SnapshotGroup(g.ordinal, g.points) for g in groups.values()),
        tests=tuple(
            snap.SnapshotTest(groups[c.group_id].ordinal, c.ordinal, c.is_sample) for c in cases
        ),
    )
    admin, _ = signed_in(Role.admin)  # in-review problems are open to admins
    created = _submit(admin, "sample-sum")
    stored = _stored(db, created.id)
    assert stored is not None and snap.matches(stored, independent)
    assert stored != row.tests_hash  # v2, never the v1 value itself


def test_snapshot_is_read_in_one_statement(
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    # Read the id first: the fixture's commit expired the object, and reloading it would
    # issue statements of its own while the counter is listening.
    problem_id = make_problem("onestmt").id
    statements: list[str] = []

    def count(*args: Any) -> None:
        statements.append(str(args[2]))

    bind = db.connection()
    event.listen(bind, "before_cursor_execute", count)
    try:
        snapshot = service.judging_snapshot(db, problem_id)
    finally:
        event.remove(bind, "before_cursor_execute", count)
    assert snapshot is not None and len(statements) == 1


# ------------------------------------------------------------------ determinism and changes


def test_same_snapshot_binds_the_same_hash(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("same")
    student, _ = signed_in(Role.student)
    first, second = _submit(student, "same"), _submit(student, "same")
    assert _stored(db, first.id) == _stored(db, second.id) is not None


@pytest.fixture
def changed(db: Session, make_problem: MakeProblem) -> Iterator[Problem]:  # noqa: F811
    yield make_problem("chg")


@pytest.mark.parametrize(
    ("change", "expected_changes"),
    [
        ("time_limit", {"time_limit_ms": 2000}),
        ("memory_limit", {"memory_limit_mb": 512}),
        ("scoring", {"scoring": Scoring.binary}),
        ("v1", {"tests_hash_v1": "f" * 64}),
        ("points", {"groups": (snap.SnapshotGroup(0, 0), snap.SnapshotGroup(1, 90))}),
    ],
)
def test_metadata_changes_bind_a_new_hash_and_keep_the_old_one(
    signed_in: SignedIn,
    db: Session,
    changed: Problem,
    change: str,
    expected_changes: dict[str, Any],
) -> None:
    student, _ = signed_in(Role.student)
    before = _submit(student, "chg")
    old = _stored(db, before.id)
    problem = db.get(Problem, changed.id)
    assert problem is not None
    if change == "time_limit":
        problem.time_limit_ms = 2000
    elif change == "memory_limit":
        problem.memory_limit_mb = 512
    elif change == "scoring":
        problem.scoring = Scoring.binary
    elif change == "v1":
        problem.tests_hash = "f" * 64
    else:
        group = db.scalars(
            select(TestGroup).where(TestGroup.problem_id == problem.id, TestGroup.ordinal == 1)
        ).one()
        group.points = 90
    db.commit()
    after = _submit(student, "chg")
    assert _stored(db, after.id) == _expected(changed, **expected_changes) != old
    assert _stored(db, before.id) == old  # the earlier binding is never replaced
    assert db.get(Problem, changed.id).tests_hash == (  # type: ignore[union-attr]
        "f" * 64 if change == "v1" else FIXTURE_TESTS_HASH
    )  # v1 is only read


# ------------------------------------------------------------------ NULL and malformed


def test_problem_without_a_v1_hash_binds_null(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    problem = make_problem("nohash")
    problem.tests_hash = None
    db.commit()
    student, _ = signed_in(Role.student)
    with caplog.at_level(logging.WARNING, logger="kyrgyzstem.judging"):
        created = _submit(student, "nohash")
    assert _stored(db, created.id) is None  # never invented
    assert not caplog.records  # an expected state, not an error


@pytest.mark.parametrize("defect", ["v1_not_hex", "sample_flag", "empty_group"])
def test_malformed_snapshot_binds_null_and_warns(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    caplog: pytest.LogCaptureFixture,
    defect: str,
) -> None:
    problem = make_problem("broken")
    if defect == "v1_not_hex":
        problem.tests_hash = "h" * 64
    elif defect == "sample_flag":
        sample = db.scalars(
            select(TestCase).where(TestCase.problem_id == problem.id, TestCase.is_sample)
        ).one()
        sample.is_sample = False
    else:
        db.add(TestGroup(problem_id=problem.id, ordinal=2, points=0, description_md="Empty."))
    db.commit()
    student, _ = signed_in(Role.student)
    with caplog.at_level(logging.WARNING, logger="kyrgyzstem.judging"):
        created = _submit(student, "broken")
    assert _stored(db, created.id) is None  # never a hash for an invalid snapshot
    [record] = caplog.records
    assert record.getMessage() == "judge.snapshot_unbindable"
    assert record.__dict__["fields"] == {"problem": "broken"}  # no user data
    with pytest.raises(snap.SnapshotError):
        service.judging_snapshot(db, problem.id)


# ------------------------------------------------------------------ history


def test_historical_submissions_are_never_backfilled(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    make_user: Any,
    db: Session,
) -> None:
    problem = make_problem("hist")
    old_user: User = make_user()
    historical = Submission(
        user_id=old_user.id,
        problem_id=problem.id,
        language=Language.python,
        source_code="print()",
        state=SubmissionState.not_judged,
    )
    db.add(historical)
    db.commit()
    assert historical.tests_hash is None
    student, _ = signed_in(Role.student)
    created = _submit(student, "hist")
    assert _stored(db, created.id) == _expected(problem)
    assert _stored(db, historical.id) is None  # left exactly as it was


# ------------------------------------------------------------------ queueing (Milestone D)


def _post(client: TestClient, slug: str, language: str = "python") -> dict[str, Any]:
    res = client.post(
        "/api/v1/submissions",
        json={"problem_slug": slug, "language": language, "source_code": "print(1)"},
    )
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data


def test_without_judge_languages_nothing_is_queued(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("q0")
    student, _ = signed_in(Role.student)
    assert get_settings().judge_languages == ""  # the default: no worker, no queueing
    data = _post(student, "q0")
    assert (data["state"], data["verdict"], data["score"]) == ("not_judged", None, None)
    assert student.get("/api/v1/problems/q0").json()["judge_available"] is False


def test_a_judged_language_is_queued_and_bound(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    problem = make_problem("q1")
    get_settings().judge_languages = "python"
    student, _ = signed_in(Role.student)
    data = _post(student, "q1")
    assert (data["state"], data["verdict"], data["score"], data["group_results"]) == (
        "queued",
        None,
        None,
        [],
    )
    row = db.get(Submission, uuid.UUID(data["id"]))
    assert row is not None and row.state == SubmissionState.queued
    assert row.tests_hash == _expected(problem) and row.judge_attempts == 0
    assert student.get("/api/v1/problems/q1").json()["judge_available"] is True


def test_a_language_the_judge_does_not_take_is_not_judged(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    make_problem("q2")
    get_settings().judge_languages = "python"
    student, _ = signed_in(Role.student)
    assert _post(student, "q2", language="cpp")["state"] == "not_judged"


def test_an_unbindable_problem_is_not_judged_instead_of_queued(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    problem = make_problem("q3")
    problem.tests_hash = None
    db.commit()
    get_settings().judge_languages = "python,cpp"
    student, _ = signed_in(Role.student)
    data = _post(student, "q3")
    assert data["state"] == "not_judged"  # never queued only to end in judge_error


def test_a_failed_commit_never_reports_queued(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_problem("q4")
    get_settings().judge_languages = "python"
    student, _ = signed_in(Role.student)

    def broken_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", broken_commit)
    res = student.post(
        "/api/v1/submissions",
        json={"problem_slug": "q4", "language": "python", "source_code": "print(1)"},
    )
    # The app's error handler answers 500 with the generic envelope: no submission shown.
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "internal_error"
    assert "queued" not in res.text and "id" not in res.json()


def test_a_finished_judgement_is_shown_without_test_data(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    from app.modules.olympiad.models import SubmissionGroupResult, Verdict

    problem = make_problem("q5")
    get_settings().judge_languages = "python"
    student, _ = signed_in(Role.student)
    created = _post(student, "q5")
    row = db.get(Submission, uuid.UUID(created["id"]))
    assert row is not None
    # What the worker's fenced finish stores (queue.finish); written directly here.
    row.state, row.verdict, row.score = SubmissionState.done, Verdict.wrong_answer, 0
    row.compile_message = None
    group_ids = {
        g.ordinal: g.id
        for g in db.scalars(select(TestGroup).where(TestGroup.problem_id == problem.id))
    }
    for ordinal, points, verdict in ((0, 0, Verdict.accepted), (1, 100, Verdict.wrong_answer)):
        db.add(
            SubmissionGroupResult(
                submission_id=row.id,
                group_id=group_ids.get(ordinal),
                group_ordinal=ordinal,
                points=points,
                verdict=verdict,
                score=0,
            )
        )
    db.commit()
    detail = student.get(f"/api/v1/submissions/{created['id']}").json()
    assert (detail["state"], detail["verdict"], detail["score"]) == ("done", "wrong_answer", 0)
    assert [(g["ordinal"], g["verdict"]) for g in detail["group_results"]] == [
        (0, "accepted"),
        (1, "wrong_answer"),
    ]
    tests = list(db.scalars(select(TestCase).where(TestCase.problem_id == problem.id)))
    shown = student.get(f"/api/v1/submissions/{created['id']}").text
    for case in tests:  # neither hidden inputs nor expected outputs
        if not case.is_sample:
            assert case.input not in shown and case.expected_output not in shown
    assert "tests_hash" not in shown and "lease" not in shown and "attempts" not in shown


def test_a_judge_error_is_shown_without_details(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("q6")
    get_settings().judge_languages = "python"
    student, _ = signed_in(Role.student)
    created = _post(student, "q6")
    row = db.get(Submission, uuid.UUID(created["id"]))
    assert row is not None
    row.state = SubmissionState.judge_error
    db.commit()
    detail = student.get(f"/api/v1/submissions/{created['id']}").json()
    assert (detail["state"], detail["verdict"], detail["score"], detail["group_results"]) == (
        "judge_error",
        None,
        None,
        [],
    )


def test_another_students_queued_submission_is_not_found(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    make_problem("q7")
    get_settings().judge_languages = "python"
    owner, _ = signed_in(Role.student)
    other, _ = signed_in(Role.student)
    created = _post(owner, "q7")
    assert other.get(f"/api/v1/submissions/{created['id']}").status_code == 404
