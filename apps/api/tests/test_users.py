from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.users.models import Role, User

SignedIn = Callable[[Role], tuple[TestClient, User]]
ME = "/api/v1/users/me"
ADMIN_USERS = "/api/v1/admin/users"
AUDIT = "/api/v1/admin/audit-logs"


def test_me_requires_auth(client: TestClient) -> None:
    assert client.get(ME).status_code == 401
    assert client.patch(ME, json={"display_name": "X"}).status_code == 401


def test_update_me(signed_in: SignedIn) -> None:
    client, _ = signed_in(Role.student)
    res = client.patch(ME, json={"display_name": "  Bekzat ", "preferred_locale": "ky"})
    assert res.status_code == 200
    assert res.json()["display_name"] == "Bekzat"
    assert res.json()["preferred_locale"] == "ky"
    assert client.get(ME).json()["preferred_locale"] == "ky"


@pytest.mark.parametrize(
    "body",
    [
        {"role": "admin"},
        {"email": "new@example.com"},
        {"is_active": False},
        {"display_name": ""},
        {"display_name": "x" * 81},
        {"preferred_locale": "fr"},
    ],
)
def test_update_me_rejects_forbidden_or_invalid_fields(
    signed_in: SignedIn, body: dict[str, object]
) -> None:
    client, _ = signed_in(Role.student)
    res = client.patch(ME, json=body)
    assert res.status_code == 422
    assert client.get(ME).json()["role"] == "student"


@pytest.mark.parametrize("role", [Role.student, Role.teacher])
def test_admin_endpoints_forbidden_for_non_admins(signed_in: SignedIn, role: Role) -> None:
    client, user = signed_in(role)
    assert client.get(ADMIN_USERS).status_code == 403
    assert client.get(AUDIT).status_code == 403
    res = client.patch(f"{ADMIN_USERS}/{user.id}", json={"role": "admin"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"


def test_admin_endpoints_require_auth(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user()
    assert client.get(ADMIN_USERS).status_code == 401
    assert client.get(AUDIT).status_code == 401
    assert client.patch(f"{ADMIN_USERS}/{user.id}", json={"role": "admin"}).status_code == 401


def test_admin_lists_and_filters_users(signed_in: SignedIn, make_user: Callable[..., User]) -> None:
    admin, _ = signed_in(Role.admin)
    make_user(role=Role.teacher, email="nurlan@example.com", display_name="Nurlan")
    make_user(role=Role.student, email="a_b@example.com", display_name="Aida")
    make_user(role=Role.student, email="axb@example.com", display_name="Other")

    page = admin.get(ADMIN_USERS, params={"limit": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2 and page["limit"] == 2
    assert "password_hash" not in page["items"][0]

    teachers = admin.get(ADMIN_USERS, params={"role": "teacher"}).json()
    assert [u["email"] for u in teachers["items"]] == ["nurlan@example.com"]

    by_name = admin.get(ADMIN_USERS, params={"q": "NURL"}).json()
    assert by_name["total"] == 1

    # LIKE wildcards in the query are matched literally.
    underscore = admin.get(ADMIN_USERS, params={"q": "a_b"}).json()
    assert [u["email"] for u in underscore["items"]] == ["a_b@example.com"]
    assert admin.get(ADMIN_USERS, params={"q": "%"}).json()["total"] == 0

    assert admin.get(ADMIN_USERS, params={"limit": 101}).status_code == 422
    assert admin.get(ADMIN_USERS, params={"offset": -1}).status_code == 422


def test_admin_changes_role_with_audit(
    signed_in: SignedIn, make_user: Callable[..., User], db: Session
) -> None:
    admin, admin_user = signed_in(Role.admin)
    target = make_user()
    res = admin.patch(f"{ADMIN_USERS}/{target.id}", json={"role": "teacher"})
    assert res.status_code == 200
    assert res.json()["role"] == "teacher"

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.role_changed")).one()
    assert entry.actor_id == admin_user.id
    assert entry.target_id == str(target.id)
    assert entry.details == {"from": "student", "to": "teacher"}

    logs = admin.get(AUDIT).json()
    assert logs["items"][0]["action"] == "user.role_changed"
    assert "ip" not in logs["items"][0]


def test_noop_update_writes_no_audit(
    signed_in: SignedIn, make_user: Callable[..., User], db: Session
) -> None:
    admin, _ = signed_in(Role.admin)
    target = make_user()
    admin.patch(f"{ADMIN_USERS}/{target.id}", json={"role": "student", "is_active": True})
    assert db.scalars(select(AuditLog)).all() == []


def test_admin_cannot_demote_or_disable_self(signed_in: SignedIn) -> None:
    admin, me = signed_in(Role.admin)
    for body in ({"role": "student"}, {"is_active": False}):
        res = admin.patch(f"{ADMIN_USERS}/{me.id}", json=body)
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "cannot_modify_self"


def test_admin_update_unknown_user_404(signed_in: SignedIn) -> None:
    admin, _ = signed_in(Role.admin)
    res = admin.patch(f"{ADMIN_USERS}/00000000-0000-0000-0000-000000000000", json={"role": "admin"})
    assert res.status_code == 404
    assert admin.patch(f"{ADMIN_USERS}/not-a-uuid", json={"role": "admin"}).status_code == 422


def test_deactivation_kills_sessions_immediately_and_is_audited(
    signed_in: SignedIn, db: Session
) -> None:
    admin, _ = signed_in(Role.admin)
    student, student_user = signed_in(Role.student)
    assert student.get(ME).status_code == 200

    res = admin.patch(f"{ADMIN_USERS}/{student_user.id}", json={"is_active": False})
    assert res.status_code == 200 and res.json()["is_active"] is False
    assert student.get(ME).status_code == 401

    admin.patch(f"{ADMIN_USERS}/{student_user.id}", json={"is_active": True})
    actions = [a for a in db.scalars(select(AuditLog.action).order_by(AuditLog.created_at))]
    assert actions == ["user.deactivated", "user.reactivated"]
    # Reactivation does not resurrect revoked sessions.
    assert student.get(ME).status_code == 401


def test_audit_log_filter(signed_in: SignedIn, make_user: Callable[..., User]) -> None:
    admin, _ = signed_in(Role.admin)
    target = make_user()
    admin.patch(f"{ADMIN_USERS}/{target.id}", json={"role": "teacher"})
    admin.patch(f"{ADMIN_USERS}/{target.id}", json={"is_active": False})
    res = admin.get(AUDIT, params={"action": "user.deactivated"}).json()
    assert res["total"] == 1
