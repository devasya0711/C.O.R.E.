"""Run PostgreSQL migrations with an async engine, or render offline SQL."""

import asyncio

from alembic import context
from app.core.config import get_settings
from app.db import Base, create_database
from sqlalchemy.engine import Connection

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render PostgreSQL SQL without settings, credentials, or a connection."""

    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Execute migration operations on an already-open sync facade."""

    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Connect through SQLAlchemy's real async PostgreSQL engine."""

    database_url = (
        config.get_main_option("sqlalchemy.url") or get_settings().database_url
    )
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to run online migrations.")
    engine, _ = create_database(database_url)
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("Online migrations require PostgreSQL.")
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Accept an injected sync connection, otherwise own the async lifecycle.

    Async callers may invoke Alembic through AsyncConnection.run_sync with
    config.attributes['connection'] set to the supplied sync connection.
    """

    connection = config.attributes.get("connection")
    if connection is not None:
        _run_migrations(connection)
    else:
        asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
