"""FastAPI application entrypoint for C.O.R.E."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.repos import router as repos_router
from app.api.v1.settings import router as settings_router
from app.core.auth import ClerkVerifier, get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.rate_limit import enforce_rate_limit
from app.db.neo4j import create_neo4j_driver, initialize_schema
from app.db.redis import create_redis_client
from app.db.session import create_database
from app.services.embedding_service import EmbeddingService
from app.services.ingestion_service import IngestionService
from app.services.neo4j_service import Neo4jService
from app.services.repository_source import RepositorySource


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own store/client lifetimes and apply the idempotent Neo4j schema."""
    settings: Settings = app.state.settings
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            httpx.AsyncClient(timeout=10, follow_redirects=False)
        )
        app.state.http_client = client
        app.state.clerk_verifier = ClerkVerifier(settings, client)
        app.state.session_factory = None
        if settings.database_url:
            engine, factory = create_database(settings.database_url)
            stack.push_async_callback(engine.dispose)
            app.state.session_factory = factory
        app.state.redis = None
        if settings.redis_url:
            app.state.redis = create_redis_client(settings.redis_url)
            stack.push_async_callback(app.state.redis.aclose)
        driver = create_neo4j_driver(settings)
        app.state.neo4j = driver
        if driver:
            stack.push_async_callback(driver.close)
            await initialize_schema(driver)
        app.state.ingestion = None
        if (
            driver
            and app.state.redis is not None
            and app.state.session_factory is not None
        ):
            service = IngestionService(
                app.state.session_factory,
                app.state.redis,
                Neo4jService(driver),
                RepositorySource(
                    settings.sample_monolith_path,
                    settings.clone_timeout_seconds,
                    settings.ingestion_max_source_bytes,
                ),
                EmbeddingService(
                    client,
                    settings.openai_api_key,
                    model=settings.embedding_model,
                    timeout_seconds=settings.embedding_timeout_seconds,
                    max_retries=settings.embedding_max_retries,
                ),
                settings,
            )
            await service.start()
            stack.push_async_callback(service.close)
            app.state.ingestion = service
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application instance used by Uvicorn and tests."""

    application = FastAPI(
        title="C.O.R.E. API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = settings or get_settings()
    install_error_handlers(application)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=application.state.settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["Retry-After"],
    )
    application.include_router(health_router, prefix="/api/v1")
    protected = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(get_current_user), Depends(enforce_rate_limit)],
    )
    protected.include_router(settings_router)
    protected.include_router(repos_router)
    application.include_router(protected)
    return application


app = create_app()
