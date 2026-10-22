"""Service health endpoint and dependency probes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

import asyncpg
from fastapi import APIRouter, Request
from neo4j import AsyncGraphDatabase
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.redis import create_redis_client

router = APIRouter(tags=["system"])
_LOGGER = logging.getLogger(__name__)

HealthState = Literal["healthy", "unhealthy", "not_configured"]


class ComponentHealth(BaseModel):
    """Status for one required infrastructure dependency."""

    status: HealthState
    latency_ms: float | None = Field(default=None, ge=0)
    detail: str | None = None


class HealthResponse(BaseModel):
    """Stable response contract for the public health endpoint."""

    status: Literal["ok"]
    version: str
    components: dict[str, ComponentHealth]
    postgres_ms: float | None = Field(default=None, ge=0)
    neo4j_ms: float | None = Field(default=None, ge=0)
    redis_ms: float | None = Field(default=None, ge=0)


def _latency_ms(start: float) -> float:
    """Convert a monotonic clock interval into a rounded millisecond value."""

    return round((asyncio.get_running_loop().time() - start) * 1000, 2)


async def _probe(
    name: str,
    operation: Callable[[], Awaitable[None]],
    timeout: float,
) -> ComponentHealth:
    """Execute one dependency probe without allowing it to block the API."""

    start = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(operation(), timeout=timeout)
    except TimeoutError:
        return ComponentHealth(
            status="unhealthy",
            latency_ms=_latency_ms(start),
            detail=f"{name} health probe timed out",
        )
    except Exception as error:  # noqa: BLE001 - health must report dependency errors
        return ComponentHealth(
            status="unhealthy",
            latency_ms=_latency_ms(start),
            detail=f"{name} connectivity check failed: {type(error).__name__}",
        )
    return ComponentHealth(
        status="healthy",
        latency_ms=_latency_ms(start),
        detail=f"{name} connectivity check passed",
    )


async def _probe_postgres(settings: Settings) -> ComponentHealth:
    """Open and close a PostgreSQL connection using the configured URL."""

    if not settings.database_url:
        return ComponentHealth(status="not_configured", detail="not configured")

    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def operation() -> None:
        connection = await asyncpg.connect(url)
        await connection.close()

    return await _probe("postgres", operation, settings.health_probe_timeout_seconds)


async def _probe_neo4j(settings: Settings) -> ComponentHealth:
    """Verify Neo4j connectivity and close the short-lived driver."""

    uri, user, password = (
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password,
    )
    if not uri or not user or not password:
        return ComponentHealth(status="not_configured", detail="not configured")
    secret = password.get_secret_value()

    async def operation() -> None:
        driver = AsyncGraphDatabase.driver(
            uri,
            auth=(user, secret),
        )
        try:
            await driver.verify_connectivity()
        finally:
            await driver.close()

    return await _probe("neo4j", operation, settings.health_probe_timeout_seconds)


async def _probe_redis(settings: Settings) -> ComponentHealth:
    """Send a Redis PING and close the async client."""

    url = settings.redis_url
    if not url:
        return ComponentHealth(status="not_configured", detail="not configured")

    async def operation() -> None:
        client = create_redis_client(url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    return await _probe("redis", operation, settings.health_probe_timeout_seconds)


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Probe critical stores without requiring authentication or exposing URLs."""

    settings: Settings = request.app.state.settings
    postgres, neo4j, redis_status = await asyncio.gather(
        _probe_postgres(settings),
        _probe_neo4j(settings),
        _probe_redis(settings),
    )
    components = {
        "postgres": postgres,
        "neo4j": neo4j,
        "redis": redis_status,
    }
    latency_fields = {
        "postgres_ms": postgres.latency_ms,
        "neo4j_ms": neo4j.latency_ms,
        "redis_ms": redis_status.latency_ms,
    }
    healthy = all(component.status == "healthy" for component in components.values())
    _LOGGER.info(
        "health_check",
        extra={
            "event": "health_check",
            "status": "ok" if healthy else "degraded",
            **latency_fields,
            "component_statuses": {
                key: value.status for key, value in components.items()
            },
        },
    )
    if not healthy:
        raise ApiError(
            503,
            "SERVICE_UNAVAILABLE",
            "One or more critical stores are unavailable.",
            {
                "status": "degraded",
                "components": {
                    key: value.model_dump() for key, value in components.items()
                },
                **latency_fields,
            },
        )
    return HealthResponse(
        status="ok",
        version="0.1.0",
        components=components,
        **latency_fields,
    )
