from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.modules.audit.models import AuditLog
from app.modules.olympiad import content
from app.modules.olympiad.importer import (
    ContentImportError,
    import_curriculum,
    import_problem,
)
from app.modules.olympiad.models import (
    Band,
    Editorial,
    Hint,
    Language,
    Origin,
    Problem,
    ProblemStatus,
    ProblemTopic,
    ProblemTranslation,
    ReferenceSolution,
    Scoring,
    Submission,
    SubmissionState,
    Tag,
    TestCase,
    TestGroup,
    Topic,
    Verdict,
)
from app.modules.users.models import Locale, Role, User

SignedIn = Callable[[Role], tuple[TestClient, User]]
HIDDEN_INPUT = "HIDDEN-TEST-INPUT-should-never-leave-the-api"
# A valid v1 test-set hash (lowercase hex), so fixture problems bind a judging snapshot.
FIXTURE_TESTS_HASH = "0123456789abcdef" * 4


@pytest.fixture
def curriculum(db: Session) -> None:
    import_curriculum(db, content.load_curriculum(get_settings().content_dir / "curriculum.toml"))
    db.commit()


MakeProblem = Callable[..., Problem]


@pytest.fixture
def make_problem(db: Session, curriculum: None) -> MakeProblem:
    def factory(
        slug: str = "demo",
        *,
        status: ProblemStatus = ProblemStatus.published,
        band: Band = Band.easy,
        topics: tuple[tuple[str, int], ...] = (("sorting", 1),),
        tags: tuple[str, ...] = (),
        locales: tuple[str, ...] = ("en", "ru"),
        hints: int = 2,
        editorial: bool = True,
        created_by: User | None = None,
        title: str | None = None,
    ) -> Problem:
        topic_rows = {t.slug: t for t in db.scalars(select(Topic))}
        tag_rows = {t.slug: t for t in db.scalars(select(Tag))}
        problem = Problem(
            slug=slug,
            status=status,
            difficulty_band=band,
            difficulty_rationale="Synthetic test problem.",
            time_limit_ms=1000,
            memory_limit_mb=256,
            scoring=Scoring.ioi,
            origin=Origin.original,
            license="CC-BY-4.0",
            attribution_md="Synthetic test fixture.",
            authors_md="Test suite",
            generated_with_ai=False,
            created_by=created_by.id if created_by else None,
            tests_hash=FIXTURE_TESTS_HASH,
            published_at=utcnow() if status == ProblemStatus.published else None,
            translations=[
                ProblemTranslation(
                    locale=Locale(loc),
                    title=title or f"{slug} [{loc}]",
                    legend_md=f"Legend {loc}",
                    input_md="In",
                    output_md="Out",
                )
                for loc in locales
            ],
            topics=[
                ProblemTopic(topic_id=topic_rows[s].id, stage=st, is_primary=i == 0)
                for i, (s, st) in enumerate(topics)
            ],
            tags=[tag_rows[t] for t in tags],
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
                    input=HIDDEN_INPUT,
                    expected_output="secret",
                ),
                ReferenceSolution(
                    problem_id=problem.id,
                    language=Language.python,
                    source="print()",
                    is_primary=True,
                    verified_at=utcnow(),
                    verified_tests_hash=FIXTURE_TESTS_HASH,
                ),
            ]
        )
        for loc in locales:
            for i in range(1, hints + 1):
                db.add(
                    Hint(
                        problem_id=problem.id,
                        ordinal=i,
                        locale=Locale(loc),
                        body_md=f"hint {i} {loc}",
                    )
                )
            if editorial:
                db.add(
                    Editorial(problem_id=problem.id, locale=Locale(loc), body_md=f"editorial {loc}")
                )
        db.commit()
        return problem

    return factory


def _judged(
    db: Session,
    user: User,
    problem: Problem,
    verdict: Verdict,
    hints: int = 0,
    ago: timedelta = timedelta(0),
    state: SubmissionState = SubmissionState.done,
) -> None:
    """Insert a judged submission directly (no judge exists in Phase 2)."""
    db.add(
        Submission(
            user_id=user.id,
            problem_id=problem.id,
            language=Language.python,
            source_code="x",
            state=state,
            verdict=verdict if state == SubmissionState.done else None,
            score=100 if verdict == Verdict.accepted else 0,
            hints_revealed_count=hints,
            created_at=utcnow() - ago,
        )
    )
    db.commit()


# ------------------------------------------------------------------ catalogue & visibility


def test_topics_and_tags(client: TestClient, curriculum: None) -> None:
    topics = client.get("/api/v1/topics", params={"locale": "ru"}).json()
    by_slug = {t["slug"]: t for t in topics}
    assert by_slug["prefix-sums"]["name"] == "Префиксные суммы"
    assert {
        "stage": 1,
        "requires": "sorting",
        "requires_stage": 1,
        "strength": "required",
    } in by_slug["two-pointers"]["prerequisites"]
    tags = client.get("/api/v1/tags", params={"locale": "en"}).json()
    assert {"slug": "overflow", "name": "Integer overflow"} in tags


def test_only_published_problems_are_public(
    client: TestClient, make_problem: MakeProblem, signed_in: SignedIn
) -> None:
    student, me = signed_in(Role.student)
    make_problem("pub")
    make_problem("draft", status=ProblemStatus.draft)
    make_problem("review", status=ProblemStatus.in_review)
    make_problem("mine", status=ProblemStatus.draft, created_by=me)

    anon = [p["slug"] for p in client.get("/api/v1/problems").json()["items"]]
    assert anon == ["pub"]
    assert client.get("/api/v1/problems/draft").status_code == 404
    assert client.get("/api/v1/problems/nope").json()["error"]["code"] == "not_found"

    own = {p["slug"] for p in student.get("/api/v1/problems").json()["items"]}
    assert own == {"pub", "mine"}
    # Non-admins cannot widen visibility with the status filter.
    assert student.get("/api/v1/problems", params={"status": "draft"}).json()["total"] == 2

    admin, _ = signed_in(Role.admin)
    assert admin.get("/api/v1/problems").json()["total"] == 4
    in_review = admin.get("/api/v1/problems", params={"status": "in_review"}).json()
    assert [p["slug"] for p in in_review["items"]] == ["review"]


def test_problem_filters_search_and_order(client: TestClient, make_problem: MakeProblem) -> None:
    make_problem("b-hard", band=Band.hard, topics=(("greedy", 2),), title="Zeta")
    make_problem(
        "a-easy", band=Band.easy, topics=(("sorting", 1),), tags=("overflow",), title="Alpha 50%"
    )
    make_problem("c-intro", band=Band.intro, topics=(("sorting", 2), ("greedy", 1)))

    def slugs(**params: str) -> list[str]:
        return [p["slug"] for p in client.get("/api/v1/problems", params=params).json()["items"]]

    assert slugs() == ["c-intro", "a-easy", "b-hard"]  # ordered by band, then slug
    assert slugs(topic="greedy") == ["c-intro", "b-hard"]
    assert slugs(tag="overflow") == ["a-easy"]
    assert slugs(band="hard") == ["b-hard"]
    assert slugs(q="alpha") == ["a-easy"]
    assert slugs(q="50%") == ["a-easy"]
    assert slugs(q="_") == []  # LIKE wildcards are matched literally
    page = client.get("/api/v1/problems", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and [p["slug"] for p in page["items"]] == ["a-easy"]
    assert client.get("/api/v1/problems", params={"band": "legendary"}).status_code == 422


def test_problem_detail_locale_fallback_and_no_hidden_tests(
    client: TestClient, make_problem: MakeProblem
) -> None:
    make_problem("p1", locales=("en", "ru"))
    res = client.get("/api/v1/problems/p1", params={"locale": "ky"})
    data = res.json()
    assert data["locale"] == "ru" and data["is_fallback"] is True
    assert data["available_locales"] == ["ru", "en"]
    assert data["samples"] == [{"input": "1 2\n", "output": "3\n"}]
    assert data["groups"] == [{"ordinal": 1, "points": 100, "description_md": "All"}]
    assert data["license"] == "CC-BY-4.0" and data["attribution_md"]
    assert data["hint_count"] == 2 and data["revealed_hint_count"] == 0
    assert data["judge_available"] is False
    assert data["review"] is None  # admin-only
    assert HIDDEN_INPUT not in res.text and "secret" not in res.text

    en = client.get("/api/v1/problems/p1", params={"locale": "en"}).json()
    assert en["title"] == "p1 [en]" and en["is_fallback"] is False


# ------------------------------------------------------------------ hints & editorial


def test_hints_reveal_in_order_and_are_recorded(
    signed_in: SignedIn, make_problem: MakeProblem, client: TestClient
) -> None:
    make_problem("h")
    assert client.get("/api/v1/problems/h/hints").status_code == 401
    student, _ = signed_in(Role.student)

    assert student.get("/api/v1/problems/h/hints").json() == {"total": 2, "revealed": []}
    skip = student.post("/api/v1/problems/h/hints/2/reveal")
    assert skip.status_code == 409 and skip.json()["error"]["code"] == "hint_out_of_order"
    assert student.post("/api/v1/problems/h/hints/3/reveal").status_code == 404

    first = student.post("/api/v1/problems/h/hints/1/reveal", params={"locale": "ru"}).json()
    assert first["revealed"] == [{"ordinal": 1, "locale": "ru", "body_md": "hint 1 ru"}]
    again = student.post("/api/v1/problems/h/hints/1/reveal").json()  # idempotent
    assert len(again["revealed"]) == 1
    student.post("/api/v1/problems/h/hints/2/reveal")
    en = student.get("/api/v1/problems/h/hints", params={"locale": "en"}).json()
    assert [h["body_md"] for h in en["revealed"]] == ["hint 1 en", "hint 2 en"]
    assert student.get("/api/v1/problems/h").json()["revealed_hint_count"] == 2


def test_hint_reveal_requires_csrf(signed_in: SignedIn, make_problem: MakeProblem) -> None:
    make_problem("h")
    student, _ = signed_in(Role.student)
    del student.headers["X-CSRF-Token"]
    assert student.post("/api/v1/problems/h/hints/1/reveal").status_code == 403


def test_editorial_is_locked_until_give_up(signed_in: SignedIn, make_problem: MakeProblem) -> None:
    make_problem("e")
    student, _ = signed_in(Role.student)
    locked = student.get("/api/v1/problems/e/editorial")
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "editorial_locked"
    assert (
        student.post("/api/v1/problems/e/editorial/unlock", json={"reason": "solved"}).status_code
        == 422
    )

    res = student.post(
        "/api/v1/problems/e/editorial/unlock", json={"reason": "gave_up"}, params={"locale": "ky"}
    )
    assert res.status_code == 200
    assert res.json() == {
        "locale": "ru", "is_fallback": True, "body_md": "editorial ru",
        "reviewed": False, "unlock_reason": "gave_up",
    }  # fmt: skip
    assert student.get("/api/v1/problems/e/editorial").status_code == 200
    assert student.get("/api/v1/problems/e").json()["editorial_unlocked"] is True


def test_editorial_unlocks_after_accepted_submission(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    problem = make_problem("e")
    student, me = signed_in(Role.student)
    _judged(db, me, problem, Verdict.wrong_answer)
    assert student.get("/api/v1/problems/e/editorial").status_code == 403
    _judged(db, me, problem, Verdict.accepted)
    assert student.get("/api/v1/problems/e/editorial").json()["unlock_reason"] == "solved"


def test_missing_editorial_is_404(signed_in: SignedIn, make_problem: MakeProblem) -> None:
    make_problem("noed", editorial=False)
    student, _ = signed_in(Role.student)
    assert (
        student.post(
            "/api/v1/problems/noed/editorial/unlock", json={"reason": "gave_up"}
        ).status_code
        == 404
    )


# ------------------------------------------------------------------ submissions


def test_submission_without_judge_is_stored_not_judged(
    signed_in: SignedIn, make_problem: MakeProblem
) -> None:
    make_problem("s")
    student, _ = signed_in(Role.student)
    student.post("/api/v1/problems/s/hints/1/reveal")
    res = student.post(
        "/api/v1/submissions",
        json={"problem_slug": "s", "language": "python", "source_code": "print(3)"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["state"] == "not_judged" and data["verdict"] is None and data["score"] is None
    assert data["hints_revealed_count"] == 1
    assert data["source_code"] == "print(3)"
    assert student.get("/api/v1/problems/s").json()["my_status"] == "attempted"


@pytest.mark.parametrize(
    "body",
    [
        {"problem_slug": "s", "language": "pascal", "source_code": "x"},
        {"problem_slug": "s", "language": "python", "source_code": ""},
        {"problem_slug": "s", "language": "python", "source_code": "x" * (64 * 1024 + 1)},
        {"problem_slug": "s", "language": "python", "source_code": "x", "verdict": "accepted"},
    ],
)
def test_submission_validation(
    signed_in: SignedIn, make_problem: MakeProblem, body: dict[str, str]
) -> None:
    make_problem("s")
    student, _ = signed_in(Role.student)
    assert student.post("/api/v1/submissions", json=body).status_code == 422


def test_submission_size_is_measured_in_bytes(
    signed_in: SignedIn, make_problem: MakeProblem
) -> None:
    make_problem("s")
    student, _ = signed_in(Role.student)
    body = {"problem_slug": "s", "language": "python", "source_code": "ж" * (40 * 1024)}  # 80 KiB
    res = student.post("/api/v1/submissions", json=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "source_too_large"


def test_cannot_submit_to_hidden_problem(signed_in: SignedIn, make_problem: MakeProblem) -> None:
    make_problem("d", status=ProblemStatus.draft)
    student, _ = signed_in(Role.student)
    body = {"problem_slug": "d", "language": "cpp", "source_code": "int main(){}"}
    assert student.post("/api/v1/submissions", json=body).status_code == 404


def test_submissions_are_rate_limited(signed_in: SignedIn, make_problem: MakeProblem) -> None:
    make_problem("s")
    student, _ = signed_in(Role.student)
    body = {"problem_slug": "s", "language": "python", "source_code": "x"}
    for _ in range(6):
        assert student.post("/api/v1/submissions", json=body).status_code == 201
    res = student.post("/api/v1/submissions", json=body)
    assert res.status_code == 429 and "Retry-After" in res.headers


def test_submission_history_is_private(
    signed_in: SignedIn, make_problem: MakeProblem, client: TestClient
) -> None:
    make_problem("s")
    make_problem("t")
    alice, _ = signed_in(Role.student)
    bob, _ = signed_in(Role.student)
    body = {"problem_slug": "s", "language": "python", "source_code": "alice-secret"}
    sid = alice.post("/api/v1/submissions", json=body).json()["id"]
    alice.post("/api/v1/submissions", json={**body, "problem_slug": "t"})

    history = alice.get("/api/v1/submissions").json()
    assert history["total"] == 2 and "source_code" not in history["items"][0]
    assert alice.get("/api/v1/submissions", params={"problem": "s"}).json()["total"] == 1
    assert bob.get("/api/v1/submissions").json()["total"] == 0
    other = bob.get(f"/api/v1/submissions/{sid}")
    assert other.status_code == 404 and "alice-secret" not in other.text
    assert client.get(f"/api/v1/submissions/{sid}").status_code == 401

    admin, _ = signed_in(Role.admin)
    assert admin.get(f"/api/v1/submissions/{sid}").json()["source_code"] == "alice-secret"


# ------------------------------------------------------------------ self-reports & progress


def test_self_report_is_idempotent_and_never_counts_as_evidence(
    signed_in: SignedIn, make_problem: MakeProblem
) -> None:
    make_problem("sr")
    student, _ = signed_in(Role.student)
    first = student.post("/api/v1/problems/sr/self-report").json()
    second = student.post("/api/v1/problems/sr/self-report").json()
    assert first == second
    progress = student.get("/api/v1/me/progress").json()
    assert [r["problem_slug"] for r in progress["self_reported"]] == ["sr"]
    assert progress["demonstrated_topics"] == 0
    sorting = next(t for t in progress["topics"] if t["slug"] == "sorting")
    assert sorting["stages"][0] == {
        "stage": 1,
        "unaided_by_band": {},
        "with_hints": 0,
        "demonstrated": False,
    }
    assert student.get("/api/v1/problems/sr").json()["my_status"] == "self_reported"


def test_progress_counts_judged_evidence_only(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session, client: TestClient
) -> None:
    assert client.get("/api/v1/me/progress").status_code == 401
    student, me = signed_in(Role.student)
    p1 = make_problem("p1", topics=(("sorting", 1),), band=Band.easy)
    p2 = make_problem("p2", topics=(("sorting", 2),), band=Band.medium)
    p3 = make_problem("p3", topics=(("greedy", 1),), band=Band.easy)
    p4 = make_problem("p4", topics=(("greedy", 1),), band=Band.easy)

    _judged(db, me, p1, Verdict.compile_error, ago=timedelta(hours=3))  # not an attempt
    _judged(db, me, p1, Verdict.wrong_answer, ago=timedelta(hours=2))
    _judged(db, me, p1, Verdict.accepted, ago=timedelta(hours=1))  # 2 attempts, unaided
    _judged(db, me, p2, Verdict.accepted)  # stage 2 counts toward stage 1 too
    _judged(db, me, p3, Verdict.accepted, hints=2)  # with hints
    _judged(db, me, p4, Verdict.accepted, state=SubmissionState.not_judged)  # pending

    data = student.get("/api/v1/me/progress").json()
    sorting = next(t for t in data["topics"] if t["slug"] == "sorting")
    assert sorting["stages"][0] == {
        "stage": 1,
        "unaided_by_band": {"easy": 1},
        "with_hints": 0,
        "demonstrated": True,
    }
    assert sorting["stages"][1]["demonstrated"] is False  # one problem < threshold 2
    greedy = next(t for t in data["topics"] if t["slug"] == "greedy")
    assert greedy["stages"][0] == {
        "stage": 1,
        "unaided_by_band": {},
        "with_hints": 1,
        "demonstrated": False,
    }
    assert data["demonstrated_topics"] == 1 and data["total_topics"] == 13
    assert data["median_attempts_to_accept"] == 1.0  # [2, 1, 1]
    assert data["hint_reliance_30d"] == pytest.approx(1 / 3)
    assert data["pending_not_judged"] == 1
    assert data["demonstrated_threshold"] == 2


# ------------------------------------------------------------------ review & publishing


def test_admin_review_endpoints_require_admin(
    signed_in: SignedIn, make_problem: MakeProblem, client: TestClient
) -> None:
    make_problem("r", status=ProblemStatus.in_review)
    for role in (Role.student, Role.teacher):
        c, _ = signed_in(role)
        assert c.post("/api/v1/admin/problems/r/publish").status_code == 403
        body = {"band": "easy", "rationale": "Looks right to me."}
        assert c.patch("/api/v1/admin/problems/r/difficulty", json=body).status_code == 403
    assert client.post("/api/v1/admin/problems/r/publish").status_code == 401


def test_publish_gate_and_audit(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    admin, admin_user = signed_in(Role.admin)
    make_problem("r", status=ProblemStatus.in_review)

    blocked = admin.post("/api/v1/admin/problems/r/publish")
    assert blocked.status_code == 409
    expected = [
        "rights_not_verified",
        "statement_unreviewed",
        "editorial_unreviewed",
        "hint_unreviewed",
        "difficulty_not_reviewed",
    ]
    assert blocked.json()["error"]["details"] == expected
    assert admin.get("/api/v1/problems/r").json()["review"]["publish_blockers"] == expected

    res = admin.patch(
        "/api/v1/admin/problems/r/difficulty",
        json={"band": "medium", "rationale": "Needs two ideas combined."},
    )
    assert (
        res.json()["difficulty_band"] == "medium" and res.json()["difficulty_status"] == "reviewed"
    )
    assert (
        admin.patch(
            "/api/v1/admin/problems/r/difficulty", json={"band": "medium", "rationale": "short"}
        ).status_code
        == 422
    )
    assert admin.post("/api/v1/admin/problems/r/translations/ky/review").status_code == 404
    res = admin.post("/api/v1/admin/problems/r/translations/en/review")
    assert res.json()["review"]["reviewed_locales"] == ["en"]
    res = admin.post("/api/v1/admin/problems/r/rights/verify", json={"attest_not_author": True})
    assert res.json()["review"]["rights_verified_at"] is not None
    # One reviewed language is not enough: every statement, editorial and hint must be.
    blocked = admin.post("/api/v1/admin/problems/r/publish")
    assert blocked.json()["error"]["details"] == [
        "statement_unreviewed",
        "editorial_unreviewed",
        "hint_unreviewed",
    ]
    attest = {"attest_not_author": True}
    assert admin.post("/api/v1/admin/problems/r/translations/ru/review").status_code == 200
    for loc in ("en", "ru"):
        assert admin.post(
            f"/api/v1/admin/problems/r/editorials/{loc}/review", json=attest
        ).is_success
        assert admin.post(f"/api/v1/admin/problems/r/hints/{loc}/review", json=attest).is_success

    published = admin.post("/api/v1/admin/problems/r/publish")
    assert published.status_code == 200 and published.json()["status"] == "published"
    again = admin.post("/api/v1/admin/problems/r/publish")
    assert again.json()["error"]["details"] == ["already_published"]
    assert admin.post("/api/v1/admin/problems/r/unpublish").json()["status"] == "in_review"

    actions = list(db.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))
    assert actions == [
        "problem.difficulty_changed",
        "problem.translation_reviewed",
        "problem.rights_verified",
        "problem.translation_reviewed",
        *["problem.editorial_reviewed", "problem.hint_reviewed", "problem.hint_reviewed"] * 2,
        "problem.published",
        "problem.unpublished",
    ]
    change = db.scalars(
        select(AuditLog).where(AuditLog.action == "problem.difficulty_changed")
    ).one()
    assert change.actor_id == admin_user.id
    assert change.details["from"] == "easy" and change.details["to"] == "medium"


def test_author_cannot_review_or_publish_own_problem(
    signed_in: SignedIn, make_problem: MakeProblem
) -> None:
    admin, me = signed_in(Role.admin)
    make_problem("own", status=ProblemStatus.in_review, created_by=me)
    body = {"band": "easy", "rationale": "I think it is easy."}
    res = admin.patch("/api/v1/admin/problems/own/difficulty", json=body)
    assert res.status_code == 403 and res.json()["error"]["code"] == "reviewer_is_author"
    assert admin.post("/api/v1/admin/problems/own/translations/en/review").status_code == 403
    assert (
        admin.post(
            "/api/v1/admin/problems/own/rights/verify", json={"attest_not_author": True}
        ).status_code
        == 403
    )
    assert (
        "approver_is_author"
        in admin.post("/api/v1/admin/problems/own/publish").json()["error"]["details"]
    )


def test_stale_reference_solution_blocks_publishing(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    admin, _ = signed_in(Role.admin)
    problem = make_problem("stale", status=ProblemStatus.in_review)
    problem.tests_hash = "0" * 64  # tests changed after the solution was verified
    db.commit()
    details = admin.post("/api/v1/admin/problems/stale/publish").json()["error"]["details"]
    assert "no_verified_reference_solution" in details


# ------------------------------------------------------------------ importer


def test_import_real_problem(db: Session, curriculum: None) -> None:
    problem = content.load_problem(get_settings().content_dir / "problems" / "sample-sum")
    result = import_problem(db, problem)
    db.commit()
    assert result.created and result.tests > 2
    row = db.scalars(select(Problem).where(Problem.slug == "sample-sum")).one()
    assert row.status == ProblemStatus.in_review and row.generated_with_ai
    assert {t.locale for t in row.translations} == {Locale.en, Locale.ru}
    solutions = {
        s.language: s
        for s in db.scalars(select(ReferenceSolution).where(ReferenceSolution.problem_id == row.id))
    }
    assert solutions[Language.python].verified_tests_hash == row.tests_hash
    assert solutions[Language.cpp].verified_at is None

    # Unchanged statements keep their review; changed ones lose it.
    for t in row.translations:
        t.reviewed_by, t.reviewed_at = None, utcnow()
    en = next(t for t in row.translations if t.locale == Locale.en)
    ru = next(t for t in row.translations if t.locale == Locale.ru)
    reviewer = User(
        email="rev@example.com", password_hash="x", display_name="Reviewer", role=Role.admin
    )
    db.add(reviewer)
    db.flush()
    en.reviewed_by = ru.reviewed_by = reviewer.id
    db.commit()
    changed = replace(
        problem,
        statements={**problem.statements, "ru": replace(problem.statements["ru"], title="Новое")},
    )
    assert not import_problem(db, changed).created
    db.commit()
    reviewed = {
        t.locale: t.reviewed_by
        for t in db.scalars(
            select(ProblemTranslation).where(ProblemTranslation.problem_id == row.id)
        )
    }
    assert reviewed == {Locale.en: reviewer.id, Locale.ru: None}

    row.status = ProblemStatus.published
    row.published_at = utcnow()
    db.commit()
    with pytest.raises(ContentImportError, match="published"):
        import_problem(db, problem)


# ------------------------------------------------------------------ isolation & integrity


def test_hints_unlocks_and_progress_are_per_user(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    problem = make_problem("iso")
    alice, alice_user = signed_in(Role.student)
    bob, _ = signed_in(Role.student)
    alice.post("/api/v1/problems/iso/hints/1/reveal")
    alice.post("/api/v1/problems/iso/editorial/unlock", json={"reason": "gave_up"})
    alice.post("/api/v1/problems/iso/self-report")
    _judged(db, alice_user, problem, Verdict.accepted)

    assert bob.get("/api/v1/problems/iso/hints").json()["revealed"] == []
    assert bob.get("/api/v1/problems/iso/editorial").status_code == 403
    detail = bob.get("/api/v1/problems/iso").json()
    assert detail["revealed_hint_count"] == 0 and detail["my_status"] is None
    assert detail["editorial_unlocked"] is False
    progress = bob.get("/api/v1/me/progress").json()
    assert progress["self_reported"] == [] and progress["median_attempts_to_accept"] is None
    # Bob cannot reveal "after" Alice: ordering is per user.
    assert bob.post("/api/v1/problems/iso/hints/2/reveal").status_code == 409


def test_idempotent_inserts_do_not_fail_on_duplicates(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    from app.modules.olympiad.models import HintReveal, SelfReport
    from app.modules.olympiad.service import _insert_once

    problem = make_problem("dup")
    _, me = signed_in(Role.student)
    for _ in range(2):  # what two concurrent requests would do
        _insert_once(db, SelfReport, user_id=me.id, problem_id=problem.id)
        _insert_once(db, HintReveal, user_id=me.id, problem_id=problem.id, ordinal=1)
    assert len(db.scalars(select(SelfReport)).all()) == 1
    assert len(db.scalars(select(HintReveal)).all()) == 1


def test_database_rejects_inconsistent_rows(
    make_problem: MakeProblem, db: Session, make_user: Callable[..., User]
) -> None:
    from sqlalchemy.exc import IntegrityError

    problem = make_problem("db")
    user = make_user()
    bad_rows = [
        Submission(  # verdict before judging finished
            user_id=user.id, problem_id=problem.id, language=Language.python, source_code="x",
            state=SubmissionState.not_judged, verdict=Verdict.accepted,
        ),
        Submission(
            user_id=user.id, problem_id=problem.id, language=Language.python, source_code="x",
            state=SubmissionState.not_judged, hints_revealed_count=-1,
        ),
        ProblemTopic(  # second primary topic
            problem_id=problem.id,
            topic_id=db.scalars(select(Topic).where(Topic.slug == "greedy")).one().id,
            stage=1, is_primary=True,
        ),
        Hint(problem_id=problem.id, ordinal=0, locale=Locale.en, body_md="x"),
    ]  # fmt: skip
    for row in bad_rows:
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(row)
                db.flush()
    problem.status = ProblemStatus.published
    problem.published_at = None
    with pytest.raises(IntegrityError), db.begin_nested():
        db.flush()


def test_problem_sorting(client: TestClient, make_problem: MakeProblem, db: Session) -> None:
    old = make_problem("old", band=Band.intro)
    make_problem("new", band=Band.hard)
    old.created_at = utcnow() - timedelta(days=1)
    db.commit()
    by_band = [p["slug"] for p in client.get("/api/v1/problems").json()["items"]]
    newest = [
        p["slug"] for p in client.get("/api/v1/problems", params={"sort": "newest"}).json()["items"]
    ]
    assert by_band == ["old", "new"] and newest == ["new", "old"]
    assert client.get("/api/v1/problems", params={"sort": "random"}).status_code == 422


def test_progress_writes_are_rate_limited(signed_in: SignedIn, make_problem: MakeProblem) -> None:
    make_problem("rl")
    student, _ = signed_in(Role.student)
    for _ in range(60):
        assert student.post("/api/v1/problems/rl/self-report").status_code == 200
    assert student.post("/api/v1/problems/rl/self-report").status_code == 429
