"""Operations: readiness, behaviour when the database is down, structured logging."""

import json
import logging
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logs import JsonFormatter, configure_logging, request_id_var
from app.main import _SECURITY_HEADERS, create_app
from app.modules.users.models import Role, User
from tests.conftest import login


@pytest.fixture
def broken_db_client() -> Iterator[TestClient]:
    """An app whose database is unreachable (nothing listens on port 1)."""
    engine = create_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
        connect_args={"connect_timeout": 1},
        hide_parameters=True,
    )
    app = create_app()

    def _get_db() -> Iterator[Session]:
        session = Session(bind=engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_ready_when_database_answers(client: TestClient) -> None:
    res = client.get("/api/v1/health/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok"}


def test_liveness_and_readiness_when_database_is_down(broken_db_client: TestClient) -> None:
    live = broken_db_client.get("/api/v1/health")
    assert live.status_code == 200
    assert live.json() == {"status": "ok", "database": "unavailable"}

    ready = broken_db_client.get("/api/v1/health/ready")
    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "service_unavailable"
    assert ready.headers["Retry-After"] == "30"


def test_regular_routes_return_503_not_500_when_database_is_down(
    broken_db_client: TestClient,
) -> None:
    res = broken_db_client.get("/api/v1/problems")
    assert res.status_code == 503
    body = res.json()["error"]
    assert body["code"] == "service_unavailable"
    assert "127.0.0.1" not in json.dumps(body)

    login_res = broken_db_client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "x" * 12}
    )
    assert login_res.status_code == 503


def _formatted(caplog: pytest.LogCaptureFixture) -> str:
    formatter = JsonFormatter()
    return "\n".join(formatter.format(r) for r in caplog.records)


def test_request_log_uses_route_template_not_path_or_query(
    signed_in: Callable[[Role], tuple[TestClient, User]],
    make_user: Callable[..., User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    admin, _ = signed_in(Role.admin)
    student = make_user(email="private-student@example.com")
    caplog.set_level(logging.INFO, logger="kyrgyzstem")

    res = admin.get("/api/v1/admin/users", params={"q": student.email})
    assert res.status_code == 200

    records = [r for r in caplog.records if r.name == "kyrgyzstem.request"]
    assert records, "request was not logged"
    fields = records[-1].fields  # type: ignore[attr-defined]
    assert fields["route"] == "/api/v1/admin/users"
    assert fields["method"] == "GET"
    assert fields["status"] == 200
    assert set(fields) == {"method", "route", "status", "duration_ms"}

    output = _formatted(caplog)
    assert student.email not in output
    assert "testclient" not in output  # the client address
    assert str(student.id) not in output


def test_request_log_never_contains_passwords_or_tokens(
    make_client: Callable[[], TestClient],
    make_user: Callable[..., User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = make_user()
    caplog.set_level(logging.DEBUG)
    client = login(make_client(), user.email)
    client.post("/api/v1/auth/login", json={"email": user.email, "password": "wrong-password!"})
    client.get("/api/v1/users/me")

    output = _formatted(caplog)
    for secret in (
        "correct horse battery",
        "wrong-password!",
        client.cookies.get("ks_session"),
        client.headers["X-CSRF-Token"],
        user.email,
    ):
        assert secret
        assert secret not in output


def test_request_id_is_echoed_only_when_safe(client: TestClient) -> None:
    assert (
        client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"]
        == "abc-123"
    )
    injected = client.get("/api/v1/health", headers={"X-Request-ID": "x\n{evil} " + "a" * 80})
    generated = injected.headers["X-Request-ID"]
    assert len(generated) == 32 and generated.isalnum()


def test_unhandled_errors_return_the_envelope_and_log_a_traceback(
    make_client: Callable[[], TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client()

    def boom() -> None:
        raise RuntimeError("unexpected")

    client.app.add_api_route("/api/v1/_boom", boom)  # type: ignore[attr-defined]
    caplog.set_level(logging.INFO, logger="kyrgyzstem")
    res = client.get("/api/v1/_boom?email=hidden@example.com")

    assert res.status_code == 500
    error = res.json()["error"]
    assert error["code"] == "internal_error"
    assert error["details"]["request_id"] == res.headers["X-Request-ID"]
    assert "unexpected" not in res.text
    # Generated 500s get the same security headers as every other API response.
    for name, value in _SECURITY_HEADERS.items():
        assert res.headers.get(name) == value, name
    failure = next(r for r in caplog.records if r.getMessage() == "request.unhandled_error")
    assert failure.exc_info is not None
    assert "hidden@example.com" not in _formatted(caplog)


def test_json_formatter_shape() -> None:
    record = logging.LogRecord("kyrgyzstem.test", logging.INFO, __file__, 1, "evt", None, None)
    record.fields = {"status": 200}
    token = request_id_var.set("rid-1")
    try:
        entry = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert entry["event"] == "evt"
    assert entry["level"] == "info"
    assert entry["request_id"] == "rid-1"
    assert entry["status"] == 200


def test_configure_logging_disables_uvicorn_access_log() -> None:
    access = logging.getLogger("uvicorn.access")
    root = logging.getLogger()
    before_handlers, before_level = list(root.handlers), root.level
    try:
        configure_logging("INFO", "json")
        configure_logging("INFO", "json")  # idempotent: one handler, not two
        assert access.disabled
        ours = [h for h in root.handlers if h not in before_handlers]
        assert len(ours) == 1
        assert isinstance(ours[0].formatter, JsonFormatter)
    finally:
        for handler in [h for h in root.handlers if h not in before_handlers]:
            root.removeHandler(handler)
        root.setLevel(before_level)
        access.disabled = False


def test_promote_admin_cli_is_audited_and_needs_an_existing_active_account(
    db: Session, make_user: Callable[..., User]
) -> None:
    from sqlalchemy import select

    from app.modules.audit.models import AuditLog
    from scripts.promote_admin import promote

    user = make_user(email="first-admin@example.com")
    assert promote(db, "First-Admin@example.com ") == "promoted"
    db.refresh(user)
    assert user.role == Role.admin
    entry = db.scalar(select(AuditLog).where(AuditLog.target_id == str(user.id)))
    assert entry is not None
    assert entry.action == "user.role_changed"
    assert entry.actor_id is None
    assert entry.details == {"from": "student", "to": "admin", "via": "cli"}

    assert promote(db, "first-admin@example.com") == "unchanged"
    assert promote(db, "nobody@example.com") == "not_found"
    inactive = make_user(is_active=False)
    assert promote(db, inactive.email) == "inactive"
    db.refresh(inactive)
    assert inactive.role == Role.student
