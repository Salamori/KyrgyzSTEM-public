"""Hint review: admin-only, audited per hint, author-excluded, and never a student reveal."""

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.modules.audit import service as audit_service
from app.modules.audit.models import AuditLog
from app.modules.olympiad import content
from app.modules.olympiad.importer import import_problem
from app.modules.olympiad.models import (
    EditorialUnlock,
    Hint,
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


def _one(slug: str, locale: str = "en", ordinal: int | str = 1) -> str:
    return f"/api/v1/admin/problems/{slug}/hints/{locale}/{ordinal}/review"


def _all(slug: str, locale: str = "en") -> str:
    return f"/api/v1/admin/problems/{slug}/hints/{locale}/review"


def _read(slug: str) -> str:
    return f"/api/v1/admin/problems/{slug}/hints"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hints(db: Session, slug: str) -> dict[tuple[str, int], Hint]:
    db.expire_all()
    problem = db.scalars(select(Problem).where(Problem.slug == slug)).one()
    rows = db.scalars(select(Hint).where(Hint.problem_id == problem.id))
    return {(h.locale.value, h.ordinal): h for h in rows}


def _audits(db: Session) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == "problem.hint_reviewed")
    return list(db.scalars(stmt.order_by(AuditLog.created_at)))


def _no_reviews(db: Session, slug: str) -> bool:
    return all(h.reviewed_by is None for h in _hints(db, slug).values()) and _audits(db) == []


# ------------------------------------------------------------------ authorization


def test_hint_review_endpoints_require_admin(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    client: TestClient,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)
    assert client.get(_read("h")).status_code == 401
    assert client.post(_one("h"), json=ATTEST).status_code == 401
    assert client.post(_all("h"), json=ATTEST).status_code == 401
    for role in (Role.student, Role.teacher):
        c, _ = signed_in(role)
        assert c.get(_read("h")).status_code == 403
        assert c.post(_one("h"), json=ATTEST).status_code == 403
        assert c.post(_all("h"), json=ATTEST).status_code == 403


def test_hint_review_requires_csrf(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    del admin.headers["X-CSRF-Token"]
    for url in (_one("h"), _all("h")):
        res = admin.post(url, json=ATTEST)
        assert res.status_code == 403 and res.json()["error"]["code"] == "csrf_failed"
    assert _no_reviews(db, "h")


def test_author_cannot_review_own_hints(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    admin, me = signed_in(Role.admin)
    make_problem("own", status=ProblemStatus.in_review, created_by=me)
    for url in (_one("own"), _all("own")):
        res = admin.post(url, json=ATTEST)  # attesting cannot override a known identity
        assert res.status_code == 403 and res.json()["error"]["code"] == "reviewer_is_author"
    assert _no_reviews(db, "own")


def test_unlinked_author_requires_attestation(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)  # created_by NULL: author not linked
    admin, _ = signed_in(Role.admin)
    for url in (_one("h"), _all("h")):
        for body in ({}, {"attest_not_author": False}):
            res = admin.post(url, json=body)
            assert res.status_code == 422
            assert res.json()["error"]["code"] == "author_attestation_required"
    assert _no_reviews(db, "h")


def test_unknown_problem_missing_and_invalid_targets(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)  # hints 1..2 in en and ru
    admin, _ = signed_in(Role.admin)
    for res in (
        admin.get(_read("missing")),
        admin.post(_one("missing"), json=ATTEST),
        admin.post(_all("missing"), json=ATTEST),
        admin.post(_one("h", "ky"), json=ATTEST),
        admin.post(_all("h", "ky"), json=ATTEST),
        admin.post(_one("h", "en", 3), json=ATTEST),
    ):
        assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    for res in (
        admin.post(_one("h", "xx"), json=ATTEST),
        admin.post(_all("h", "xx"), json=ATTEST),
        admin.post(_one("h", "en", 0), json=ATTEST),
        admin.post(_one("h", "en", -1), json=ATTEST),
        admin.post(_one("h", "en", "first"), json=ATTEST),
        admin.post(_one("h"), json={**ATTEST, "extra": 1}),
    ):
        assert res.status_code == 422 and res.json()["error"]["code"] == "validation_error"
    assert _no_reviews(db, "h")


# ------------------------------------------------------------------ reviewing


def test_single_review_sets_fields_and_writes_audit_evidence(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    problem = make_problem("h", status=ProblemStatus.in_review)
    admin, admin_user = signed_in(Role.admin)

    res = admin.post(_one("h", "en", 1), json=ATTEST)
    assert res.status_code == 200 and res.json()["status"] == "in_review"
    assert res.json()["review"]["hint_review"] == [
        {"locale": "ru", "total": 2, "reviewed": 0},
        {"locale": "en", "total": 2, "reviewed": 1},
    ]

    hints = _hints(db, "h")
    assert hints[("en", 1)].reviewed_by == admin_user.id and hints[("en", 1)].reviewed_at
    assert all(h.reviewed_by is None for key, h in hints.items() if key != ("en", 1))

    [entry] = _audits(db)
    assert entry.actor_id == admin_user.id and entry.target_id == str(problem.id)
    assert entry.details == {
        "locale": "en",
        "ordinal": 1,
        "body_sha256": _sha("hint 1 en"),
        "author_identity": "unlinked",
        "attested_not_author": True,
    }

    listing = admin.get(_read("h")).json()
    assert [(h["locale"], h["ordinal"]) for h in listing] == [
        ("ru", 1), ("ru", 2), ("en", 1), ("en", 2),
    ]  # fmt: skip
    by_key = {(h["locale"], h["ordinal"]): h for h in listing}
    assert by_key[("en", 1)]["reviewed"] is True
    assert by_key[("en", 1)]["body_sha256"] == _sha("hint 1 en")
    assert by_key[("en", 2)]["reviewed"] is False and by_key[("en", 2)]["reviewed_at"] is None


def test_linked_author_needs_no_attestation(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    _, author = signed_in(Role.admin)
    reviewer, _ = signed_in(Role.admin)
    make_problem("linked", status=ProblemStatus.in_review, created_by=author)
    assert reviewer.post(_one("linked"), json={}).status_code == 200
    [entry] = _audits(db)
    assert entry.details is not None
    assert entry.details["author_identity"] == "linked"
    assert entry.details["attested_not_author"] is False


def test_bulk_review_audits_each_hint_of_one_locale(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)
    admin, admin_user = signed_in(Role.admin)
    res = admin.post(_all("h", "ru"), json=ATTEST)
    assert res.status_code == 200
    assert {"locale": "ru", "total": 2, "reviewed": 2} in res.json()["review"]["hint_review"]

    hints = _hints(db, "h")
    assert hints[("ru", 1)].reviewed_by == hints[("ru", 2)].reviewed_by == admin_user.id
    assert hints[("en", 1)].reviewed_by is None and hints[("en", 2)].reviewed_by is None
    details = [e.details for e in _audits(db)]
    assert details == [
        {
            "locale": "ru",
            "ordinal": n,
            "body_sha256": _sha(f"hint {n} ru"),
            "author_identity": "unlinked",
            "attested_not_author": True,
        }
        for n in (1, 2)
    ]


def test_bulk_review_is_all_or_nothing(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    original = audit_service.record
    calls = 0

    def failing_on_second(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("audit store unavailable")
        original(*args, **kwargs)

    monkeypatch.setattr(audit_service, "record", failing_on_second)
    res = admin.post(_all("h", "en"), json=ATTEST)
    assert res.status_code == 500 and calls == 2
    db.rollback()  # what the request's session does when it is closed without a commit
    assert _no_reviews(db, "h")


def test_repeated_reviews_restamp_and_are_audited_again(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    assert admin.post(_one("h"), json=ATTEST).status_code == 200
    hint = _hints(db, "h")[("en", 1)]
    hint.reviewed_at = utcnow() - timedelta(hours=1)
    db.commit()
    earlier = hint.reviewed_at

    assert admin.post(_one("h"), json=ATTEST).status_code == 200
    later = _hints(db, "h")[("en", 1)].reviewed_at
    assert earlier is not None and later is not None and later > earlier
    assert admin.post(_all("h", "en"), json=ATTEST).status_code == 200
    assert len(_audits(db)) == 4  # 1 + 1 + 2 (the bulk review audits each hint)


def test_published_problem_hints_can_be_reviewed_without_changing_status(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    problem = make_problem("pub")  # published by default
    published_at = problem.published_at
    admin, _ = signed_in(Role.admin)
    assert admin.post(_one("pub", "ru", 2), json=ATTEST).json()["status"] == "published"
    assert admin.post(_all("pub", "en"), json=ATTEST).json()["status"] == "published"
    db.refresh(problem)
    assert problem.status == ProblemStatus.published and problem.published_at == published_at


def test_reading_and_reviewing_never_reveal_hints_or_change_progress(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("h", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    assert admin.get(_read("h")).status_code == 200
    assert admin.post(_one("h", "ru", 1), json=ATTEST).status_code == 200
    assert admin.post(_all("h", "en"), json=ATTEST).status_code == 200

    for model in (HintReveal, EditorialUnlock, Submission, SelfReport):
        assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
    detail = admin.get("/api/v1/problems/h").json()
    assert detail["revealed_hint_count"] == 0 and detail["my_status"] is None
    assert detail["editorial_unlocked"] is False
    assert admin.get("/api/v1/problems/h/hints").json() == {"total": 2, "revealed": []}


# ------------------------------------------------------------------ importer


Snapshot = dict[tuple[str, int], tuple[str, Any, datetime | None]]


def _state(db: Session) -> Snapshot:
    return {
        k: (h.body_md, h.reviewed_by, h.reviewed_at) for k, h in _hints(db, "sample-sum").items()
    }


def _mark_all(db: Session, reviewer: User, stamp: datetime) -> None:
    for hint in _hints(db, "sample-sum").values():
        hint.reviewed_by, hint.reviewed_at = reviewer.id, stamp
    db.commit()


def _reviewed(state: Snapshot, locale: str) -> set[bool]:
    return {by is not None for (loc, _), (_, by, _) in state.items() if loc == locale}


def test_reimport_keeps_hint_reviews_only_for_unchanged_locale_sequences(
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    en, ru = list(problem.hints["en"]), list(problem.hints["ru"])
    assert len(en) >= 2 and len(ru) >= 2
    import_problem(db, problem)
    db.commit()
    reviewer = User(
        email="hints@example.com", password_hash="x", display_name="Reviewer", role=Role.admin
    )
    db.add(reviewer)
    db.flush()
    stamp = utcnow() - timedelta(days=1)
    _mark_all(db, reviewer, stamp)

    # Unchanged sequences: every review, with its timestamp, stands.
    before = _state(db)
    import_problem(db, problem)
    db.commit()
    assert _state(db) == before

    variants = {
        "edited": [en[0] + " (edited)", *en[1:]],
        "added": [*en, "An extra hint."],
        "removed": en[:-1],
        "reordered": [en[1], en[0], *en[2:]],
    }
    for name, new_en in variants.items():
        _mark_all(db, reviewer, stamp)
        import_problem(db, replace(problem, hints={"en": new_en, "ru": ru}))
        db.commit()
        state = _state(db)
        assert _reviewed(state, "en") == {False}, name  # the changed locale is cleared...
        assert _reviewed(state, "ru") == {True}, name  # ...the other locale is untouched
        assert [state[("en", n)][0] for n in range(1, len(new_en) + 1)] == new_en, name
        import_problem(db, problem)  # back to the original content for the next variant
        db.commit()

    # A new locale starts unreviewed; a removed locale disappears; the others are kept.
    _mark_all(db, reviewer, stamp)
    import_problem(db, replace(problem, hints={"en": en, "ky": ["Жаңы кеңеш."]}))
    db.commit()
    state = _state(db)
    assert {loc for loc, _ in state} == {"en", "ky"}
    assert _reviewed(state, "en") == {True} and _reviewed(state, "ky") == {False}
    assert all(at == stamp for (loc, _), (_, _, at) in state.items() if loc == "en")
    assert Locale.ky in {h.locale for h in _hints(db, "sample-sum").values()}
