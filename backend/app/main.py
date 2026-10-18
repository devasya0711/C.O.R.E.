"""FastAPI application entrypoint for C.O.R.E."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.health import router as health_router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Attach validated settings for the lifetime of the API process."""

    app.state.settings = get_settings()
    yield


def create_app() -> FastAPI:
    """Build the application instance used by Uvicorn and tests."""

    application = FastAPI(
        title="C.O.R.E. API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(health_router, prefix="/api/v1")
    return application


app = create_app()
