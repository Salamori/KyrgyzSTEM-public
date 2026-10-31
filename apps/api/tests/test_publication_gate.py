"""The audited publication gate: every existing statement, editorial and hint must carry a
valid independent review (backed by its audit event), provenance must satisfy the content
policy, and a refused publish changes nothing."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import ApiError
from app.modules.audit import service as audit_service
from app.modules.audit.models import AuditLog
from app.modules.olympiad import content, service
from app.modules.olympiad.importer import import_problem
from app.modules.olympiad.models import (
    Band,
    DifficultyStatus,
    Editorial,
    EditorialUnlock,
    Hint,
    HintReveal,
    Origin,
    Problem,
    ProblemStatus,
    Scoring,
    SelfReport,
    Submission,
    Tag,
)
from app.modules.users.models import Locale, Role, User
from tests.test_olympiad import MakeProblem, SignedIn, curriculum, make_problem  # noqa: F401

ATTEST = {"attest_not_author": True}
SAMPLE_SUM = get_settings().content_dir / "problems" / "sample-sum"


def _url(slug: str, action: str) -> str:
    return f"/api/v1/admin/problems/{slug}/{action}"


def _review_everything(
    admin: TestClient,
    slug: str,
    locales: tuple[str, ...] = ("en", "ru"),
    *,
    editorials: bool = True,
    hints: bool = True,
    attest: bool = True,
    skip: str | None = None,
    band: str = "easy",
) -> None:
    """Every review step through the API, as a reviewer would do it in the panel, except the
    step named by `skip` (an action path such as "editorials/ru/review")."""
    body = ATTEST if attest else {}

    def review(action: str, **kwargs: Any) -> None:
        if action != skip:
            assert admin.post(_url(slug, action), **kwargs).status_code == 200, action

    if skip != "difficulty":
        ok = admin.patch(
            _url(slug, "difficulty"), json={"band": band, "rationale": "One idea, small input."}
        )
        assert ok.status_code == 200, ok.text
    review("rights/verify", json=body)
    for loc in locales:
        review(f"translations/{loc}/review")
        if editorials:
            review(f"editorials/{loc}/review", json=body)
        if hints:
            review(f"hints/{loc}/review", json=body)


def _problem(db: Session, slug: str) -> Problem:
    db.expire_all()
    return db.scalars(select(Problem).where(Problem.slug == slug)).one()


def _snapshot(db: Session, slug: str) -> tuple[Any, ...]:
    """Everything a publish attempt could touch: status, reviews, progress and audit."""
    p = _problem(db, slug)
    editorials = db.scalars(select(Editorial).where(Editorial.problem_id == p.id))
    hints = db.scalars(select(Hint).where(Hint.problem_id == p.id))
    counts = tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (AuditLog, HintReveal, EditorialUnlock, Submission, SelfReport)
    )
    return (
        p.status,
        p.published_at,
        p.published_by,
        p.rights_verified_at,
        p.rights_verified_by,
        p.difficulty_status,
        sorted((t.locale.value, t.reviewed_by, t.reviewed_at) for t in p.translations),
        sorted((e.locale.value, e.reviewed_by, e.reviewed_at) for e in editorials),
        sorted((h.locale.value, h.ordinal, h.reviewed_by, h.reviewed_at) for h in hints),
        counts,
    )


def _refused(admin: TestClient, db: Session, slug: str) -> list[str]:
    """Attempt to publish; assert it is refused and changed nothing; return its codes."""
    before = _snapshot(db, slug)
    res = admin.post(_url(slug, "publish"))
    assert res.status_code == 409, res.text
    error = res.json()["error"]
    assert error["code"] == "publish_blocked"
    assert _snapshot(db, slug) == before
    codes: list[str] = error["details"]
    return codes


def _items(admin: TestClient, slug: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = admin.get(f"/api/v1/problems/{slug}").json()["review"][
        "blocking_items"
    ]
    return items


def _published_events(db: Session) -> int:
    stmt = select(func.count()).where(AuditLog.action == "problem.published")
    return db.scalar(stmt) or 0


# ------------------------------------------------------------------ success


def test_fully_reviewed_multilingual_problem_publishes_once(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("full", status=ProblemStatus.in_review)
    admin, admin_user = signed_in(Role.admin)
    _review_everything(admin, "full")
    review = admin.get("/api/v1/problems/full").json()["review"]
    assert review["publish_blockers"] == [] and review["blocking_items"] == []
    assert sorted(review["reviewed_locales"]) == ["en", "ru"]
    assert sorted(review["reviewed_editorial_locales"]) == ["en", "ru"]
    assert {(h["locale"], h["total"], h["reviewed"]) for h in review["hint_review"]} == {
        ("en", 2, 2),
        ("ru", 2, 2),
    }

    res = admin.post(_url("full", "publish"))
    assert res.status_code == 200 and res.json()["status"] == "published"
    problem = _problem(db, "full")
    assert problem.published_at is not None and problem.published_by == admin_user.id
    assert _published_events(db) == 1
    assert admin.post(_url("full", "publish")).json()["error"]["details"] == ["already_published"]
    assert _published_events(db) == 1


@pytest.mark.parametrize(("editorials", "hints"), [(False, 0), (True, 0), (False, 3)])
def test_single_language_problem_without_optional_parts_publishes(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    editorials: bool,
    hints: int,
) -> None:
    # No hints and no editorial are allowed: only what exists must be reviewed.
    make_problem(
        "one", status=ProblemStatus.in_review, locales=("ky",), hints=hints, editorial=editorials
    )
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "one", ("ky",), editorials=editorials, hints=hints > 0)
    assert admin.post(_url("one", "publish")).status_code == 200


def test_independent_reviewer_may_also_publish_but_the_linked_author_may_not(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    author_client, author = signed_in(Role.admin)
    reviewer, _ = signed_in(Role.admin)
    make_problem("linked", status=ProblemStatus.in_review, created_by=author)
    _review_everything(reviewer, "linked", attest=False)  # linked author: no attestation needed
    assert _refused(author_client, db, "linked") == ["approver_is_author"]
    # One independent reviewer is enough: no second person is required to publish.
    assert reviewer.post(_url("linked", "publish")).status_code == 200


def test_unpublish_stays_allowed_when_the_gate_would_fail(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("old")  # published before the gate, never reviewed
    admin, _ = signed_in(Role.admin)
    # Reading it (and its review state) never unpublishes it silently.
    assert (
        "statement_unreviewed"
        in admin.get("/api/v1/problems/old").json()["review"]["publish_blockers"]
    )
    assert _problem(db, "old").status == ProblemStatus.published
    res = admin.post(_url("old", "unpublish"))
    assert res.status_code == 200 and res.json()["status"] == "in_review"


# ------------------------------------------------------------------ rejection


def test_all_blockers_are_reported_at_once_with_their_items(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("raw", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    assert _refused(admin, db, "raw") == [
        "rights_not_verified",
        "statement_unreviewed",
        "editorial_unreviewed",
        "hint_unreviewed",
        "difficulty_not_reviewed",
    ]
    items = [(i["code"], i["item"], i["locale"], i["ordinal"]) for i in _items(admin, "raw")]
    assert items == [
        ("rights_not_verified", "rights", None, None),
        ("statement_unreviewed", "statement", "ru", None),
        ("statement_unreviewed", "statement", "en", None),
        ("editorial_unreviewed", "editorial", "ru", None),
        ("editorial_unreviewed", "editorial", "en", None),
        ("hint_unreviewed", "hint", "ru", 1),
        ("hint_unreviewed", "hint", "ru", 2),
        ("hint_unreviewed", "hint", "en", 1),
        ("hint_unreviewed", "hint", "en", 2),
        ("difficulty_not_reviewed", "difficulty", None, None),
    ]


@pytest.mark.parametrize(
    ("skip", "code", "item"),
    [
        ("translations/ru/review", "statement_unreviewed", ("statement", "ru", None)),
        ("editorials/ru/review", "editorial_unreviewed", ("editorial", "ru", None)),
        ("hints/ru/review", "hint_unreviewed", ("hint", "ru", 1)),
    ],
)
def test_one_unreviewed_language_blocks(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    skip: str,
    code: str,
    item: tuple[str, str, int | None],
) -> None:
    make_problem("p", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "p", skip=skip)  # the reviewer misses exactly this step
    assert _refused(admin, db, "p") == [code]
    assert (item[0], item[1], item[2]) in {
        (i["item"], i["locale"], i["ordinal"]) for i in _items(admin, "p")
    }


def test_partially_reviewed_hints_block_until_every_hint_is_reviewed(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("h", status=ProblemStatus.in_review, locales=("en",), hints=3)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "h", ("en",), hints=False)
    for ordinal in (1, 3):
        assert admin.post(_url("h", f"hints/en/{ordinal}/review"), json=ATTEST).status_code == 200
    assert _refused(admin, db, "h") == ["hint_unreviewed"]
    assert [(i["locale"], i["ordinal"]) for i in _items(admin, "h")] == [("en", 2)]
    review = admin.get("/api/v1/problems/h").json()["review"]
    assert review["hint_review"] == [{"locale": "en", "total": 3, "reviewed": 2}]
    assert admin.post(_url("h", "hints/en/2/review"), json=ATTEST).status_code == 200
    assert admin.post(_url("h", "publish")).status_code == 200


def test_review_record_without_audit_evidence_does_not_count(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("forged", status=ProblemStatus.in_review, locales=("en",), hints=1)
    admin, admin_user = signed_in(Role.admin)
    _review_everything(admin, "forged", ("en",), editorials=False, hints=False)
    # Review fields written straight to the database, bypassing the audited endpoints.
    problem = _problem(db, "forged")
    for row in [
        *db.scalars(select(Editorial).where(Editorial.problem_id == problem.id)),
        *db.scalars(select(Hint).where(Hint.problem_id == problem.id)),
    ]:
        row.reviewed_by, row.reviewed_at = admin_user.id, problem.rights_verified_at
    db.commit()
    assert _refused(admin, db, "forged") == ["editorial_unreviewed", "hint_unreviewed"]
    review = admin.get("/api/v1/problems/forged").json()["review"]
    assert review["reviewed_editorial_locales"] == []
    assert review["hint_review"] == [{"locale": "en", "total": 1, "reviewed": 0}]


def test_review_by_someone_else_than_the_audited_reviewer_does_not_count(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("swap", status=ProblemStatus.in_review, locales=("en",), hints=0)
    admin, _ = signed_in(Role.admin)
    _, other = signed_in(Role.admin)
    _review_everything(admin, "swap", ("en",), hints=False)
    translation = _problem(db, "swap").translations[0]
    translation.reviewed_by = other.id  # not who the audit log says reviewed it
    db.commit()
    assert _refused(admin, db, "swap") == ["statement_unreviewed"]


def test_text_changed_after_review_invalidates_it(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("drift", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "drift")
    problem = _problem(db, "drift")
    editorial = db.scalars(
        select(Editorial).where(Editorial.problem_id == problem.id, Editorial.locale == "ru")
    ).one()
    hint = db.scalars(
        select(Hint).where(Hint.problem_id == problem.id, Hint.locale == "en", Hint.ordinal == 2)
    ).one()
    editorial.body_md += " (edited)"  # reviewed fields left in place: only the hash differs
    hint.body_md += " (edited)"
    db.commit()
    assert _refused(admin, db, "drift") == ["editorial_unreviewed", "hint_unreviewed"]
    assert {(i["item"], i["locale"], i["ordinal"]) for i in _items(admin, "drift")} == {
        ("editorial", "ru", None),
        ("hint", "en", 2),
    }
    # Reviewing the new text again restores eligibility.
    assert admin.post(_url("drift", "editorials/ru/review"), json=ATTEST).status_code == 200
    assert admin.post(_url("drift", "hints/en/2/review"), json=ATTEST).status_code == 200
    assert admin.post(_url("drift", "publish")).status_code == 200


def test_reviews_by_someone_later_linked_as_author_do_not_count(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("late", status=ProblemStatus.in_review, locales=("en",), hints=1)
    reviewer, reviewer_user = signed_in(Role.admin)
    publisher, _ = signed_in(Role.admin)
    _review_everything(reviewer, "late", ("en",))
    problem = _problem(db, "late")
    problem.created_by = reviewer_user.id  # authorship established after the review
    db.commit()
    assert _refused(publisher, db, "late") == ["author_review_conflict"]
    assert [i["item"] for i in _items(publisher, "late")] == [
        "rights",
        "statement",
        "editorial",
        "hint",
        "difficulty",
    ]


SOURCE = {
    "title": "Synthetic source task",
    "author": "Example Author",
    "url": "https://example.com/task",
    "license": "CC-BY-SA-4.0",
}


@pytest.mark.parametrize(
    "change",
    [
        {"license": "All-Rights-Reserved"},  # not an allowed license
        # Share-alike material cannot be adapted under a more permissive license.
        {"origin": Origin.adapted, "source": SOURCE, "license": "CC-BY-4.0"},
        # A licensed problem must keep its source's license.
        {"origin": Origin.licensed, "source": SOURCE, "license": "CC0-1.0"},
        # A reused problem's source must name all of title, author, url and license.
        {"origin": Origin.adapted, "source": {**SOURCE, "url": ""}, "license": "CC-BY-SA-4.0"},
    ],
)
def test_incomplete_provenance_blocks(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    change: dict[str, Any],
) -> None:
    make_problem("src", status=ProblemStatus.in_review, locales=("en",), hints=0)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "src", ("en",), hints=False)
    problem = _problem(db, "src")
    for field, value in change.items():
        setattr(problem, field, value)
    db.commit()
    # The rights confirmation covered the old provenance, so it no longer counts either.
    assert _refused(admin, db, "src") == ["provenance_incomplete", "rights_not_verified"]


def test_missing_attribution_still_blocks(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("anon", status=ProblemStatus.in_review, locales=("en",), hints=0)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "anon", ("en",), hints=False)
    _problem(db, "anon").authors_md = " "
    db.commit()
    assert _refused(admin, db, "anon") == ["missing_license_or_attribution", "rights_not_verified"]


# ------------------------------------------------------------------ authorization


def test_publish_requires_admin_and_csrf(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    client: TestClient,
    db: Session,
) -> None:
    make_problem("auth", status=ProblemStatus.in_review)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "auth")
    before = _snapshot(db, "auth")
    assert client.post(_url("auth", "publish")).status_code == 401
    for role in (Role.student, Role.teacher):
        c, _ = signed_in(role)
        assert c.post(_url("auth", "publish")).status_code == 403
    del admin.headers["X-CSRF-Token"]
    res = admin.post(_url("auth", "publish"))
    assert res.status_code == 403 and res.json()["error"]["code"] == "csrf_failed"
    assert _snapshot(db, "auth") == before


def test_blocking_items_are_admin_only(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    make_problem("pub")
    student, _ = signed_in(Role.student)
    assert student.get("/api/v1/problems/pub").json()["review"] is None


def test_direct_service_call_is_gated(
    make_problem: MakeProblem,  # noqa: F811
    make_user: Any,
    db: Session,
) -> None:
    make_problem("svc", status=ProblemStatus.in_review)
    actor: User = make_user(role=Role.admin)
    with pytest.raises(ApiError) as refused:
        service.publish(db, "svc", actor, "127.0.0.1")
    assert refused.value.code == "publish_blocked"
    db.rollback()
    assert _problem(db, "svc").status == ProblemStatus.in_review


# ------------------------------------------------------------------ importer


def test_reimport_invalidates_only_changed_reviews(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem)
    db.commit()
    admin, _ = signed_in(Role.admin)
    locales = tuple(sorted(problem.statements))
    # Confirm the authored band: a re-import resets a difficulty that differs from the file.
    _review_everything(admin, "sample-sum", locales, hints=bool(problem.hints), band=problem.band)
    assert admin.get("/api/v1/problems/sample-sum").json()["review"]["blocking_items"] == []

    import_problem(db, problem)  # unchanged: every review stands
    db.commit()
    assert _items(admin, "sample-sum") == []

    changed = replace(
        problem,
        editorials={**problem.editorials, "ru": "Changed editorial."},
        hints={**problem.hints, "en": [*problem.hints["en"], "An extra hint."]},
    )
    import_problem(db, changed)
    db.commit()
    blocked = {(i["code"], i["locale"]) for i in _items(admin, "sample-sum")}
    assert blocked == {("editorial_unreviewed", "ru"), ("hint_unreviewed", "en")}
    assert _refused(admin, db, "sample-sum") == ["editorial_unreviewed", "hint_unreviewed"]


# ------------------------------------------------------------------ evidence binding


def test_statement_text_changed_after_review_invalidates_it(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("stmt", status=ProblemStatus.in_review, hints=0, editorial=False)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "stmt", editorials=False, hints=False)
    ru = next(t for t in _problem(db, "stmt").translations if t.locale == Locale.ru)
    ru.notes_md = "A note added after the review."  # review fields left in place
    db.commit()
    assert _refused(admin, db, "stmt") == ["statement_unreviewed"]
    assert [(i["item"], i["locale"]) for i in _items(admin, "stmt")] == [("statement", "ru")]
    assert admin.get("/api/v1/problems/stmt").json()["review"]["reviewed_locales"] == ["en"]
    # Reviewing the new text again restores eligibility.
    assert admin.post(_url("stmt", "translations/ru/review")).status_code == 200
    assert admin.post(_url("stmt", "publish")).status_code == 200


def test_statement_review_event_without_a_text_hash_does_not_count(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("nohash", status=ProblemStatus.in_review, locales=("en",), hints=0)
    admin, admin_user = signed_in(Role.admin)
    _review_everything(admin, "nohash", ("en",), hints=False)
    # A later entry in the old format (no statement_sha256) proves nothing about the text.
    audit_service.record(
        db,
        action="problem.translation_reviewed",
        actor_id=admin_user.id,
        target_type="problem",
        target_id=_problem(db, "nohash").id,
        details={"locale": "en"},
    )
    db.commit()
    assert _refused(admin, db, "nohash") == ["statement_unreviewed"]


@pytest.mark.parametrize(
    "tamper", ["no_event", "other_time", "other_verifier", "older_event", "provenance"]
)
def test_rights_confirmation_needs_matching_audit_evidence(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    tamper: str,
) -> None:
    make_problem("rv", status=ProblemStatus.in_review, locales=("en",), hints=0, editorial=False)
    admin, admin_user = signed_in(Role.admin)
    other, other_user = signed_in(Role.admin)
    skip = "rights/verify" if tamper == "no_event" else None
    _review_everything(admin, "rv", ("en",), editorials=False, hints=False, skip=skip)
    problem = _problem(db, "rv")
    if tamper == "no_event":  # confirmation written straight to the database
        problem.rights_verified_by, problem.rights_verified_at = admin_user.id, utcnow()
    elif tamper == "other_time":  # the event belongs to a different confirmation
        assert problem.rights_verified_at is not None
        problem.rights_verified_at -= timedelta(days=1)
    elif tamper == "other_verifier":
        problem.rights_verified_by = other_user.id
    elif tamper == "older_event":  # restore the first confirmation after a newer one
        first = (problem.rights_verified_by, problem.rights_verified_at)
        assert other.post(_url("rv", "rights/verify"), json=ATTEST).status_code == 200
        problem = _problem(db, "rv")
        problem.rights_verified_by, problem.rights_verified_at = first
    else:  # provenance changed after the confirmation, without the importer
        problem.attribution_md = "Different attribution."
    db.commit()
    assert _refused(admin, db, "rv") == ["rights_not_verified"]


@pytest.mark.parametrize("tamper", ["no_event", "band_changed"])
def test_difficulty_review_needs_matching_audit_evidence(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    tamper: str,
) -> None:
    make_problem("dv", status=ProblemStatus.in_review, locales=("en",), hints=0, editorial=False)
    admin, _ = signed_in(Role.admin)
    skip = "difficulty" if tamper == "no_event" else None
    _review_everything(admin, "dv", ("en",), editorials=False, hints=False, skip=skip)
    problem = _problem(db, "dv")
    if tamper == "no_event":
        problem.difficulty_status = DifficultyStatus.reviewed  # never reviewed through the API
    else:
        problem.difficulty_band = Band.hard  # status left "reviewed" for another band
    db.commit()
    assert _refused(admin, db, "dv") == ["difficulty_not_reviewed"]


def test_import_linking_a_reviewer_as_author_voids_their_reviews(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem)
    db.commit()
    reviewer, reviewer_user = signed_in(Role.admin)
    publisher, _ = signed_in(Role.admin)
    locales = tuple(sorted(problem.statements))
    _review_everything(
        reviewer, "sample-sum", locales, hints=bool(problem.hints), band=problem.band
    )
    assert _items(publisher, "sample-sum") == []

    import_problem(db, problem, reviewer_user)  # the reviewer turns out to be the author
    db.commit()
    assert _refused(publisher, db, "sample-sum") == [
        "rights_not_verified",
        "author_review_conflict",
    ]
    conflicts = {
        i["item"] for i in _items(publisher, "sample-sum") if i["code"] == "author_review_conflict"
    }
    assert conflicts == {"statement", "editorial", "hint", "difficulty"} - (
        set() if problem.hints else {"hint"}
    )


def test_reimport_keeps_the_reviewed_rationale_while_the_band_is_unchanged(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem)
    db.commit()
    admin, _ = signed_in(Role.admin)
    rationale = "Reviewer: one pass over the stops is enough."
    body = {"band": problem.band, "rationale": rationale}
    assert admin.patch(_url("sample-sum", "difficulty"), json=body).status_code == 200

    import_problem(db, replace(problem, band_rationale="The author rewrote this text."))
    db.commit()
    row = _problem(db, "sample-sum")
    assert row.difficulty_status == DifficultyStatus.reviewed
    assert row.difficulty_rationale == rationale
    blockers = admin.get("/api/v1/problems/sample-sum").json()["review"]["publish_blockers"]
    assert "difficulty_not_reviewed" not in blockers


def test_reimport_with_a_new_band_resets_the_review_and_its_rationale(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem)
    db.commit()
    admin, _ = signed_in(Role.admin)
    body = {"band": problem.band, "rationale": "Reviewer: one pass over the stops is enough."}
    assert admin.patch(_url("sample-sum", "difficulty"), json=body).status_code == 200

    new_band = next(b for b in Band if b.value != problem.band)
    changed = replace(problem, band=new_band.value, band_rationale="Author: a new estimate.")
    import_problem(db, changed)
    db.commit()
    row = _problem(db, "sample-sum")
    assert row.difficulty_band == new_band
    assert row.difficulty_status == DifficultyStatus.author_estimate
    assert row.difficulty_rationale == "Author: a new estimate."
    blockers = admin.get("/api/v1/problems/sample-sum").json()["review"]["publish_blockers"]
    assert "difficulty_not_reviewed" in blockers


# ------------------------------------------------------------------ difficulty evidence scope


def _import_and_review_difficulty(
    admin: TestClient, db: Session, band: str, rationale: str = "Reviewer: one pass is enough."
) -> content.ProblemContent:
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem)
    db.commit()
    body = {"band": band, "rationale": rationale}
    assert admin.patch(_url("sample-sum", "difficulty"), json=body).status_code == 200
    return problem


def _difficulty_blocked(admin: TestClient) -> bool:
    blockers = admin.get("/api/v1/problems/sample-sum").json()["review"]["publish_blockers"]
    return "difficulty_not_reviewed" in blockers


def _other_band(*not_these: str) -> Band:
    return next(b for b in Band if b.value not in not_these)


def test_reimport_keeps_a_reviewers_band_correction(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    admin, _ = signed_in(Role.admin)
    file_band = content.load_problem(SAMPLE_SUM).band
    corrected = _other_band(file_band)
    problem = _import_and_review_difficulty(admin, db, corrected.value, "Reviewer: harder.")
    import_problem(db, problem)  # the file still says the author's original band
    db.commit()
    row = _problem(db, "sample-sum")
    assert row.difficulty_band == corrected
    assert row.difficulty_status == DifficultyStatus.reviewed
    assert row.difficulty_rationale == "Reviewer: harder."
    assert not _difficulty_blocked(admin)


@pytest.mark.parametrize("adopt_reviewers_band", [False, True])
def test_reimport_after_a_correction_resets_on_a_genuine_band_change(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
    adopt_reviewers_band: bool,
) -> None:
    admin, _ = signed_in(Role.admin)
    file_band = content.load_problem(SAMPLE_SUM).band
    corrected = _other_band(file_band)
    problem = _import_and_review_difficulty(admin, db, corrected.value)
    # The author changes the file's band: to a third band, or to the reviewer's band. Either
    # is a new estimate from the author, so the difficulty is reviewed again.
    new_band = corrected if adopt_reviewers_band else _other_band(file_band, corrected.value)
    import_problem(db, replace(problem, band=new_band.value, band_rationale="Author: new."))
    db.commit()
    row = _problem(db, "sample-sum")
    assert row.difficulty_band == new_band
    assert row.difficulty_status == DifficultyStatus.author_estimate
    assert row.difficulty_rationale == "Author: new."
    assert _difficulty_blocked(admin)


def _changed_spec(
    db: Session, problem: content.ProblemContent, change: str
) -> content.ProblemContent:
    if change == "time_limit_ms":
        return replace(problem, time_limit_ms=problem.time_limit_ms + 500)
    if change == "memory_limit_mb":
        return replace(problem, memory_limit_mb=problem.memory_limit_mb * 2)
    if change == "scoring":
        return replace(problem, scoring="binary" if problem.scoring == "ioi" else "ioi")
    if change == "topics":
        first = problem.topics[0]
        stage = 2 if first.stage == 1 else 1
        return replace(problem, topics=[replace(first, stage=stage), *problem.topics[1:]])
    extra = next(t for t in db.scalars(select(Tag.slug)) if t not in problem.tags)
    return replace(problem, tags=[*problem.tags, extra])


SPEC_CHANGES = ["time_limit_ms", "memory_limit_mb", "scoring", "topics", "tags"]


@pytest.mark.parametrize("change", SPEC_CHANGES)
def test_reimport_with_changed_limits_scoring_or_placement_resets_the_difficulty(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
    change: str,
) -> None:
    admin, _ = signed_in(Role.admin)
    file_band = content.load_problem(SAMPLE_SUM).band
    problem = _import_and_review_difficulty(admin, db, file_band)
    assert not _difficulty_blocked(admin)

    import_problem(db, _changed_spec(db, problem, change))
    db.commit()
    row = _problem(db, "sample-sum")
    assert row.difficulty_status == DifficultyStatus.author_estimate
    assert row.difficulty_rationale == problem.band_rationale
    assert _difficulty_blocked(admin)
    # A new review of the changed problem makes it eligible again.
    body = {"band": file_band, "rationale": "Reviewer: checked the new limits and placement."}
    assert admin.patch(_url("sample-sum", "difficulty"), json=body).status_code == 200
    assert not _difficulty_blocked(admin)


@pytest.mark.parametrize("change", SPEC_CHANGES)
def test_difficulty_evidence_covers_limits_scoring_and_placement(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
    change: str,
) -> None:
    make_problem("spec", status=ProblemStatus.in_review, locales=("en",), hints=0)
    admin, _ = signed_in(Role.admin)
    _review_everything(admin, "spec", ("en",), hints=False)
    problem = _problem(db, "spec")
    if change == "time_limit_ms":
        problem.time_limit_ms += 500
    elif change == "memory_limit_mb":
        problem.memory_limit_mb *= 2
    elif change == "scoring":
        problem.scoring = Scoring.binary
    elif change == "topics":
        problem.topics[0].stage = 2 if problem.topics[0].stage == 1 else 1
    else:
        problem.tags.append(db.scalars(select(Tag)).first())  # type: ignore[arg-type]
    db.commit()  # changed without the importer: the audited evidence no longer matches
    assert _refused(admin, db, "spec") == ["difficulty_not_reviewed"]


def test_difficulty_review_event_without_spec_evidence_does_not_count(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    make_problem("oldspec", status=ProblemStatus.in_review, locales=("en",), hints=0)
    admin, admin_user = signed_in(Role.admin)
    _review_everything(admin, "oldspec", ("en",), hints=False)
    problem = _problem(db, "oldspec")
    audit_service.record(  # a later entry in the old format, without spec_sha256
        db,
        action="problem.difficulty_changed",
        actor_id=admin_user.id,
        target_type="problem",
        target_id=problem.id,
        details={"from": "easy", "to": "easy", "status_from": "reviewed", "rationale": "Old."},
    )
    db.commit()
    assert _refused(admin, db, "oldspec") == ["difficulty_not_reviewed"]
