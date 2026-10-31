"""Content provenance: structured sources, license compatibility, human rights verification."""

import json
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.modules.audit.models import AuditLog
from app.modules.olympiad import content, service
from app.modules.olympiad.content import ContentError, Source
from app.modules.olympiad.importer import ContentImportError, import_problem
from app.modules.olympiad.models import Origin, Problem, ProblemStatus
from app.modules.users.models import Role, User
from tests.test_olympiad import MakeProblem, SignedIn, curriculum, make_problem  # noqa: F401

SAMPLE_SUM = get_settings().content_dir / "problems" / "sample-sum"
ATTEST = {"attest_not_author": True}
SOURCE = {
    "title": "Synthetic source task",
    "author": "Example Author",
    "url": "https://example.com/task",
    "license": "CC-BY-4.0",
}


def _variant(tmp_path: Path, *, origin: str, license: str, source: dict[str, str] | None) -> Path:
    """A copy of sample-sum with different provenance (parsed only, never executed here)."""
    target = tmp_path / "sample-sum"
    shutil.copytree(SAMPLE_SUM, target)
    toml = target / "problem.toml"
    text = toml.read_text(encoding="utf-8")
    text = text.replace('origin = "original"', f'origin = "{origin}"', 1)
    text = text.replace('license = "CC-BY-4.0"', f'license = "{license}"', 1)
    if source is not None:
        text += "\n[source]\n" + "".join(f'{k} = "{v}"\n' for k, v in source.items())
    toml.write_text(text, encoding="utf-8")
    return target


# ------------------------------------------------------------------ loader


def test_existing_content_is_original_without_source() -> None:
    problems = content.load_problems(get_settings().content_dir / "problems")
    assert problems and all(p.origin == "original" and p.source is None for p in problems)


@pytest.mark.parametrize(
    ("origin", "license", "source", "message"),
    [
        ("original", "CC-BY-4.0", SOURCE, "original problem has no"),
        ("adapted", "CC-BY-4.0", None, "requires a \\[source\\]"),
        ("licensed", "CC-BY-4.0", None, "requires a \\[source\\]"),
        ("adapted", "CC-BY-4.0", {k: v for k, v in SOURCE.items() if k != "url"}, "needs exactly"),
        ("adapted", "CC-BY-4.0", {**SOURCE, "author": " "}, "must not be empty"),
        ("adapted", "CC-BY-4.0", {**SOURCE, "url": "ftp://example.com/x"}, "http"),
        ("adapted", "CC-BY-4.0", {**SOURCE, "license": "MIT"}, "legal check"),
        ("licensed", "CC-BY-SA-4.0", SOURCE, "keeps its source's license"),
        ("adapted", "CC-BY-4.0", {**SOURCE, "license": "CC-BY-SA-4.0"}, "not allowed"),
        ("adapted", "CC0-1.0", SOURCE, "not allowed"),
    ],
)
def test_loader_rejects_bad_provenance(
    tmp_path: Path, origin: str, license: str, source: dict[str, str] | None, message: str
) -> None:
    directory = _variant(tmp_path, origin=origin, license=license, source=source)
    with pytest.raises(ContentError, match=message):
        content.load_problem(directory)


@pytest.mark.parametrize(
    ("origin", "license", "source_license"),
    [
        ("licensed", "CC-BY-4.0", "CC-BY-4.0"),
        ("adapted", "CC-BY-SA-4.0", "CC-BY-4.0"),
        ("adapted", "CC-BY-SA-4.0", "CC-BY-SA-4.0"),
        ("adapted", "CC-BY-4.0", "CC0-1.0"),
    ],
)
def test_loader_accepts_compatible_provenance(
    tmp_path: Path, origin: str, license: str, source_license: str
) -> None:
    source = {**SOURCE, "license": source_license}
    problem = content.load_problem(
        _variant(tmp_path, origin=origin, license=license, source=source)
    )
    assert problem.source == Source(**source)


# ------------------------------------------------------------------ importer


def test_import_never_verifies_rights_and_clears_them_when_provenance_changes(
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem)
    db.commit()
    row = db.scalars(select(Problem).where(Problem.slug == "sample-sum")).one()
    assert row.rights_verified_at is None and row.source is None
    # "No source" is SQL NULL, not the JSON value null (migration 0007).
    assert db.scalar(text("SELECT source IS NULL FROM problems WHERE slug = 'sample-sum'"))

    reviewer = User(
        email="rights@example.com", password_hash="x", display_name="Reviewer", role=Role.admin
    )
    db.add(reviewer)
    db.flush()
    row.rights_verified_by, row.rights_verified_at = reviewer.id, utcnow()
    db.commit()

    import_problem(db, problem)  # same provenance: the human check stands
    db.commit()
    db.refresh(row)
    assert row.rights_verified_by == reviewer.id

    adapted = replace(problem, origin="adapted", license="CC-BY-SA-4.0", source=Source(**SOURCE))
    import_problem(db, adapted)
    db.commit()
    db.refresh(row)
    assert row.rights_verified_by is None and row.rights_verified_at is None
    assert row.origin == Origin.adapted and row.source == SOURCE


def test_database_requires_a_source_for_reused_problems(
    db: Session,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    problem = make_problem("reused", status=ProblemStatus.in_review)
    problem.origin = Origin.adapted
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_orm_stores_a_missing_source_as_sql_null(
    db: Session,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    problem = make_problem("original", status=ProblemStatus.in_review)
    problem.source = None
    db.commit()
    stored = db.execute(
        text("SELECT source IS NULL, jsonb_typeof(source) FROM problems WHERE slug = 'original'")
    ).one()
    assert tuple(stored) == (True, None)


@pytest.mark.parametrize("json_value", ["null", '"a string"', "[]", "42"])
def test_database_rejects_a_reused_problem_without_a_source_object(
    db: Session,
    make_problem: MakeProblem,  # noqa: F811
    json_value: str,
) -> None:
    """Regression: JSON null (and other non-objects) used to satisfy `source IS NOT NULL`."""
    make_problem("reused", status=ProblemStatus.in_review)
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "UPDATE problems SET origin = 'adapted', source = CAST(:value AS jsonb) "
                "WHERE slug = 'reused'"
            ),
            {"value": json_value},
        )
    db.rollback()


def test_database_rejects_a_source_on_an_original_problem(
    db: Session,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    make_problem("original", status=ProblemStatus.in_review)
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE problems SET source = CAST(:value AS jsonb) WHERE slug = 'original'"),
            {"value": json.dumps(SOURCE)},
        )
    db.rollback()


def test_database_accepts_a_reused_problem_with_a_source_object(
    db: Session,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    problem = make_problem("reused", status=ProblemStatus.in_review)
    problem.origin, problem.license, problem.source = Origin.adapted, "CC-BY-SA-4.0", SOURCE
    db.commit()
    assert db.scalar(text("SELECT jsonb_typeof(source) FROM problems WHERE slug = 'reused'")) == (
        "object"
    )


# ------------------------------------------------------------------ verification endpoint


def test_verify_rights_requires_admin(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    client: TestClient,
) -> None:
    make_problem("r", status=ProblemStatus.in_review)
    url = "/api/v1/admin/problems/r/rights/verify"
    assert client.post(url, json=ATTEST).status_code == 401
    for role in (Role.student, Role.teacher):
        c, _ = signed_in(role)
        assert c.post(url, json=ATTEST).status_code == 403
    admin, _ = signed_in(Role.admin)
    missing = admin.post("/api/v1/admin/problems/missing/rights/verify", json=ATTEST)
    assert missing.status_code == 404
    extra = admin.post(url, json={**ATTEST, "verified": True})
    assert extra.status_code == 422  # RequestModel forbids unknown fields


def test_verify_rights_is_recorded_and_unblocks_only_that_step(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    admin, admin_user = signed_in(Role.admin)
    problem = make_problem("r", status=ProblemStatus.in_review)
    problem.origin, problem.license, problem.source = Origin.adapted, "CC-BY-SA-4.0", SOURCE
    problem.generated_with_ai = True
    db.commit()

    before = admin.get("/api/v1/problems/r").json()
    assert "rights_not_verified" in before["review"]["publish_blockers"]
    assert before["source"] == SOURCE

    # Imported problems have no author account: the reviewer must attest, not just click.
    url = "/api/v1/admin/problems/r/rights/verify"
    for body in ({}, {"attest_not_author": False}):
        refused = admin.post(url, json=body)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "author_attestation_required"
    assert before["review"]["author_linked"] is False

    res = admin.post(url, json=ATTEST)
    assert res.status_code == 200
    assert res.json()["review"]["rights_verified_at"] is not None
    assert "rights_not_verified" not in res.json()["review"]["publish_blockers"]
    assert "statement_unreviewed" in res.json()["review"]["publish_blockers"]

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "problem.rights_verified")).one()
    assert entry.actor_id == admin_user.id and entry.target_id == str(problem.id)
    db.refresh(problem)
    details = dict(entry.details or {})
    # The evidence that binds this event to this confirmation and this provenance.
    assert datetime.fromisoformat(details.pop("verified_at")) == problem.rights_verified_at
    assert details.pop("provenance_sha256") == service.provenance_sha256(problem)
    assert details == {
        "origin": "adapted",
        "license": "CC-BY-SA-4.0",
        "generated_with_ai": True,
        "source_url": SOURCE["url"],
        "author_identity": "unlinked",
        "attested_not_author": True,
    }


def test_author_cannot_verify_rights(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
) -> None:
    admin, me = signed_in(Role.admin)
    make_problem("own", status=ProblemStatus.in_review, created_by=me)
    # Attesting does not override a known identity.
    res = admin.post("/api/v1/admin/problems/own/rights/verify", json=ATTEST)
    assert res.status_code == 403 and res.json()["error"]["code"] == "reviewer_is_author"


def test_linked_author_is_checked_by_the_server_without_attestation(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    _, author = signed_in(Role.admin)
    reviewer, _ = signed_in(Role.admin)
    make_problem("linked", status=ProblemStatus.in_review, created_by=author)
    assert reviewer.get("/api/v1/problems/linked").json()["review"]["author_linked"] is True
    res = reviewer.post("/api/v1/admin/problems/linked/rights/verify", json={})
    assert res.status_code == 200
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "problem.rights_verified")).one()
    assert entry.details["author_identity"] == "linked"
    assert entry.details["attested_not_author"] is False


def test_import_can_link_the_author_so_the_author_is_refused(
    signed_in: SignedIn,
    db: Session,
    curriculum: None,  # noqa: F811
) -> None:
    """Audit F-05: an imported problem linked to its author's account is protected."""
    admin, me = signed_in(Role.admin)
    _, other_user = signed_in(Role.admin)
    problem = content.load_problem(SAMPLE_SUM)
    import_problem(db, problem, author=me)
    db.commit()
    row = db.scalars(select(Problem).where(Problem.slug == "sample-sum")).one()
    assert row.created_by == me.id

    res = admin.post("/api/v1/admin/problems/sample-sum/rights/verify", json=ATTEST)
    assert res.status_code == 403 and res.json()["error"]["code"] == "reviewer_is_author"
    blockers = admin.post("/api/v1/admin/problems/sample-sum/publish").json()["error"]["details"]
    assert "approver_is_author" in blockers

    with pytest.raises(ContentImportError, match="another author"):
        import_problem(db, problem, author=other_user)
    db.rollback()

    # Linking an author later is a provenance change: an earlier confirmation is cleared.
    row.created_by = None
    row.rights_verified_by, row.rights_verified_at = other_user.id, utcnow()
    db.commit()
    import_problem(db, problem, author=me)
    db.commit()
    db.refresh(row)
    assert row.created_by == me.id and row.rights_verified_at is None


def test_students_see_the_source_but_not_review_state(
    signed_in: SignedIn,
    make_problem: MakeProblem,  # noqa: F811
    db: Session,
) -> None:
    student, _ = signed_in(Role.student)
    problem = make_problem("pub")
    problem.origin, problem.source = Origin.licensed, SOURCE
    db.commit()
    body = student.get("/api/v1/problems/pub").json()
    assert body["source"] == SOURCE and body["review"] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["sample-sum"],  # no author choice
        ["--author-email", "a@example.com", "--author-unlinked", "sample-sum"],  # both
    ],
)
def test_import_script_requires_an_explicit_author_choice(argv: list[str]) -> None:
    """Linking the author cannot be skipped by accident (audit F-05)."""
    from scripts.import_content import main

    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
