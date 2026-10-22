"""Authenticated migration workflow creation, SSE replay, and PR publishing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from neo4j.exceptions import DriverError, Neo4jError
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_owned_repository
from app.api.dependencies import lock_owned_repository as _lock_repository
from app.core.auth import get_current_user
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.redaction import SecretRedactor
from app.db.models import MigrationJob, Repository, SeamCluster, User
from app.db.session import get_session
from app.schemas.migrations import (
    MigrationAccepted,
    MigrationCreate,
    MigrationEvent,
    MigrationEventType,
    MigrationJobDetail,
    MigrationPRRequest,
    MigrationPRResponse,
    MigrationStatus,
)
from app.services.migration_events import (
    MigrationEventService,
    MigrationEventStoreError,
    RedisStreamClient,
)
from app.services.migration_service import MigrationService
from app.services.neo4j_service import Neo4jService
from app.services.sandbox_runner import SandboxIsolation
from app.workflows.migration_graph import MigrationState

router = APIRouter(prefix="/repos", tags=["migrations"])


def _service(request: Request) -> MigrationService:
    configured = getattr(request.app.state, "migration_service", None)
    if configured is not None:
        return cast(MigrationService, configured)
    factory = getattr(request.app.state, "session_factory", None)
    redis = getattr(request.app.state, "redis", None)
    driver = getattr(request.app.state, "neo4j", None)
    client = getattr(request.app.state, "http_client", None)
    if factory is None or redis is None or driver is None or client is None:
        raise ApiError(
            503, "MIGRATION_UNAVAILABLE", "Migration workflow is unavailable."
        )
    return MigrationService(
        factory,
        cast(RedisStreamClient, redis),
        Neo4jService(driver),
        cast(httpx.AsyncClient, client),
        cast(Settings, request.app.state.settings),
        cast(set[asyncio.Task[None]], request.app.state.migration_tasks),
    )


def _redactor(request: Request) -> SecretRedactor:
    return SecretRedactor.from_settings(getattr(request.app.state, "settings", None))


async def _owned_job(
    session: AsyncSession, repo_id: UUID, job_id: UUID, user_id: UUID
) -> MigrationJob:
    job = await session.scalar(
        select(MigrationJob)
        .join(Repository, Repository.id == MigrationJob.repo_id)
        .where(
            MigrationJob.id == job_id,
            MigrationJob.repo_id == repo_id,
            Repository.user_id == user_id,
        )
    )
    if job is None:
        raise ApiError(
            403, "RESOURCE_ACCESS_DENIED", "Migration not found or access denied."
        )
    return job


def _sse(record_id: str, event: MigrationEvent) -> str:
    data = json.dumps(
        event.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")
    )
    return f"id: {record_id}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.post("/{repo_id}/migrations", response_model=MigrationAccepted, status_code=202)
async def create_migration(
    repo_id: UUID,
    payload: MigrationCreate,
    request: Request,
    repository: Annotated[Repository, Depends(get_owned_repository)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MigrationAccepted:
    del repo_id
    repository = await _lock_repository(session, repository)
    service = _service(request)
    if repository.status != "READY":
        raise ApiError(
            409, "REPOSITORY_NOT_READY", "Repository ingestion is not ready."
        )
    if not repository.last_commit_sha:
        raise ApiError(
            409,
            "REPOSITORY_SOURCE_IDENTITY_MISSING",
            "Repository source identity is unavailable; re-ingest first.",
        )
    if payload.create_github_pr and not repository.repo_url.startswith(
        "https://github.com/"
    ):
        raise ApiError(
            422,
            "GITHUB_REPOSITORY_REQUIRED",
            "GitHub PR creation requires a GitHub repository source.",
        )
    driver = getattr(request.app.state, "neo4j", None)
    if driver is None:
        raise ApiError(503, "GRAPH_UNAVAILABLE", "Repository graph is unavailable.")
    try:
        graph = Neo4jService(driver)
        snapshot = await graph.snapshot_version(repository.id)
        if snapshot is None:
            raise ApiError(
                409, "REPOSITORY_GRAPH_EMPTY", "Repository graph is unavailable."
            )
        seam = await session.scalar(
            select(SeamCluster).where(
                SeamCluster.id == payload.seam_id,
                SeamCluster.repo_id == repository.id,
            )
        )
        if seam is None:
            raise ApiError(404, "SEAM_NOT_FOUND", "Seam not found for this repository.")
        if seam.snapshot_version != snapshot:
            raise ApiError(
                409,
                "SEAM_SNAPSHOT_STALE",
                "Re-run seam analysis before starting a migration.",
            )
        current_graph = await graph.load_graph(repository.id)
    except (DriverError, Neo4jError):
        raise ApiError(
            503, "GRAPH_UNAVAILABLE", "Repository graph is unavailable."
        ) from None
    active_names = {node.qualified_name for node in current_graph.nodes}
    if not set(seam.node_ids).issubset(active_names):
        raise ApiError(
            409,
            "SEAM_SNAPSHOT_STALE",
            "The selected seam is no longer present in the active graph.",
        )
    job = MigrationJob(
        repo_id=repository.id,
        seam_id=seam.id,
        create_github_pr=payload.create_github_pr,
        snapshot_version=snapshot,
        source_commit_sha=repository.last_commit_sha,
    )
    session.add(job)
    await session.flush()
    await session.commit()
    state: MigrationState = {
        "job_id": job.id,
        "repo_id": repository.id,
        "seam_id": seam.id,
        "create_github_pr": payload.create_github_pr,
        "expected_snapshot_version": snapshot,
    }
    try:
        service.start(state)
    except Exception:
        job.status = "FAILED"
        await session.commit()
        raise ApiError(
            503, "MIGRATION_UNAVAILABLE", "Migration workflow is unavailable."
        ) from None
    del user
    return MigrationAccepted(job_id=job.id)


@router.get("/{repo_id}/migrations/{job_id}", response_model=MigrationJobDetail)
async def migration_detail(
    repo_id: UUID,
    job_id: UUID,
    request: Request,
    repository: Annotated[Repository, Depends(get_owned_repository)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MigrationJobDetail:
    del repo_id
    job = await _owned_job(session, repository.id, job_id, user.id)
    redactor = _redactor(request)
    return MigrationJobDetail(
        job_id=job.id,
        repo_id=job.repo_id,
        seam_id=job.seam_id,
        status=cast(MigrationStatus, job.status),
        create_github_pr=job.create_github_pr,
        crag_status=job.crag_status,
        retry_count=job.retry_count,
        generated_artifacts=dict(job.generated_artifacts),
        sandbox_logs=(
            redactor.redact(job.sandbox_logs) if job.sandbox_logs is not None else None
        ),
        sandbox_passed=job.sandbox_passed,
        sandbox_isolation=job.sandbox_isolation,
        sandbox_isolation_note=(
            redactor.redact(job.sandbox_isolation_note)
            if job.sandbox_isolation_note is not None
            else None
        ),
        pr_url=job.pr_url,
        created_at=job.created_at,
    )


@router.get("/{repo_id}/migrations/{job_id}/stream")
async def migration_stream(
    repo_id: UUID,
    job_id: UUID,
    request: Request,
    repository: Annotated[Repository, Depends(get_owned_repository)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    del repo_id
    job = await _owned_job(session, repository.id, job_id, user.id)
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise ApiError(
            503, "MIGRATION_UNAVAILABLE", "Migration event stream is unavailable."
        )
    events = MigrationEventService(redis)
    try:
        initial = await events.read_events(
            job_id, last_event_id=last_event_id, stop_at_terminal=False
        )
    except (ValueError, MigrationEventStoreError):
        raise ApiError(
            503,
            "MIGRATION_STREAM_UNAVAILABLE",
            "Migration event stream is unavailable.",
        ) from None
    for record in initial:
        if record.event.repo_id != repository.id:
            raise ApiError(
                503,
                "MIGRATION_STREAM_UNAVAILABLE",
                "Migration event stream is invalid.",
            )

    async def body() -> AsyncIterator[str]:
        cursor = last_event_id
        records = initial
        deadline = asyncio.get_running_loop().time() + 900
        while True:
            for index, record in enumerate(records):
                if record.event.repo_id != repository.id:
                    return
                yield _sse(record.id, record.event)
                cursor = record.id
                if record.event.event_type in {
                    MigrationEventType.PR_CREATED,
                    MigrationEventType.MIGRATION_FAILED,
                    MigrationEventType.HUMAN_REVIEW,
                }:
                    return
                if record.event.event_type is MigrationEventType.COMPLETED:
                    later_pr = any(
                        later.event.event_type is MigrationEventType.PR_CREATED
                        for later in records[index + 1 :]
                    )
                    if not later_pr and not job.create_github_pr:
                        return
            if not records and job.status in {"COMPLETED", "FAILED"}:
                return
            if asyncio.get_running_loop().time() >= deadline:
                return
            await asyncio.sleep(0.25)
            try:
                records = await events.read_events(
                    job_id, last_event_id=cursor, stop_at_terminal=False
                )
            except (ValueError, MigrationEventStoreError, RedisError):
                return

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{repo_id}/migrations/{job_id}/pr",
    response_model=MigrationPRResponse,
)
async def create_migration_pr(
    repo_id: UUID,
    job_id: UUID,
    payload: MigrationPRRequest,
    request: Request,
    repository: Annotated[Repository, Depends(get_owned_repository)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MigrationPRResponse:
    del repo_id
    repository = await _lock_repository(session, repository, shared=True)
    if not repository.repo_url.startswith("https://github.com/"):
        raise ApiError(
            422,
            "GITHUB_REPOSITORY_REQUIRED",
            "GitHub PR creation requires a GitHub repository source.",
        )
    job = await _owned_job(session, repository.id, job_id, user.id)
    seam = await session.scalar(
        select(SeamCluster).where(
            SeamCluster.id == job.seam_id, SeamCluster.repo_id == repository.id
        )
    )
    if seam is None:
        raise ApiError(404, "SEAM_NOT_FOUND", "Seam not found for this repository.")
    if payload.job_id is not None and payload.job_id != job.id:
        raise ApiError(
            422, "VALIDATION_ERROR", "Migration job identifiers do not match."
        )
    if job.sandbox_isolation != SandboxIsolation.DOCKER.value:
        raise ApiError(
            409,
            "SANDBOX_ISOLATION_UNAVAILABLE",
            "Migration artifacts must pass inside a Docker sandbox before publication.",
        )
    effective = payload.model_copy(update={"base_branch": repository.default_branch})
    return await _service(request).publish_job(job, repository, seam, user, effective)
