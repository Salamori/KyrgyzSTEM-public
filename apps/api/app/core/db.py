import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache

from sqlalchemy import DateTime, Engine, Enum, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core.config import get_settings

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def str_enum(enum: type[StrEnum], name: str, length: int = 16) -> Enum:
    """Store a StrEnum as VARCHAR + CHECK constraint (no native PG enum: easier migrations)."""
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


def create_database_engine(*, pool_size: int, max_overflow: int, pool_timeout: float) -> Engine:
    """An engine on DATABASE_URL with its own bounded pool."""
    settings = get_settings()
    connect_args: dict[str, object] = {"connect_timeout": 5}
    if not settings.database_prepared_statements:
        connect_args["prepare_threshold"] = None  # psycopg: never prepare (PgBouncer)
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        # Fail fast when the database is down (requests get 503, see core/errors.py).
        connect_args=connect_args,
        # Keep bound values (emails, password hashes, source code) out of error messages.
        hide_parameters=True,
    )


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_database_engine(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=30,  # SQLAlchemy's default
    )


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Request-scoped session. Routes commit explicitly; anything else is rolled back."""
    db = _sessionmaker()()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
