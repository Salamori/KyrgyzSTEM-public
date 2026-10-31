"""Test harness: real PostgreSQL, schema built by Alembic, one rolled-back transaction per test.

Requires TEST_DATABASE_URL pointing at a disposable database (it is wiped on startup).
"""

import os
from collections.abc import Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if not TEST_DATABASE_URL:
    raise pytest.UsageError(
        "Set TEST_DATABASE_URL to a disposable PostgreSQL database, e.g. "
        "postgresql+psycopg://postgres@localhost:5432/kyrgyzstem_test"
    )
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["APP_ENV"] = "test"

from app.core.config import get_settings  # noqa: E402
from app.core.db import get_db  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.users.models import Role, User  # noqa: E402

PASSWORD = "correct horse battery"
API_DIR = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    get_settings.cache_clear()
    eng = create_engine(TEST_DATABASE_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "alembic"))
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    trans = conn.begin()
    yield conn
    trans.rollback()
    conn.close()


@pytest.fixture
def db(connection: Connection) -> Iterator[Session]:
    # Route-level commit() only releases a savepoint; the outer transaction is rolled back.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    limiter.reset()
    settings = get_settings()
    original = settings.model_copy()
    yield
    for field in type(settings).model_fields:
        setattr(settings, field, getattr(original, field))


@pytest.fixture
def make_client(db: Session) -> Iterator[Callable[[], TestClient]]:
    app = create_app()

    def _get_db() -> Iterator[Session]:
        db.expire_all()  # each request sees fresh rows, like a new session would
        yield db

    app.dependency_overrides[get_db] = _get_db
    clients: list[TestClient] = []

    def factory() -> TestClient:
        client = TestClient(app)
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.close()


@pytest.fixture
def client(make_client: Callable[[], TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    counter = iter(range(10_000))

    def factory(
        role: Role = Role.student,
        email: str | None = None,
        is_active: bool = True,
        display_name: str = "Test User",
    ) -> User:
        user = User(
            email=email or f"user{next(counter)}-{role.value}@example.com",
            password_hash=hash_password(PASSWORD),
            display_name=display_name,
            role=role,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        return user

    return factory


def login(client: TestClient, email: str, password: str = PASSWORD) -> TestClient:
    """Sign in and configure the client to send the CSRF header like the web app does."""
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    client.headers["X-CSRF-Token"] = res.json()["csrf_token"]
    return client


@pytest.fixture
def signed_in(
    make_client: Callable[[], TestClient], make_user: Callable[..., User]
) -> Callable[[Role], tuple[TestClient, User]]:
    def factory(role: Role = Role.student) -> tuple[TestClient, User]:
        user = make_user(role=role)
        return login(make_client(), user.email), user

    return factory
