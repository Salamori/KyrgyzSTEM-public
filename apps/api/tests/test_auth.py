from collections.abc import Callable
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.modules.audit.models import AuditLog
from app.modules.auth.models import AuthSession
from app.modules.users.models import Role, User
from tests.conftest import PASSWORD, login

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
SESSION = "/api/v1/auth/session"


def _register_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "email": "Aidai@Example.com",
        "password": PASSWORD,
        "display_name": "  Aidai  ",
        "accepted_terms": True,
    }
    body.update(overrides)
    return body


def test_register_creates_signed_in_student(client: TestClient, db: Session) -> None:
    res = client.post(REGISTER, json=_register_body())
    assert res.status_code == 201
    data = res.json()
    assert data["user"]["role"] == "student"
    assert data["user"]["email"] == "aidai@example.com"
    assert data["user"]["display_name"] == "Aidai"
    assert data["user"]["preferred_locale"] == "ru"
    assert "password_hash" not in data["user"]
    assert data["csrf_token"]

    cookies = res.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("ks_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("ks_csrf="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" not in session_cookie  # disabled in local/test config

    # The raw token is never stored.
    token = client.cookies["ks_session"]
    stored = db.scalars(select(AuthSession.token_hash)).all()
    assert token not in stored and len(stored) == 1

    assert client.get(SESSION).status_code == 200


def test_session_cookie_is_secure_when_configured(client: TestClient) -> None:
    get_settings().session_cookie_secure = True
    res = client.post(REGISTER, json=_register_body())
    session_cookie = next(
        c for c in res.headers.get_list("set-cookie") if c.startswith("ks_session=")
    )
    assert "Secure" in session_cookie


def test_register_cannot_choose_role(client: TestClient) -> None:
    res = client.post(REGISTER, json=_register_body(role="admin"))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


def test_register_duplicate_email_is_case_insensitive(client: TestClient) -> None:
    assert client.post(REGISTER, json=_register_body()).status_code == 201
    res = client.post(REGISTER, json=_register_body(email="AIDAI@example.COM"))
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "email_taken"


def test_register_validates_input_without_echoing_password(client: TestClient) -> None:
    secret = "short-pw"
    res = client.post(REGISTER, json=_register_body(password=secret))
    assert res.status_code == 422
    assert secret not in res.text
    assert client.post(REGISTER, json=_register_body(display_name="   ")).status_code == 422
    assert client.post(REGISTER, json=_register_body(email="not-an-email")).status_code == 422
    assert client.post(REGISTER, json=_register_body(password="x" * 129)).status_code == 422
    assert client.post(REGISTER, json=_register_body(preferred_locale="de")).status_code == 422


def test_login_failures_are_indistinguishable(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    user = make_user()
    wrong_pw = client.post(LOGIN, json={"email": user.email, "password": "wrong password!"})
    unknown = client.post(LOGIN, json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()
    assert "ks_session" not in client.cookies


def test_login_is_case_insensitive_on_email(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    user = make_user(email="mixed@example.com")
    res = client.post(LOGIN, json={"email": "MIXED@example.com", "password": PASSWORD})
    assert res.status_code == 200
    assert res.json()["user"]["id"] == str(user.id)


def test_disabled_account_cannot_login(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user(is_active=False)
    res = client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "account_disabled"


def test_anonymous_session_is_401(client: TestClient) -> None:
    res = client.get(SESSION)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "not_authenticated"


def test_garbage_cookie_is_401(client: TestClient) -> None:
    client.cookies.set("ks_session", "forged-token")
    assert client.get(SESSION).status_code == 401


def test_logout_revokes_session_server_side(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    user = make_user()
    login(client, user.email)
    stolen = client.cookies["ks_session"]

    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.set("ks_session", stolen)  # replay the old cookie
    assert client.get(SESSION).status_code == 401


def test_expired_session_is_rejected(
    client: TestClient, make_user: Callable[..., User], db: Session
) -> None:
    login(client, make_user().email)
    session = db.scalars(select(AuthSession)).one()
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get(SESSION).status_code == 401


def test_unsafe_requests_require_csrf_token(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    login(client, make_user().email)
    good = client.headers.pop("X-CSRF-Token")

    missing = client.patch("/api/v1/users/me", json={"display_name": "X"})
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"

    wrong = client.patch(
        "/api/v1/users/me", json={"display_name": "X"}, headers={"X-CSRF-Token": "nope"}
    )
    assert wrong.status_code == 403
    assert client.post("/api/v1/auth/logout").status_code == 403

    ok = client.patch(
        "/api/v1/users/me", json={"display_name": "X"}, headers={"X-CSRF-Token": good}
    )
    assert ok.status_code == 200


def test_session_endpoint_keeps_valid_csrf_and_reissues_lost_one(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    login(client, make_user().email)
    original = client.headers["X-CSRF-Token"]
    assert client.get(SESSION).json()["csrf_token"] == original

    client.cookies.delete("ks_csrf")
    reissued = client.get(SESSION).json()["csrf_token"]
    assert reissued != original
    assert client.cookies["ks_csrf"] == reissued


def test_login_is_rate_limited(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user()
    for _ in range(10):
        res = client.post(LOGIN, json={"email": user.email, "password": "wrong password!"})
        assert res.status_code == 401
    res = client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "rate_limited"
    assert int(res.headers["Retry-After"]) > 0


def test_register_is_rate_limited(client: TestClient) -> None:
    for i in range(10):
        res = client.post(REGISTER, json=_register_body(email=f"r{i}@example.com"))
        assert res.status_code == 201
    assert client.post(REGISTER, json=_register_body(email="r10@example.com")).status_code == 429


def test_change_password_revokes_other_sessions_and_is_audited(
    make_client: Callable[[], TestClient], make_user: Callable[..., User], db: Session
) -> None:
    user = make_user()
    laptop = login(make_client(), user.email)
    phone = login(make_client(), user.email)
    before = laptop.cookies["ks_session"]

    wrong = laptop.post(
        "/api/v1/auth/password",
        json={"current_password": "not it at all", "new_password": "a brand new password"},
    )
    assert wrong.status_code == 401

    res = laptop.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new password"},
    )
    assert res.status_code == 204
    assert laptop.get(SESSION).status_code == 200
    assert laptop.cookies["ks_session"] != before  # the session is replaced, not kept
    assert phone.get(SESSION).status_code == 401
    replay = make_client()
    replay.cookies.set("ks_session", before)
    assert replay.get(SESSION).status_code == 401
    laptop.headers["X-CSRF-Token"] = laptop.cookies["ks_csrf"]
    assert laptop.post("/api/v1/auth/logout").status_code == 204  # the new CSRF token works

    fresh = make_client()
    old = fresh.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert old.status_code == 401
    login(fresh, user.email, "a brand new password")

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.password_changed")).one()
    assert entry.actor_id == user.id


def test_admin_role_cannot_be_obtained_by_registration(client: TestClient, db: Session) -> None:
    client.post(REGISTER, json=_register_body())
    assert db.scalars(select(User.role)).one() == Role.student
