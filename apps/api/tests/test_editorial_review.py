"""Editorial review: admin-only, audited, author-excluded, and never a student unlock."""

import hashlib
from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.modules.audit.models import AuditLog
from app.modules.olympiad import content
from app.modules.olympiad.importer import import_problem
from app.modules.olympiad.models import (
    Editorial,
    EditorialUnlock,
    HintReveal,
    Problem,
    ProblemStatus,
    SelfReport,
    Submission,
)
from app.modules.users.models import Locale, Role, User
from tests.test_olympiad import MakeProblem, SignedIn, curriculum, make_problem  # noqa: F401

ATTEST = {"attest_not_author": True}
SAMPLE_SUM = get_settings().content_dir / "problems" / "sample-sum"


def _review_url(slug: str, locale: str = "en") -> str:
    return f"/api/v1/admin/problems/{slug}/editorials/{locale}/review"


def _read_url(slug: str) -> str:
    return f"/api/v1/admin/problems/{slug}/editorials"


def _editorial(db: Session, slug: str, locale: str) -> Editorial:
    problem = db.scalars(select(Problem).where(Problem.slug == slug)).one()
    row = db.get(Editorial, (problem.id, Locale(locale)))
    assert row is not None
    db.refresh(row)
    return row


def _audits(db: Session) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == "problem.editorial_reviewed")
    return list(db.scalars(stmt.order_by(AuditLog.created_at)))


# ------------------------------------------------------------------ authorization


def test_editorial_review_endpoints_require_admin(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    client: TestClient,
) -> None:
    make_problem("e", status=ProblemStatus.in_review)
    assert client.get(_read_url("e")).status_code == 401
    assert client.post(_review_url("e"), json=ATTEST).status_code == 401
    for role in (Role.student, Role.teacher):
        c, _ = signed_in(role)
        assert c.get(_read_url("e")).status_code == 403
        assert c.post(_review_url("e"), json=ATTEST).status_code == 403


def test_editorial_review_requires_csrf(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("e", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    del admin.headers["X-CSRF-Token"]
    res = admin.post(_review_url("e"), json=ATTEST)
    assert res.status_code == 403 and res.json()["error"]["code"] == "csrf_failed"
    assert _editorial(db, "e", "en").reviewed_by is None and _audits(db) == []


def test_author_cannot_review_own_editorial(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    admin, me = signed_in(Role.admin)
    make_problem("own", status=ProblemStatus.in_review, created_by=me)
    res = admin.post(_review_url("own"), json=ATTEST)  # attesting cannot override identity
    assert res.status_code == 403 and res.json()["error"]["code"] == "reviewer_is_author"
    assert _editorial(db, "own", "en").reviewed_by is None and _audits(db) == []


def test_unlinked_author_requires_attestation(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("e", status=ProblemStatus.in_review)  # created_by NULL: author not linked
    admin, _ = signed_in(Role.admin)
    for body in ({}, {"attest_not_author": False}):
        res = admin.post(_review_url("e"), json=body)
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "author_attestation_required"
    assert _editorial(db, "e", "en").reviewed_by is None and _audits(db) == []


def test_unknown_problem_missing_and_unsupported_locale(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    make_problem("e", status=ProblemStatus.in_review)  # editorials in en and ru only
    admin, _ = signed_in(Role.admin)
    assert admin.get(_read_url("missing")).status_code == 404
    assert admin.post(_review_url("missing"), json=ATTEST).status_code == 404
    missing_locale = admin.post(_review_url("e", "ky"), json=ATTEST)
    assert missing_locale.status_code == 404
    assert missing_locale.json()["error"]["code"] == "not_found"
    unsupported = admin.post(_review_url("e", "xx"), json=ATTEST)
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "validation_error"
    assert admin.post(_review_url("e"), json={**ATTEST, "extra": 1}).status_code == 422


# ------------------------------------------------------------------ reviewing


def test_review_sets_fields_and_writes_audit_evidence(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    problem = make_problem("e", status=ProblemStatus.in_review)
    admin, admin_user = signed_in(Role.admin)

    before = admin.get(f"/api/v1/problems/{problem.slug}").json()["review"]
    assert before["editorial_locales"] == ["ru", "en"]
    assert before["reviewed_editorial_locales"] == []

    res = admin.post(_review_url("e"), json=ATTEST)
    assert res.status_code == 200
    assert res.json()["review"]["reviewed_editorial_locales"] == ["en"]
    assert res.json()["status"] == "in_review"  # publication status untouched

    row = _editorial(db, "e", "en")
    assert row.reviewed_by == admin_user.id and row.reviewed_at is not None
    assert _editorial(db, "e", "ru").reviewed_by is None

    [entry] = _audits(db)
    assert entry.actor_id == admin_user.id and entry.target_id == str(problem.id)
    assert entry.details == {
        "locale": "en",
        "body_sha256": hashlib.sha256(b"editorial en").hexdigest(),
        "author_identity": "unlinked",
        "attested_not_author": True,
    }

    listing = {e["locale"]: e for e in admin.get(_read_url("e")).json()}
    assert listing["en"]["reviewed"] is True and listing["en"]["body_md"] == "editorial en"
    assert listing["en"]["body_sha256"] == entry.details["body_sha256"]
    assert listing["ru"]["reviewed"] is False and listing["ru"]["reviewed_at"] is None


def test_linked_author_needs_no_attestation(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    _, author = signed_in(Role.admin)
    reviewer, _ = signed_in(Role.admin)
    make_problem("linked", status=ProblemStatus.in_review, created_by=author)
    assert reviewer.post(_review_url("linked"), json={}).status_code == 200
    [entry] = _audits(db)
    assert entry.details is not None
    assert entry.details["author_identity"] == "linked"
    assert entry.details["attested_not_author"] is False


def test_repeated_review_restamps_and_is_audited_again(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("e", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    assert admin.post(_review_url("e"), json=ATTEST).status_code == 200
    row = _editorial(db, "e", "en")
    row.reviewed_at = utcnow() - timedelta(hours=1)
    db.commit()
    earlier = row.reviewed_at

    assert admin.post(_review_url("e"), json=ATTEST).status_code == 200
    later = _editorial(db, "e", "en").reviewed_at
    assert earlier is not None and later is not None and later > earlier
    assert len(_audits(db)) == 2


def test_published_problem_can_be_reviewed_without_changing_status(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    problem = make_problem("pub")  # published by default
    published_at = problem.published_at
    admin, _ = signed_in(Role.admin)
    res = admin.post(_review_url("pub", "ru"), json=ATTEST)
    assert res.status_code == 200 and res.json()["status"] == "published"
    db.refresh(problem)
    assert problem.status == ProblemStatus.published and problem.published_at == published_at


def test_reading_and_reviewing_never_unlock_or_change_progress(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("e", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    assert admin.get(_read_url("e")).status_code == 200
    assert admin.post(_review_url("e"), json=ATTEST).status_code == 200
    assert admin.post(_review_url("e", "ru"), json=ATTEST).status_code == 200

    for model in (EditorialUnlock, HintReveal, Submission, SelfReport):
        assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
    detail = admin.get("/api/v1/problems/e").json()
    assert detail["my_status"] is None and detail["editorial_unlocked"] is False
    assert detail["revealed_hint_count"] == 0
    locked = admin.get("/api/v1/problems/e/editorial")
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "editorial_locked"


def test_students_see_the_reviewed_flag_after_their_own_unlock(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    make_problem("pub")
    admin, _ = signed_in(Role.admin)
    student, _ = signed_in(Role.student)
    assert admin.post(_review_url("pub", "en"), json=ATTEST).status_code == 200
    opened = student.post(
        "/api/v1/problems/pub/editorial/unlock", json={"reason": "gave_up"}, params={"locale": "en"}
    )
    assert opened.status_code == 200 and opened.json()["reviewed"] is True


# ------------------------------------------------------------------ importer


def test_reimport_keeps_review_only_for_unchanged_editorial_text(
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    assert set(problem.editorials) >= {"en", "ru"}
    import_problem(db, problem)
    db.commit()
    reviewer = User(
        email="editorials@example.com", password_hash="x", display_name="Reviewer", role=Role.admin
    )
    db.add(reviewer)
    db.flush()
    stamp = utcnow() - timedelta(days=1)
    for locale in ("en", "ru"):
        row = _editorial(db, "sample-sum", locale)
        row.reviewed_by, row.reviewed_at = reviewer.id, stamp
    db.commit()

    import_problem(db, problem)  # unchanged text: reviews stand
    db.commit()
    for locale in ("en", "ru"):
        row = _editorial(db, "sample-sum", locale)
        assert (row.reviewed_by, row.reviewed_at) == (reviewer.id, stamp)

    changed = replace(
        problem,
        editorials={**problem.editorials, "ru": "Changed editorial.", "ky": "New editorial."},
    )
    import_problem(db, changed)
    db.commit()
    assert _editorial(db, "sample-sum", "en").reviewed_by == reviewer.id
    for locale in ("ru", "ky"):
        row = _editorial(db, "sample-sum", locale)
        assert row.reviewed_by is None and row.reviewed_at is None

    import_problem(db, replace(problem, editorials={"en": problem.editorials["en"]}))
    db.commit()
    bus = db.scalars(select(Problem).where(Problem.slug == "sample-sum")).one()
    remaining = set(db.scalars(select(Editorial.locale).where(Editorial.problem_id == bus.id)))
    assert remaining == {Locale.en}
    assert _editorial(db, "sample-sum", "en").reviewed_by == reviewer.id
