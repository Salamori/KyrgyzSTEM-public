"""Regression tests for findings in docs/SECURITY_AUDIT.md (M2 independent audit).

Each test names the finding it covers (F-xx). They exercise the public HTTP surface the
way an attacker or a confused client would.
"""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter
from app.modules.ai.provider import ProviderError
from app.modules.olympiad.models import Verdict
from app.modules.orgs.models import OrgMembership, OrgRole
from app.modules.users.models import Role, User
from tests.test_ai import FakeModel, ask, fake, no_model  # noqa: F401
from tests.test_olympiad import MakeProblem, _judged, curriculum, make_problem  # noqa: F401
from tests.test_orgs import OrgWorld, build_org  # noqa: F401

SignedIn = Callable[[Role], tuple[TestClient, User]]

NUL = "\x00"


# ------------------------------------------------------------------ F-03 malformed input → 500


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/api/v1/users/me", {"display_name": f"a{NUL}b"}),
        ("POST", "/api/v1/submissions", {"problem_slug": "demo", "language": "python",
                                         "source_code": f"print(1){NUL}"}),
        ("POST", "/api/v1/submissions", {"problem_slug": f"de{NUL}mo", "language": "python",
                                         "source_code": "print(1)"}),
        ("POST", "/api/v1/groups/join", {"code": f"ABCD{NUL}EFG"}),
    ],
)  # fmt: skip
def test_nul_bytes_in_bodies_are_rejected_not_500(
    signed_in: SignedIn, make_problem: MakeProblem, method: str, path: str, body: dict[str, Any]
) -> None:
    make_problem("demo")
    student, _ = signed_in(Role.student)
    res = student.request(method, path, json=body)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/problems?q=%00",
        "/api/v1/problems/de%00mo",
        "/api/v1/problems?offset=100000000000000000000",
        "/api/v1/submissions?offset=100000000000000000000",
    ],
)
def test_malformed_query_and_path_values_are_client_errors(
    signed_in: SignedIn, make_problem: MakeProblem, path: str
) -> None:
    make_problem("demo")
    student, _ = signed_in(Role.student)
    res = student.get(path)
    assert 400 <= res.status_code < 500, res.text
    assert "error" in res.json()


def test_nul_byte_in_org_names_is_rejected(build_org: Callable[[str], OrgWorld]) -> None:
    w = build_org("nul")
    res = w.clients["lead"].post(
        f"/api/v1/orgs/{w.org.id}/groups", json={"name": f"x{NUL}", "kind": "club"}
    )
    assert res.status_code == 422


# ------------------------------------------------------------------ F-04 CSV, non-ASCII names


def test_csv_export_works_for_cyrillic_group_names(
    build_org: Callable[[str], OrgWorld], db: Session
) -> None:
    w = build_org("cyr")
    w.group.name = "Олимпиадный кружок 9-Б"
    db.commit()
    res = w.clients["lead"].get(f"/api/v1/orgs/{w.org.id}/groups/{w.group.id}/attendance.csv")
    assert res.status_code == 200
    disposition = res.headers["content-disposition"]
    assert disposition.isascii()
    assert 'filename="attendance-' in disposition
    # The readable name survives for browsers that support RFC 5987.
    assert "filename*=UTF-8''attendance-" in disposition
    assert "%D0%9E" in disposition  # Cyrillic capital O, UTF-8


# ------------------------------------------------------------------ F-05 last org admin


def test_last_org_admin_cannot_be_demoted_through_add_staff(
    build_org: Callable[[str], OrgWorld], db: Session
) -> None:
    w = build_org("demote")
    res = w.clients["admin"].post(
        f"/api/v1/orgs/{w.org.id}/members", json={"email": w.admin.email, "role": "teacher"}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "last_org_admin"
    role = db.scalar(
        select(OrgMembership.role).where(
            OrgMembership.org_id == w.org.id, OrgMembership.user_id == w.admin.id
        )
    )
    assert role == OrgRole.org_admin


def test_one_of_two_org_admins_can_still_be_demoted(
    build_org: Callable[[str], OrgWorld], make_user: Callable[..., User]
) -> None:
    w = build_org("demote2")
    second = make_user(email="second-admin@example.com")
    url = f"/api/v1/orgs/{w.org.id}/members"
    admin = w.clients["admin"]
    assert admin.post(url, json={"email": second.email, "role": "org_admin"}).status_code == 201
    assert admin.post(url, json={"email": second.email, "role": "teacher"}).status_code == 201


# ------------------------------------------------------------------ F-06 AI quota counts paid calls


def test_withheld_ai_answers_count_towards_the_daily_quota(
    fake: FakeModel,
    signed_in: SignedIn,
    make_problem: MakeProblem,
) -> None:
    make_problem("p")
    student, _ = signed_in(Role.student)
    get_settings().ai_daily_quota_per_user = 2
    # Asking for code at level 1 makes every answer "withheld", but each one is a paid call.
    fake.reply = {"grounded": [], "suggestion": "#include <x>", "insufficient_sources": False}
    outcomes = [ask(student).json()["outcome"] for _ in range(4)]
    assert outcomes == ["code_withheld", "code_withheld", "quota_exceeded", "quota_exceeded"]
    assert len(fake.requests) == 2


def test_provider_failures_count_towards_the_global_daily_cap(
    fake: FakeModel,
    signed_in: SignedIn,
    make_problem: MakeProblem,
) -> None:
    make_problem("p")
    get_settings().ai_global_daily_cap = 1
    fake.error = ProviderError("timeout")
    first, _ = signed_in(Role.student)
    assert ask(first).json()["outcome"] == "provider_timeout"
    second, _ = signed_in(Role.student)
    assert ask(second).json()["outcome"] == "budget_exhausted"
    assert len(fake.requests) == 1


# ------------------------------------------------------------------ F-07 insecure defaults


def test_production_refuses_insecure_session_cookies() -> None:
    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        Settings(app_env="production", session_cookie_secure=False)
    Settings(app_env="production", session_cookie_secure=True)  # ok
    Settings(app_env="development", session_cookie_secure=False)  # ok for local HTTP


# ------------------------------------------------------------------ F-08 limiter memory growth


def test_rate_limiter_forgets_idle_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    limiter = RateLimiter()
    for i in range(5000):
        limiter.hit(f"login:ip:{i}@example.com", limit=10, window_seconds=60)
    clock[0] += 61
    limiter.hit("login:ip:fresh@example.com", limit=10, window_seconds=60)
    limiter.sweep()
    assert len(limiter._hits) == 1


# ------------------------------------------------------------------ F-17 defect DF1 (fixed)


def test_accept_after_giving_up_is_not_unaided(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    problem = make_problem("df1", hints=0)
    student, me = signed_in(Role.student)
    assert student.post(
        "/api/v1/problems/df1/editorial/unlock", json={"reason": "gave_up"}
    ).is_success
    _judged(db, me, problem, Verdict.accepted)  # solved after reading the editorial
    sorting = next(
        t for t in student.get("/api/v1/me/progress").json()["topics"] if t["slug"] == "sorting"
    )
    assert sorting["stages"][0]["unaided_by_band"] == {}
    assert sorting["stages"][0]["with_hints"] == 1  # counted, as solved with help


def test_accept_submitted_before_giving_up_stays_unaided(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    problem = make_problem("df1b", hints=0)
    student, me = signed_in(Role.student)
    assert student.post(
        "/api/v1/problems/df1b/editorial/unlock", json={"reason": "gave_up"}
    ).is_success
    # Written before the give-up (judged only afterwards): the editorial was not at hand.
    _judged(db, me, problem, Verdict.accepted, ago=timedelta(hours=1))
    sorting = next(
        t for t in student.get("/api/v1/me/progress").json()["topics"] if t["slug"] == "sorting"
    )
    assert sorting["stages"][0]["unaided_by_band"] != {}
