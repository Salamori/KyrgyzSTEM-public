"""Sliding-window rate limiters.

RATE_LIMIT_BACKEND chooses where the counts live (docs/SECURITY.md):

* "memory" (default): in this process. Run one API worker.
* "postgres": in the `rate_limit_hits` table, shared by every instance. Required where
  instances share no memory, such as Vercel Functions.
"""

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import lru_cache

from sqlalchemy import (
    CheckConstraint,
    Connection,
    DateTime,
    Engine,
    Index,
    String,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.core.db import Base, UUIDPrimaryKey, create_database_engine
from app.core.errors import ApiError

# Keys include client-chosen parts (emails), so idle keys are dropped periodically.
SWEEP_EVERY = 1024
# The postgres backend deletes expired rows on about one hit in this many (and
# scripts.purge_retention deletes them too).
PURGE_ONE_IN = 256


def _rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "Too many attempts. Please wait and try again.",
        headers={"Retry-After": str(retry_after)},
    )


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._windows: dict[str, int] = {}
        self._calls = 0
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        """Record one hit for `key`; raise 429 if the window already holds `limit` hits."""
        if not get_settings().rate_limit_enabled:
            return
        now = time.monotonic()
        with self._lock:
            self._calls += 1
            if self._calls % SWEEP_EVERY == 0:
                self._sweep(now)
            self._windows[key] = max(window_seconds, self._windows.get(key, 0))
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                raise _rate_limited(max(1, math.ceil(hits[0] + window_seconds - now)))
            hits.append(now)

    def sweep(self) -> None:
        with self._lock:
            self._sweep(time.monotonic())

    def _sweep(self, now: float) -> None:
        idle = [k for k, h in self._hits.items() if not h or h[-1] <= now - self._windows[k]]
        for key in idle:
            del self._hits[key]
            del self._windows[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._windows.clear()


class RateLimitHit(UUIDPrimaryKey, Base):
    """One counted request of the postgres backend.

    `key_hash` is an HMAC of the limiter key, never the key: keys contain IP addresses,
    emails and user ids. A row is useless once `expires_at` has passed and is deleted then.
    """

    __tablename__ = "rate_limit_hits"
    __table_args__ = (
        Index("ix_rate_limit_hits_key_hit", "key_hash", "hit_at"),
        CheckConstraint("expires_at > hit_at", name="expires_after_hit"),
    )

    key_hash: Mapped[str] = mapped_column(String(64))
    hit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def _key_digest(key: str) -> bytes:
    secret = get_settings().rate_limit_key_secret.get_secret_value()
    if not secret:  # config.py refuses to start without it; fail closed if it is unset anyway
        raise RuntimeError("RATE_LIMIT_KEY_SECRET is required with RATE_LIMIT_BACKEND=postgres")
    return hmac.new(secret.encode(), key.encode(), hashlib.sha256).digest()


@lru_cache
def get_limiter_engine() -> Engine:
    """The postgres backend's own small pool, never the request pool (core/db.get_engine).

    Rate-limited routes run after authentication, which keeps a request connection checked
    out. If hits came from the same pool, enough concurrent requests would each hold one
    connection and wait for a second until the pool timeout. Hits never wait on the request
    pool, so this pool always drains; when it is busy longer than
    RATE_LIMIT_POOL_TIMEOUT_SECONDS the hit fails closed (503, core/errors.py).
    """
    settings = get_settings()
    return create_database_engine(
        pool_size=settings.rate_limit_pool_size,
        max_overflow=0,
        pool_timeout=settings.rate_limit_pool_timeout_seconds,
    )


def purge_expired_hits(conn: Connection) -> int:
    """Delete postgres-backend rows whose window has passed; returns how many."""
    expired = RateLimitHit.expires_at <= func.clock_timestamp()
    return conn.execute(delete(RateLimitHit).where(expired)).rowcount


class PostgresRateLimiter:
    """The same limits as RateLimiter, counted in PostgreSQL for every instance at once.

    Each hit runs in its own short transaction, never the request's (which may roll back),
    under a transaction-level advisory lock on the key, so two instances cannot both take
    the last slot. Times come from the database clock, not from the instances'.
    """

    def __init__(self, engine: Callable[[], Engine] = get_limiter_engine) -> None:
        self._engine = engine

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        """Record one hit for `key`; raise 429 if the window already holds `limit` hits."""
        if not get_settings().rate_limit_enabled:
            return
        digest = _key_digest(key)
        key_hash = digest.hex()
        # pg_advisory_xact_lock takes a bigint: the digest's first eight bytes, signed.
        lock_id = int.from_bytes(digest[:8], "big", signed=True)
        window = timedelta(seconds=window_seconds)
        with self._engine().begin() as conn:
            conn.execute(select(func.pg_advisory_xact_lock(lock_id)))
            now: datetime = conn.execute(select(func.clock_timestamp())).scalar_one()
            count, oldest = conn.execute(
                select(func.count(), func.min(RateLimitHit.hit_at)).where(
                    RateLimitHit.key_hash == key_hash, RateLimitHit.hit_at > now - window
                )
            ).one()
            if count >= limit:
                raise _rate_limited(max(1, math.ceil((oldest + window - now).total_seconds())))
            conn.execute(
                insert(RateLimitHit).values(key_hash=key_hash, hit_at=now, expires_at=now + window)
            )
            if secrets.randbelow(PURGE_ONE_IN) == 0:
                purge_expired_hits(conn)


class ConfiguredRateLimiter:
    """What routes call: RATE_LIMIT_BACKEND chooses where the counts live."""

    def __init__(self) -> None:
        self.memory = RateLimiter()
        self.postgres = PostgresRateLimiter()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        if get_settings().rate_limit_backend == "postgres":
            self.postgres.hit(key, limit, window_seconds)
        else:
            self.memory.hit(key, limit, window_seconds)

    def reset(self) -> None:
        """Forget the in-memory counts (tests). Postgres rows expire on their own."""
        self.memory.reset()


limiter = ConfiguredRateLimiter()
