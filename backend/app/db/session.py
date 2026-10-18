"""Explicit async engine creation and request-scoped transactional sessions."""

from collections.abc import AsyncGenerator
from typing import cast

from fastapi import Request
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine


def create_database(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create a lazy engine/factory pair; the application owns engine disposal.

    Plain PostgreSQL URLs use asyncpg. SQLite+aiosqlite URLs are supported for
    portable tests. Engine creation never connects or runs migrations.
    """

    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
    engine = _create_async_engine(
        url, pool_pre_ping=True, echo=False, hide_parameters=True
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Commit successful requests and roll back failed/cancelled transactions."""

    session_factory = cast(
        async_sessionmaker[AsyncSession] | None,
        getattr(request.app.state, "session_factory", None),
    )
    if session_factory is None:
        # Keep model/factory imports independent of API exception registration.
        from app.core.errors import ApiError

        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Database is not configured.",
        )

    async with session_factory() as session, session.begin():
        yield session
