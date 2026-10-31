from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Base
from app.modules.users.models import User
from scripts import seed


def test_health(client: TestClient) -> None:
    res = client.get("/api/v1/health")
    assert res.json() == {"status": "ok", "database": "ok"}
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_migrations_match_models(connection: Connection) -> None:
    diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_check_constraints_match_models(connection: Connection) -> None:
    """Autogenerate ignores CHECK constraints, so compare names explicitly."""
    from sqlalchemy import CheckConstraint, text

    in_db = set(
        connection.execute(text("SELECT conname FROM pg_constraint WHERE contype = 'c'")).scalars()
    )
    in_models = {
        str(c.name)
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if isinstance(c, CheckConstraint)
    }
    assert in_models <= in_db, in_models - in_db


def test_check_constraint_definitions_match_models(connection: Connection) -> None:
    """Names alone miss a migration that changed a condition but kept the name.

    PostgreSQL normalizes each model condition: it is added as a temporary NOT VALID
    constraint inside a savepoint (so existing rows are not checked) and its stored
    definition is compared with the real constraint's. Everything is rolled back.
    """
    from sqlalchemy import CheckConstraint, text

    definition = text(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid WHERE t.relname = :table AND c.conname = :name"
    )
    mismatches = []
    for table in Base.metadata.tables.values():
        for check in table.constraints:
            if not isinstance(check, CheckConstraint):
                continue
            condition = str(
                check.sqltext.compile(
                    dialect=connection.dialect, compile_kwargs={"literal_binds": True}
                )
            )
            actual = connection.execute(
                definition, {"table": table.name, "name": str(check.name)}
            ).scalar_one()
            savepoint = connection.begin_nested()
            connection.execute(
                text(
                    f'ALTER TABLE "{table.name}" ADD CONSTRAINT zz_meta_probe '
                    f"CHECK ({condition}) NOT VALID"
                )
            )
            expected = connection.execute(
                definition, {"table": table.name, "name": "zz_meta_probe"}
            ).scalar_one()
            savepoint.rollback()
            if expected.removesuffix(" NOT VALID") != actual:
                mismatches.append((table.name, str(check.name), actual, expected))
    assert mismatches == []


def test_seed_is_synthetic_and_idempotent(db: Session) -> None:
    first = seed.seed(db, "dev-only-password")
    assert len(first) == len(seed.SYNTHETIC_USERS)
    assert seed.seed(db, "dev-only-password") == []
    users = db.scalars(select(User)).all()
    assert all(u.is_synthetic and u.email.endswith("@example.com") for u in users)
    assert all(u.display_name.startswith("[Synthetic]") for u in users)
    assert db.scalar(select(func.count()).select_from(User)) == len(seed.SYNTHETIC_USERS)


def test_seed_refuses_production() -> None:
    get_settings().app_env = "production"
    get_settings().seed_password = "dev-only-password"
    assert seed.main() == 1


def test_seed_requires_password() -> None:
    get_settings().seed_password = "short"
    assert seed.main() == 1


def test_seed_creates_synthetic_demo_club(db: Session) -> None:
    from app.modules.orgs.models import AttendanceRecord, Organization

    seed.seed(db, "dev-only-password")
    seed.seed(db, "dev-only-password")  # idempotent
    org = db.scalars(select(Organization)).one()
    assert org.is_synthetic and org.name.startswith("[Synthetic]")
    assert db.scalar(select(func.count()).select_from(AttendanceRecord)) == 6
