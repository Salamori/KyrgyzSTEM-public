from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    # Keep loggers created before migrations (the app's, when run in-process by tests).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    def run(connection: Connection) -> None:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()

    # Tests pass an open connection; the CLI builds an engine from DATABASE_URL.
    existing = config.attributes.get("connection")
    if existing is not None:
        run(existing)
        return
    with create_engine(get_settings().database_url).connect() as connection:
        run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
