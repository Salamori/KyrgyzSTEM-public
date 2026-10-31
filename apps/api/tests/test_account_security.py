"""Audit F-02, F-14, F-16, account recovery, consent, data export and account deletion."""

import json
from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.modules.audit.models import AuditLog
from app.modules.auth.models import AuthSession, PasswordResetToken
from app.modules.olympiad.models import EditorialUnlock, HintReveal, SelfReport, Submission
from app.modules.orgs.models import (
    AttendanceRecord,
    AttendanceStatus,
    Group,
    GroupKind,
    GroupMember,
    GroupSession,
    Organization,
    OrgKind,
    OrgMembership,
    OrgRole,
)
from app.modules.users.models import Role, User
from tests.conftest import PASSWORD, login
from tests.test_olympiad import MakeProblem, curriculum, make_problem  # noqa: F401

SignedIn = Callable[[Role], tuple[TestClient, User]]
Clients = Callable[[], TestClient]
MakeUser = Callable[..., User]

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
SESSION = "/api/v1/auth/session"
RESET = "/api/v1/auth/password/reset"
EXPORT = "/api/v1/users/me/export"
DELETE = "/api/v1/users/me/delete"
NEW_PASSWORD = "a brand new password"
WRONG = "wrong password!"


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "email": "aidai@example.com",
        "password": PASSWORD,
        "display_name": "Aidai",
        "accepted_terms": True,
    }
    body.update(overrides)
    return body


def _rotate_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    addresses = iter(f"203.0.113.{i}" for i in range(1, 200))
    monkeypatch.setattr("app.modules.auth.router.client_ip", lambda _: next(addresses))


def _issue(admin: TestClient, user: User) -> str:
    res = admin.post(f"/api/v1/admin/users/{user.id}/password-reset")
    assert res.status_code == 200
    return str(res.json()["token"])


def _reset(client: TestClient, token: str) -> int:
    return client.post(RESET, json={"token": token, "new_password": NEW_PASSWORD}).status_code


# ------------------------------------------------------------------ registration


def test_register_requires_accepting_the_terms(client: TestClient, db: Session) -> None:
    body = _body()
    del body["accepted_terms"]
    assert client.post(REGISTER, json=body).status_code == 422
    assert client.post(REGISTER, json=_body(accepted_terms=False)).status_code == 422
    assert db.scalars(select(User)).all() == []


def test_register_records_the_accepted_version(client: TestClient, db: Session) -> None:
    assert client.post(REGISTER, json=_body()).status_code == 201
    user = db.scalars(select(User)).one()
    assert user.terms_version == get_settings().legal_documents_version
    assert user.terms_accepted_at is not None


def test_repeated_registration_changes_nothing(client: TestClient, db: Session) -> None:
    assert client.post(REGISTER, json=_body()).status_code == 201
    client.cookies.clear()
    res = client.post(REGISTER, json=_body(password="another long password"))
    assert res.status_code == 409
    assert "ks_session" not in client.cookies
    login(client, "aidai@example.com")  # the first password still works
    assert len(db.scalars(select(User)).all()) == 1


def test_one_email_cannot_be_probed_endlessly_through_registration(client: TestClient) -> None:
    assert client.post(REGISTER, json=_body()).status_code == 201
    client.cookies.clear()
    codes = [client.post(REGISTER, json=_body()).status_code for _ in range(5)]
    assert codes == [409, 409, 409, 409, 429]


# ------------------------------------------------------------------ login limits


def test_login_is_limited_per_account_across_addresses(
    client: TestClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user()
    _rotate_addresses(monkeypatch)
    for _ in range(30):
        res = client.post(LOGIN, json={"email": user.email, "password": WRONG})
        assert res.status_code == 401
    assert client.post(LOGIN, json={"email": user.email, "password": PASSWORD}).status_code == 429


def test_account_limit_answers_the_same_for_unknown_emails(
    client: TestClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rotate_addresses(monkeypatch)
    answers = []
    for email in (make_user().email, "nobody@example.com"):
        for _ in range(30):
            client.post(LOGIN, json={"email": email, "password": WRONG})
        res = client.post(LOGIN, json={"email": email, "password": WRONG})
        answers.append((res.status_code, res.json()))
    assert answers[0] == answers[1]
    assert answers[0][0] == 429


# ------------------------------------------------------------------ sessions


def test_idle_session_ends(client: TestClient, make_user: MakeUser, db: Session) -> None:
    login(client, make_user().email)
    session = db.scalars(select(AuthSession)).one()
    idle = timedelta(hours=get_settings().session_idle_hours)
    session.last_seen_at = utcnow() - idle + timedelta(minutes=1)
    db.commit()
    assert client.get(SESSION).status_code == 200
    session.last_seen_at = utcnow() - idle - timedelta(minutes=1)
    db.commit()
    assert client.get(SESSION).status_code == 401


def test_logout_all_ends_every_session_and_needs_csrf(
    make_client: Clients, make_user: MakeUser
) -> None:
    user = make_user()
    laptop = login(make_client(), user.email)
    phone = login(make_client(), user.email)
    csrf = laptop.headers.pop("X-CSRF-Token")
    assert laptop.post("/api/v1/auth/logout-all").status_code == 403
    laptop.headers["X-CSRF-Token"] = csrf
    assert laptop.post("/api/v1/auth/logout-all").status_code == 204
    assert laptop.get(SESSION).status_code == 401
    assert phone.get(SESSION).status_code == 401


def test_logout_all_needs_a_session(client: TestClient) -> None:
    assert client.post("/api/v1/auth/logout-all").status_code == 401


# ------------------------------------------------------------------ password recovery


def test_password_reset_works_once_and_ends_sessions(
    make_client: Clients, signed_in: SignedIn, make_user: MakeUser, db: Session
) -> None:
    admin, admin_user = signed_in(Role.admin)
    user = make_user()
    existing = login(make_client(), user.email)
    token = _issue(admin, user)
    assert token not in db.scalars(select(PasswordResetToken.token_hash)).all()

    public = make_client()
    assert _reset(public, token) == 204
    assert existing.get(SESSION).status_code == 401
    assert public.post(LOGIN, json={"email": user.email, "password": PASSWORD}).status_code == 401
    login(public, user.email, NEW_PASSWORD)

    again = make_client().post(RESET, json={"token": token, "new_password": "yet another pass"})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_reset_token"

    issued = db.scalars(select(AuditLog).where(AuditLog.action == "user.password_reset_issued"))
    assert issued.one().actor_id == admin_user.id
    done = db.scalars(select(AuditLog).where(AuditLog.action == "user.password_reset"))
    assert done.one().actor_id == user.id
    assert token not in str(db.scalars(select(AuditLog.details)).all())


def test_password_reset_failures_look_the_same(
    make_client: Clients, signed_in: SignedIn, make_user: MakeUser, db: Session
) -> None:
    admin, _ = signed_in(Role.admin)
    client = make_client()
    expired_token = _issue(admin, make_user())
    db.scalars(select(PasswordResetToken)).one().expires_at = utcnow() - timedelta(seconds=1)
    disabled = make_user()
    disabled_token = _issue(admin, disabled)
    disabled.is_active = False
    db.commit()

    answers = [
        client.post(RESET, json={"token": t, "new_password": NEW_PASSWORD})
        for t in (expired_token, disabled_token, "made-up-token")
    ]
    assert {r.status_code for r in answers} == {400}
    assert answers[0].json() == answers[1].json() == answers[2].json()


def test_a_new_reset_link_cancels_the_previous_one(
    signed_in: SignedIn, make_user: MakeUser, client: TestClient
) -> None:
    admin, _ = signed_in(Role.admin)
    user = make_user()
    first = _issue(admin, user)
    second = _issue(admin, user)
    assert _reset(client, first) == 400
    assert _reset(client, second) == 204


def test_password_reset_is_rate_limited_and_checks_the_password(client: TestClient) -> None:
    assert client.post(RESET, json={"token": "x", "new_password": "short"}).status_code == 422
    for _ in range(10):
        assert _reset(client, "x") == 400
    assert _reset(client, "x") == 429


def test_issuing_a_reset_link_is_admin_only(
    client: TestClient, signed_in: SignedIn, make_user: MakeUser
) -> None:
    url = f"/api/v1/admin/users/{make_user().id}/password-reset"
    assert client.post(url).status_code == 401
    assert signed_in(Role.student)[0].post(url).status_code == 403
    assert signed_in(Role.teacher)[0].post(url).status_code == 403


def test_admins_cannot_reset_themselves_other_admins_or_disabled_accounts(
    signed_in: SignedIn, make_user: MakeUser
) -> None:
    admin, me = signed_in(Role.admin)
    other_admin = make_user(role=Role.admin)
    disabled = make_user(is_active=False)
    assert admin.post(f"/api/v1/admin/users/{me.id}/password-reset").status_code == 400
    assert admin.post(f"/api/v1/admin/users/{other_admin.id}/password-reset").status_code == 403
    assert admin.post(f"/api/v1/admin/users/{disabled.id}/password-reset").status_code == 409


# ------------------------------------------------------------------ data export


def test_export_requires_a_session(client: TestClient) -> None:
    assert client.get(EXPORT).status_code == 401


def test_export_holds_own_data_and_no_secrets(signed_in: SignedIn, db: Session) -> None:
    client, user = signed_in(Role.student)
    other, other_user = signed_in(Role.student)
    res = client.get(EXPORT)
    assert res.status_code == 200
    data = res.json()
    assert data["profile"]["email"] == user.email
    assert len(data["sessions"]) == 1
    text = json.dumps(data)
    assert other_user.email not in text
    assert user.password_hash not in text
    assert "password_hash" not in text and "token" not in text
    stored = db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)).one()
    assert stored.token_hash not in text and stored.csrf_token_hash not in text
    assert other.get(EXPORT).json()["profile"]["email"] == other_user.email


def _practice(client: TestClient, make_problem: MakeProblem) -> None:
    """A published problem with a submission, a revealed hint, an unlock and a self-report."""
    slug = make_problem("export-demo").slug
    res = client.post(
        "/api/v1/submissions",
        json={"problem_slug": slug, "language": "python", "source_code": "print('mine')"},
    )
    assert res.status_code == 201
    assert client.post(f"/api/v1/problems/{slug}/hints/1/reveal").status_code == 200
    unlock = client.post(f"/api/v1/problems/{slug}/editorial/unlock", json={"reason": "gave_up"})
    assert unlock.status_code == 200
    assert client.post(f"/api/v1/problems/{slug}/self-report").status_code in {200, 201}


def _member_of_group(db: Session, user: User) -> None:
    org = Organization(name="[Synthetic] School", kind=OrgKind.school, is_synthetic=True)
    db.add(org)
    db.flush()
    group = Group(org_id=org.id, name="[Synthetic] Club", kind=GroupKind.club)
    db.add(group)
    db.flush()
    session = GroupSession(group_id=group.id, starts_at=utcnow(), title="[Synthetic] Week 1")
    db.add(session)
    db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.student))
    db.add(GroupMember(group_id=group.id, user_id=user.id))
    db.add(
        AttendanceRecord(session_id=session.id, user_id=user.id, status=AttendanceStatus.present)
    )
    db.commit()


def test_export_includes_practice_history_and_group_records(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    client, user = signed_in(Role.student)
    _practice(client, make_problem)
    _member_of_group(db, user)

    data = client.get(EXPORT).json()
    assert [s["source_code"] for s in data["submissions"]] == ["print('mine')"]
    assert data["submissions"][0]["problem_slug"] == "export-demo"
    assert [h["ordinal"] for h in data["hint_reveals"]] == [1]
    assert [u["reason"] for u in data["editorial_unlocks"]] == ["gave_up"]
    assert len(data["self_reports"]) == 1
    assert [m["role"] for m in data["memberships"]] == ["student"]
    assert [g["group"] for g in data["groups"]] == ["[Synthetic] Club"]
    assert [a["status"] for a in data["attendance"]] == ["present"]


def test_delete_removes_practice_history_and_group_records(
    signed_in: SignedIn, make_problem: MakeProblem, db: Session
) -> None:
    client, user = signed_in(Role.student)
    user_id = user.id
    _practice(client, make_problem)
    _member_of_group(db, user)
    assert client.post(DELETE, json={"password": PASSWORD}).status_code == 204

    db.expire_all()
    for model in (
        Submission,
        HintReveal,
        EditorialUnlock,
        SelfReport,
        OrgMembership,
        GroupMember,
        AttendanceRecord,
    ):
        rows = db.scalars(select(model).where(model.user_id == user_id)).all()
        assert rows == [], model.__name__


def test_export_is_rate_limited(signed_in: SignedIn) -> None:
    client, _ = signed_in(Role.student)
    assert [client.get(EXPORT).status_code for _ in range(6)] == [200] * 5 + [429]


# ------------------------------------------------------------------ account deletion


def test_delete_requires_a_session_and_csrf(client: TestClient, signed_in: SignedIn) -> None:
    assert client.post(DELETE, json={"password": PASSWORD}).status_code == 401
    student, _ = signed_in(Role.student)
    csrf = student.headers.pop("X-CSRF-Token")
    assert student.post(DELETE, json={"password": PASSWORD}).status_code == 403
    student.headers["X-CSRF-Token"] = csrf


def test_delete_needs_the_password(signed_in: SignedIn, db: Session) -> None:
    client, user = signed_in(Role.student)
    res = client.post(DELETE, json={"password": WRONG})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_credentials"
    assert db.get(User, user.id) is not None


def test_delete_removes_the_account_and_its_sessions(
    signed_in: SignedIn, make_client: Clients, db: Session
) -> None:
    client, user = signed_in(Role.student)
    user_id, email = user.id, user.email
    stolen = client.cookies["ks_session"]
    assert client.post(DELETE, json={"password": PASSWORD}).status_code == 204

    db.expire_all()
    assert db.get(User, user_id) is None
    assert db.scalars(select(AuthSession).where(AuthSession.user_id == user_id)).all() == []
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.deleted")).one()
    assert entry.actor_id is None and entry.target_id == str(user_id)
    assert email not in str(entry.details)

    replay = make_client()
    replay.cookies.set("ks_session", stolen)
    assert replay.get(SESSION).status_code == 401
    assert replay.post(LOGIN, json={"email": email, "password": PASSWORD}).status_code == 401


def test_platform_admin_cannot_delete_themself(signed_in: SignedIn, db: Session) -> None:
    client, user = signed_in(Role.admin)
    res = client.post(DELETE, json={"password": PASSWORD})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "admin_cannot_delete_self"
    assert db.get(User, user.id) is not None


def test_last_org_admin_cannot_delete_themself(signed_in: SignedIn, db: Session) -> None:
    client, user = signed_in(Role.teacher)
    org = Organization(name="[Synthetic] School", kind=OrgKind.school, is_synthetic=True)
    db.add(org)
    db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.org_admin))
    db.commit()
    res = client.post(DELETE, json={"password": PASSWORD})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "last_org_admin"


def test_delete_is_rate_limited(signed_in: SignedIn) -> None:
    client, _ = signed_in(Role.student)
    for _ in range(5):
        assert client.post(DELETE, json={"password": WRONG}).status_code == 401
    assert client.post(DELETE, json={"password": PASSWORD}).status_code == 429
