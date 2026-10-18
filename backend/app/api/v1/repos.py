"""Authenticated repository registration, progress polling, and graph retrieval."""

from __future__ import annotations

import hashlib
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_owned_repository
from app.core.auth import get_current_user
from app.core.errors import ApiError
from app.db.models import IngestionJob, Repository, SeamCluster, User
from app.db.session import get_session
from app.schemas.graph import GraphResponse
from app.schemas.repos import (
    IngestionMessage,
    IngestionProgress,
    RepositoryAccepted,
    RepositoryCreate,
    RepositorySummary,
)
from app.services.ingestion_service import IngestionService
from app.services.neo4j_service import Neo4jService

router = APIRouter(prefix="/repos", tags=["repositories"])


def ingestion_service(request: Request) -> IngestionService:
    service = cast(IngestionService | None, request.app.state.ingestion)
    if service is None:
        raise ApiError(
            503, "INGESTION_UNAVAILABLE", "Repository ingestion is unavailable."
        )
    return service


@router.post("", response_model=RepositoryAccepted, status_code=202)
async def create_repository(
    payload: RepositoryCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RepositoryAccepted:
    service = ingestion_service(request)
    assert payload.repo_url is not None
    # Serialize registrations for this exact owner/source/branch without changing
    # or deleting pre-existing repository metadata to add a uniqueness constraint.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        identity = f"{user.id}|{payload.repo_url}|{payload.default_branch}"
        lock = int.from_bytes(
            hashlib.sha256(identity.encode()).digest()[:8], signed=True
        )
        await session.execute(select(func.pg_advisory_xact_lock(lock)))
    repository = await session.scalar(
        select(Repository)
        .where(
            Repository.user_id == user.id,
            Repository.repo_url == payload.repo_url,
            Repository.default_branch == payload.default_branch,
        )
        .order_by(Repository.created_at, Repository.id)
        .limit(1)
    )
    if repository is None:
        repository = Repository(
            user_id=user.id,
            name=payload.name,
            repo_url=payload.repo_url,
            default_branch=payload.default_branch,
            status="INGESTING",
        )
        session.add(repository)
        await session.flush()
    job = await session.scalar(
        select(IngestionJob)
        .join(Repository, Repository.id == IngestionJob.repo_id)
        .where(Repository.user_id == user.id, Repository.id == repository.id)
        .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
        .limit(1)
    )
    if job is None or job.status in {"COMPLETED", "FAILED"}:
        job = IngestionJob(repo_id=repository.id)
        session.add(job)
    repository.status = "INGESTING"
    await session.flush()
    message = IngestionMessage(user_id=user.id, repo_id=repository.id, job_id=job.id)
    # Queue consumers cannot observe uncommitted rows. Commit before XADD; if
    # Redis is unavailable, return 503 and keep the durable QUEUED job. Retrying
    # POST republishes that same job instead of creating a lost/duplicate job.
    await session.commit()
    await service.enqueue(message)
    return RepositoryAccepted(repo_id=message.repo_id)


@router.get("", response_model=list[RepositorySummary])
async def list_repositories(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[RepositorySummary]:
    repositories = list(
        await session.scalars(
            select(Repository)
            .where(Repository.user_id == user.id)
            .order_by(Repository.created_at.desc(), Repository.id)
        )
    )
    jobs = await session.scalars(
        select(IngestionJob)
        .join(Repository, Repository.id == IngestionJob.repo_id)
        .where(Repository.user_id == user.id)
        .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
    )
    latest: dict[UUID, IngestionProgress] = {}
    for job in jobs:
        if job.repo_id not in latest:
            latest[job.repo_id] = IngestionProgress.model_validate(job)
    return [
        RepositorySummary.model_validate(repository).model_copy(
            update={"ingestion": latest.get(repository.id)}
        )
        for repository in repositories
    ]


@router.get("/{repo_id}/graph", response_model=GraphResponse)
async def repository_graph(
    request: Request,
    repository: Annotated[Repository, Depends(get_owned_repository)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GraphResponse:
    if repository.status != "READY":
        raise ApiError(
            409, "REPOSITORY_NOT_READY", "Repository ingestion is not ready."
        )
    driver = getattr(request.app.state, "neo4j", None)
    if driver is None:
        raise ApiError(503, "GRAPH_UNAVAILABLE", "Repository graph is unavailable.")
    seams = await session.scalars(
        select(SeamCluster)
        .join(Repository, Repository.id == SeamCluster.repo_id)
        .where(
            SeamCluster.repo_id == repository.id,
            Repository.user_id == repository.user_id,
        )
    )
    try:
        return await Neo4jService(driver).flow_graph(
            repository.id, {seam.community_id: seam.shared_db_tables for seam in seams}
        )
    except (DriverError, Neo4jError):
        raise ApiError(
            503, "GRAPH_UNAVAILABLE", "Repository graph is unavailable."
        ) from None
