"""Running on Vercel Functions: client address from Vercel's edge, limits shared in PostgreSQL.

docs/DEPLOYMENT.md "Vercel-only deployment", docs/SECURITY.md (audit F-01, rate limiting).
"""

import hashlib
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import _sessionmaker, create_database_engine, get_engine
from app.core.errors import ApiError
from app.core.rate_limit import (
    PostgresRateLimiter,
    RateLimitHit,
    _key_digest,
    get_limiter_engine,
    limiter,
    purge_expired_hits,
)
from app.core.security import hash_password
from app.main import create_app
from app.modules.users.models import Role, User

LOGIN = "/api/v1/auth/login"
SECRET = "test-limiter-secret-0123456789-abcdef"
OTHER_SECRET = "another-limiter-secret-0123456789-xyz"
CLIENT_A = "203.0.113.7"
CLIENT_B = "198.51.100.23"


def _login(client: TestClient, email: str, headers: object = None) -> int:
    body = {"email": email, "password": "wrong password!"}
    return client.post(LOGIN, json=body, headers=headers).status_code  # type: ignore[arg-type]


@pytest.fixture
def hits_table(engine: Engine) -> Iterator[Engine]:
    """The limiter commits in its own transactions, outside the test's rolled-back one."""
    with engine.begin() as conn:
        conn.execute(delete(RateLimitHit))
    yield engine
    with engine.begin() as conn:
        conn.execute(delete(RateLimitHit))


@pytest.fixture
def pg_limiter(hits_table: Engine) -> PostgresRateLimiter:
    get_settings().rate_limit_key_secret = SecretStr(SECRET)
    return PostgresRateLimiter(lambda: hits_table)


@pytest.fixture
def on_vercel() -> None:
    settings = get_settings()
    settings.vercel = "1"
    settings.client_ip_source = "vercel"


def _rows(engine: Engine) -> list[tuple[str, datetime, datetime]]:
    with engine.connect() as conn:
        result = conn.execute(
            select(RateLimitHit.key_hash, RateLimitHit.hit_at, RateLimitHit.expires_at)
        )
        return [(row.key_hash, row.hit_at, row.expires_at) for row in result]


# ------------------------------------------------------------------ configuration guards


def test_vercel_client_ip_is_refused_off_vercel() -> None:
    with pytest.raises(ValueError, match="only on Vercel"):
        Settings(client_ip_source="vercel")


def test_on_vercel_the_client_ip_and_shared_limits_are_required() -> None:
    with pytest.raises(ValueError, match="CLIENT_IP_SOURCE=vercel"):
        Settings(vercel="1")
    with pytest.raises(ValueError, match="RATE_LIMIT_BACKEND=postgres"):
        Settings(vercel="1", client_ip_source="vercel")
    settings = Settings(
        vercel="1",
        client_ip_source="vercel",
        rate_limit_backend="postgres",
        rate_limit_key_secret=SECRET,
    )
    assert settings.client_ip_source == "vercel"


@pytest.mark.parametrize("secret", ["", "short-secret", "x" * 31, "*" * 40, "a b" * 20])
def test_postgres_limits_need_a_strong_key_secret(secret: str) -> None:
    with pytest.raises(ValueError, match="RATE_LIMIT_KEY_SECRET"):
        Settings(rate_limit_backend="postgres", rate_limit_key_secret=secret)


def test_secret_is_not_shown_in_settings_repr() -> None:
    settings = Settings(rate_limit_backend="postgres", rate_limit_key_secret=SECRET)
    assert SECRET not in repr(settings)


def test_pool_size_and_prepared_statements_are_configurable() -> None:
    settings = get_settings()
    settings.database_pool_size = 2
    settings.database_max_overflow = 1
    settings.database_prepared_statements = False
    engine = get_engine.__wrapped__()  # uncached: the shared engine keeps its settings
    try:
        assert engine.pool.size() == 2  # type: ignore[attr-defined]
        with engine.connect() as conn:
            assert conn.connection.driver_connection.prepare_threshold is None  # type: ignore[union-attr]
    finally:
        engine.dispose()


# ------------------------------------------------------------------ client address


def test_default_mode_ignores_x_forwarded_for(client: TestClient) -> None:
    # Without a trusted proxy (uvicorn --forwarded-allow-ips) the header changes nothing:
    # a client cannot leave its rate-limit identity by inventing addresses.
    for i in range(10):
        assert _login(client, "nobody@example.com", {"x-forwarded-for": f"203.0.113.{i}"}) == 401
    assert _login(client, "nobody@example.com", {"x-forwarded-for": "203.0.113.99"}) == 429


@pytest.mark.usefixtures("on_vercel")
def test_vercel_mode_limits_by_vercels_client_address(client: TestClient) -> None:
    for _ in range(10):
        assert _login(client, "nobody@example.com", {"x-forwarded-for": CLIENT_A}) == 401
    assert _login(client, "nobody@example.com", {"x-forwarded-for": CLIENT_A}) == 429
    # Another visitor is a separate identity.
    assert _login(client, "nobody@example.com", {"x-forwarded-for": CLIENT_B}) == 401


@pytest.mark.usefixtures("on_vercel")
def test_vercel_mode_accepts_ipv6(client: TestClient) -> None:
    assert _login(client, "nobody@example.com", {"x-forwarded-for": " 2001:DB8::1 "}) == 401


@pytest.mark.usefixtures("on_vercel")
@pytest.mark.parametrize(
    "headers",
    [
        None,
        {"x-forwarded-for": ""},
        {"x-forwarded-for": "not-an-ip"},
        {"x-forwarded-for": f"{CLIENT_A}, {CLIENT_B}"},
        [("x-forwarded-for", CLIENT_A), ("x-forwarded-for", CLIENT_B)],
    ],
)
def test_vercel_mode_refuses_requests_without_exactly_one_address(
    client: TestClient, headers: object, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="kyrgyzstem"):
        res = client.post(
            LOGIN,
            json={"email": "nobody@example.com", "password": "wrong password!"},
            headers=headers,  # type: ignore[arg-type]
        )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "service_unavailable"
    assert "client_ip.unavailable" in caplog.text
    assert CLIENT_A not in caplog.text and CLIENT_B not in caplog.text


# ------------------------------------------------------------------ PostgreSQL limiter


def test_postgres_limiter_allows_the_limit_then_refuses(pg_limiter: PostgresRateLimiter) -> None:
    for _ in range(3):
        pg_limiter.hit("login-ip:k1", limit=3, window_seconds=60)
    with pytest.raises(ApiError) as refused:
        pg_limiter.hit("login-ip:k1", limit=3, window_seconds=60)
    assert refused.value.status_code == 429
    assert refused.value.code == "rate_limited"
    assert refused.value.headers is not None
    assert 1 <= int(refused.value.headers["Retry-After"]) <= 60
    pg_limiter.hit("login-ip:k2", limit=3, window_seconds=60)  # other keys are unaffected


def test_refused_hits_are_not_counted(pg_limiter: PostgresRateLimiter, hits_table: Engine) -> None:
    for _ in range(2):
        pg_limiter.hit("k", limit=2, window_seconds=60)
    for _ in range(3):
        with pytest.raises(ApiError):
            pg_limiter.hit("k", limit=2, window_seconds=60)
    assert len(_rows(hits_table)) == 2


def test_postgres_limiter_stores_only_a_keyed_hash(
    pg_limiter: PostgresRateLimiter, hits_table: Engine
) -> None:
    key = f"login:{CLIENT_A}:alice@example.com"
    pg_limiter.hit(key, limit=5, window_seconds=60)
    [(stored, hit_at, expires_at)] = _rows(hits_table)
    assert "alice" not in stored and CLIENT_A not in stored
    assert stored == _key_digest(key).hex()
    assert stored != hashlib.sha256(key.encode()).hexdigest()  # not guessable without the key
    assert expires_at - hit_at == timedelta(seconds=60)
    get_settings().rate_limit_key_secret = SecretStr(OTHER_SECRET)
    assert _key_digest(key).hex() != stored


def test_hits_outside_the_window_no_longer_count(
    pg_limiter: PostgresRateLimiter, hits_table: Engine
) -> None:
    key_hash = _key_digest("k").hex()
    with hits_table.begin() as conn:
        now = conn.execute(select(func.clock_timestamp())).scalar_one()
        conn.execute(
            insert(RateLimitHit).values(
                key_hash=key_hash,
                hit_at=now - timedelta(seconds=61),
                expires_at=now - timedelta(seconds=1),
            )
        )
    pg_limiter.hit("k", limit=1, window_seconds=60)  # the old hit is outside the window
    with pytest.raises(ApiError):
        pg_limiter.hit("k", limit=1, window_seconds=60)


def test_instances_share_the_counts(hits_table: Engine, pg_limiter: PostgresRateLimiter) -> None:
    other_instance = PostgresRateLimiter(lambda: hits_table)
    pg_limiter.hit("shared", limit=2, window_seconds=60)
    other_instance.hit("shared", limit=2, window_seconds=60)
    with pytest.raises(ApiError):
        pg_limiter.hit("shared", limit=2, window_seconds=60)


def test_concurrent_hits_never_exceed_the_limit(pg_limiter: PostgresRateLimiter) -> None:
    start = threading.Barrier(12)

    def attempt(_: int) -> bool:
        start.wait()
        try:
            pg_limiter.hit("race", limit=5, window_seconds=60)
            return True
        except ApiError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        allowed = sum(pool.map(attempt, range(12)))
    assert allowed == 5


def test_disabled_limits_record_nothing(
    pg_limiter: PostgresRateLimiter, hits_table: Engine
) -> None:
    get_settings().rate_limit_enabled = False
    for _ in range(5):
        pg_limiter.hit("k", limit=1, window_seconds=60)
    assert _rows(hits_table) == []


def test_missing_secret_fails_closed(hits_table: Engine) -> None:
    get_settings().rate_limit_key_secret = SecretStr("")
    with pytest.raises(RuntimeError, match="RATE_LIMIT_KEY_SECRET"):
        PostgresRateLimiter(lambda: hits_table).hit("k", limit=1, window_seconds=60)
    assert _rows(hits_table) == []


def test_purge_deletes_only_expired_rows(hits_table: Engine) -> None:
    with hits_table.begin() as conn:
        now = conn.execute(select(func.clock_timestamp())).scalar_one()
        for age, ttl in ((120, 60), (30, 60)):
            hit_at = now - timedelta(seconds=age)
            conn.execute(
                insert(RateLimitHit).values(
                    key_hash="0" * 64, hit_at=hit_at, expires_at=hit_at + timedelta(seconds=ttl)
                )
            )
    with hits_table.begin() as conn:
        assert purge_expired_hits(conn) == 1
    assert len(_rows(hits_table)) == 1


def test_expiry_must_follow_the_hit(hits_table: Engine) -> None:
    with pytest.raises(SQLAlchemyError, match="ck_rate_limit_hits_expires_after_hit"):
        with hits_table.begin() as conn:
            now = conn.execute(select(func.clock_timestamp())).scalar_one()
            conn.execute(insert(RateLimitHit).values(key_hash="0" * 64, hit_at=now, expires_at=now))


# ------------------------------------------------------------------ through the API


@pytest.fixture
def postgres_backend(pg_limiter: PostgresRateLimiter) -> None:
    get_settings().rate_limit_backend = "postgres"


@pytest.mark.usefixtures("postgres_backend")
def test_login_limit_holds_across_api_instances(
    make_client: Callable[[], TestClient],
    make_user: Callable[..., User],
    hits_table: Engine,
) -> None:
    user = make_user()
    for _ in range(10):
        assert _login(make_client(), user.email) == 401
    limiter.reset()  # a fresh instance has no memory of its own; the counts are in PostgreSQL
    res = make_client().post(LOGIN, json={"email": user.email, "password": "correct horse battery"})
    assert res.status_code == 429
    assert int(res.headers["Retry-After"]) > 0
    stored = " ".join(row[0] for row in _rows(hits_table))
    assert user.email not in stored and "testclient" not in stored


def test_default_backend_writes_nothing_to_the_table(
    client: TestClient, hits_table: Engine
) -> None:
    assert _login(client, "nobody@example.com") == 401
    assert _rows(hits_table) == []


# ------------------------------------------------------------------ connection pools
#
# An authenticated request keeps its request connection while it is rate-limited. If the
# limiter drew from the same pool, N concurrent requests on a pool of N would each hold one
# connection and wait for a second: a stall until the pool timeout, then 503 for everyone.


def _hold_request_connections_and_hit(
    request_pool: Engine, rate_limiter: PostgresRateLimiter, requests: int
) -> None:
    """`requests` concurrent requests: each holds a request connection, then is limited."""
    all_holding = threading.Barrier(requests)

    def request(i: int) -> None:
        with request_pool.connect():  # like the session require_user has used
            all_holding.wait()
            rate_limiter.hit(f"user:{i}", limit=5, window_seconds=60)

    with ThreadPoolExecutor(max_workers=requests) as pool:
        list(pool.map(request, range(requests)))  # re-raises the first failure


def test_a_shared_pool_would_exhaust_and_the_limiter_pool_does_not(
    pg_limiter: PostgresRateLimiter, hits_table: Engine
) -> None:
    request_pool = create_database_engine(pool_size=3, max_overflow=0, pool_timeout=1)
    get_settings().rate_limit_pool_size = 1
    limiter_pool = get_limiter_engine.__wrapped__()  # the production factory, uncached
    try:
        # The hazard this guards against: limiter hits from the request pool time out.
        with pytest.raises(PoolTimeoutError):
            _hold_request_connections_and_hit(
                request_pool, PostgresRateLimiter(lambda: request_pool), 3
            )
        # (A request that timed out frees its connection, so a later hit may have counted.)
        with hits_table.begin() as conn:
            conn.execute(delete(RateLimitHit))
        # With its own pool, even a single connection serves every waiting request.
        _hold_request_connections_and_hit(
            request_pool, PostgresRateLimiter(lambda: limiter_pool), 3
        )
        assert len(_rows(hits_table)) == 3
    finally:
        request_pool.dispose()
        limiter_pool.dispose()


def test_limiter_pool_is_bounded_and_fails_closed(
    pg_limiter: PostgresRateLimiter, hits_table: Engine
) -> None:
    settings = get_settings()
    settings.rate_limit_pool_size = 1
    settings.rate_limit_pool_timeout_seconds = 0.5
    limiter_pool = get_limiter_engine.__wrapped__()
    try:
        assert limiter_pool.pool.size() == 1  # type: ignore[attr-defined]
        assert limiter_pool.pool._max_overflow == 0  # type: ignore[attr-defined]
        with limiter_pool.connect():  # the only connection is busy
            with pytest.raises(PoolTimeoutError):  # rendered as 503 (core/errors.py)
                PostgresRateLimiter(lambda: limiter_pool).hit("k", limit=5, window_seconds=60)
        assert _rows(hits_table) == []  # nothing was counted
    finally:
        limiter_pool.dispose()


def _clear_engines() -> None:
    for cached in (get_engine, get_limiter_engine):
        if cached.cache_info().currsize:
            cached().dispose()
        cached.cache_clear()
    _sessionmaker.cache_clear()


@pytest.fixture
def small_real_pools(hits_table: Engine) -> Iterator[None]:
    """The app's own engines (no test-session override), with a two-connection request pool."""
    settings = get_settings()
    settings.rate_limit_backend = "postgres"
    settings.rate_limit_key_secret = SecretStr(SECRET)
    settings.database_pool_size = 2
    settings.database_max_overflow = 0
    settings.rate_limit_pool_size = 1
    _clear_engines()
    yield
    _clear_engines()  # later tests rebuild them from the restored settings


@pytest.mark.usefixtures("small_real_pools")
def test_concurrent_authenticated_requests_neither_stall_nor_exceed_the_limit(
    engine: Engine, hits_table: Engine
) -> None:
    password = "correct horse battery"
    with Session(engine) as s:  # committed: the app's own sessions must see it
        user = User(
            email=f"pool-{uuid.uuid4().hex[:12]}@example.com",
            password_hash=hash_password(password),
            display_name="[Synthetic] Pool test",
            role=Role.student,
            is_active=True,
            is_synthetic=True,
        )
        s.add(user)
        s.commit()
        user_id, email = user.id, user.email
    try:
        app = create_app()  # real get_db: every request holds a connection of the pool of 2
        signed_in = TestClient(app)
        assert signed_in.post(LOGIN, json={"email": email, "password": password}).status_code == 200
        cookies = dict(signed_in.cookies)
        csrf = {"X-CSRF-Token": cookies["ks_csrf"]}
        body = {"current_password": "wrong password!", "new_password": "another long passphrase"}

        def change_password(_: int) -> int:
            with TestClient(app, cookies=cookies) as client:
                return client.post("/api/v1/auth/password", json=body, headers=csrf).status_code

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses = sorted(pool.map(change_password, range(12)))
        elapsed = time.monotonic() - started

        # The password limit is 10 per 15 minutes: exactly 10 checked, 2 refused, none
        # stalled into a 503 (the request pool's timeout is 30 s).
        assert statuses == [401] * 10 + [429] * 2
        assert elapsed < 25
    finally:
        with engine.begin() as conn:
            conn.execute(delete(User).where(User.id == user_id))  # sessions cascade
