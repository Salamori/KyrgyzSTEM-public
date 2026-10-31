"""Submission results outlive a re-import, and the judging metadata obeys its constraints
(migration 0009). Rows are inserted directly: no code is judged or executed here."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.modules.judge import snapshot as snap
from app.modules.olympiad import content
from app.modules.olympiad.importer import import_problem
from app.modules.olympiad.models import (
    Language,
    Problem,
    Scoring,
    Submission,
    SubmissionGroupResult,
    SubmissionState,
    TestGroup,
    Verdict,
)
from app.modules.users.models import Role, User
from tests.test_olympiad import (  # noqa: F401
    FIXTURE_TESTS_HASH,
    MakeProblem,
    SignedIn,
    curriculum,
    make_problem,
)

SAMPLE_SUM = get_settings().content_dir / "problems" / "sample-sum"


def _groups(db: Session, problem_id: uuid.UUID) -> dict[int, TestGroup]:
    rows = db.scalars(select(TestGroup).where(TestGroup.problem_id == problem_id))
    return {g.ordinal: g for g in rows}


def _judged_submission(db: Session, user: User, problem_id: uuid.UUID) -> Submission:
    """A partial result recorded the way a judge will: one row per group, with a copy of
    the group's ordinal and points."""
    groups = _groups(db, problem_id)
    passed = {o for o in groups if o != max(groups)}  # every group but the last passes
    results = [
        SubmissionGroupResult(
            group_id=g.id,
            group_ordinal=o,
            points=g.points,
            verdict=Verdict.accepted if o in passed else Verdict.wrong_answer,
            score=g.points if o in passed else 0,
        )
        for o, g in groups.items()
    ]
    submission = Submission(
        user_id=user.id,
        problem_id=problem_id,
        language=Language.python,
        source_code="print()",
        state=SubmissionState.done,
        verdict=Verdict.partial,
        score=sum(r.score for r in results),
        tests_hash="a" * 64,
        judge_attempts=1,
        judged_at=utcnow(),
        group_results=results,
    )
    db.add(submission)
    db.commit()
    return submission


def test_results_survive_a_reimport_that_replaces_the_test_groups(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem)
    db.commit()
    row = db.scalars(select(Problem).where(Problem.slug == "sample-sum")).one()
    student, me = signed_in(Role.admin)  # admins may open and submit to in-review problems
    old_groups = _groups(db, row.id)
    old_ids = {g.id for g in old_groups.values()}
    submission = _judged_submission(db, me, row.id)
    expected = sorted(
        (o, g.points, r.verdict.value, r.score)
        for o, g in old_groups.items()
        for r in submission.group_results
        if r.group_ordinal == o
    )
    detail = student.get(f"/api/v1/submissions/{submission.id}").json()
    shown = [(r["ordinal"], r["points"], r["verdict"], r["score"]) for r in detail["group_results"]]
    assert shown == expected

    import_problem(db, problem)  # deletes and recreates every test group of the problem
    db.commit()
    db.expire_all()
    new_groups = _groups(db, row.id)
    assert {g.id for g in new_groups.values()}.isdisjoint(old_ids)
    kept = db.scalars(
        select(SubmissionGroupResult).where(SubmissionGroupResult.submission_id == submission.id)
    ).all()
    assert len(kept) == len(old_ids)
    assert all(r.group_id is None for r in kept)  # the link is gone, the history is not
    detail = student.get(f"/api/v1/submissions/{submission.id}").json()
    shown = [(r["ordinal"], r["points"], r["verdict"], r["score"]) for r in detail["group_results"]]
    assert shown == expected  # same response shape and content as before the re-import
    assert (detail["score"], detail["verdict"]) == (submission.score, "partial")


def test_new_submission_stores_its_v2_snapshot_hash_and_no_judging_state(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("plain")
    student, _ = signed_in(Role.student)
    body = {"problem_slug": "plain", "language": "python", "source_code": "print(1)"}
    created = student.post("/api/v1/submissions", json=body)
    assert created.status_code == 201 and created.json()["state"] == "not_judged"
    row = db.get(Submission, uuid.UUID(created.json()["id"]))
    assert row is not None
    # The fixture problem's v2 snapshot, written out independently of the service.
    expected = snap.snapshot_hash(
        snap.JudgingSnapshot(
            tests_hash_v1=FIXTURE_TESTS_HASH,
            scoring=Scoring.ioi,
            time_limit_ms=1000,
            memory_limit_mb=256,
            groups=(snap.SnapshotGroup(0, 0), snap.SnapshotGroup(1, 100)),
            tests=(snap.SnapshotTest(0, 1, True), snap.SnapshotTest(1, 1, False)),
        )
    )
    assert (row.tests_hash, row.judge_attempts, row.lease_expires_at, row.compile_message) == (
        expected,
        0,
        None,
        None,
    )


def _submission(user: User, problem_id: uuid.UUID, **fields: Any) -> Submission:
    values: dict[str, Any] = {
        "user_id": user.id,
        "problem_id": problem_id,
        "language": Language.cpp,
        "source_code": "int main(){}",
        "state": SubmissionState.done,
        "verdict": Verdict.accepted,
        "score": 100,
        **fields,
    }
    return Submission(**values)


VALID = [
    {"tests_hash": "0123456789abcdef" * 4},
    {"judge_attempts": 3},
    {
        "state": SubmissionState.judging,
        "verdict": None,
        "score": None,
        "lease_expires_at": utcnow(),
    },
    {"verdict": Verdict.compile_error, "score": 0, "compile_message": "e" * 4096},
]
INVALID = [
    {"tests_hash": "A" * 64},  # not lowercase hex
    {"tests_hash": "a" * 63},
    {"judge_attempts": -1},
    {"lease_expires_at": utcnow()},  # a lease outside `judging`
    {"state": SubmissionState.queued, "verdict": None, "score": None, "lease_expires_at": utcnow()},
    {"compile_message": "error: x"},  # a message without a compile error
    {"verdict": Verdict.compile_error, "score": 0, "compile_message": "e" * 4097},
]


@pytest.mark.parametrize(
    ("fields", "ok"), [(f, True) for f in VALID] + [(f, False) for f in INVALID]
)
def test_submission_judging_metadata_constraints(
    make_problem: MakeProblem,  # noqa: F811
    make_user: Any,
    db: Session,
    fields: dict[str, Any],
    ok: bool,
) -> None:
    problem = make_problem("meta")
    user: User = make_user()
    with db.begin_nested():
        db.add(_submission(user, problem.id, **fields))
        if ok:
            db.flush()
        else:
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


@pytest.mark.parametrize(
    ("ordinal", "points", "score", "ok"),
    [
        (1, 100, 100, True),
        (0, 0, 0, True),
        (1, 30, 31, False),  # more than the group is worth
        (1, 30, -1, False),
        (-1, 30, 0, False),
        (1, 101, 0, False),
    ],
)
def test_group_result_constraints(
    make_problem: MakeProblem,  # noqa: F811
    make_user: Any,
    db: Session,
    ordinal: int,
    points: int,
    score: int,
    ok: bool,
) -> None:
    problem = make_problem("res")
    user: User = make_user()
    submission = _submission(user, problem.id)
    db.add(submission)
    db.flush()
    result = SubmissionGroupResult(
        submission_id=submission.id,
        group_ordinal=ordinal,
        points=points,
        verdict=Verdict.accepted,
        score=score,
    )
    with db.begin_nested():
        db.add(result)
        if ok:
            db.flush()
        else:
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
