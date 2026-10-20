"""Owner-protected seam analysis and snapshot-scoped blast-radius caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Annotated, cast
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, Request
from neo4j import AsyncDriver
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.boundary_mapper import BoundaryMapperAgent
from app.agents.impact_predictor import ImpactPredictorAgent
from app.api.dependencies import get_owned_repository
from app.core.auth import get_current_user
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import Repository, SeamCluster, User
from app.db.redis import BLAST_RADIUS_TTL_SECONDS
from app.db.session import get_session
from app.schemas.analysis import (
    ImpactRequest,
    ImpactResponse,
    SeamResponse,
    SeamsResponse,
)
from app.services.neo4j_service import Neo4jService
from app.services.seam_naming import SeamNamingService

router = APIRouter(prefix="/repos", tags=["analysis"])


def _driver(request: Request) -> AsyncDriver:
    driver = cast(AsyncDriver | None, request.app.state.neo4j)
    if driver is None:
        raise ApiError(
            503, "ANALYSIS_UNAVAILABLE", "Repository analysis is unavailable."
        )
    return driver


async def _lock_repository(
    session: AsyncSession, repository: Repository, *, shared: bool = False
) -> Repository:
    # Serialize seam persistence and block concurrent ingestion progress/writes.
    # Refresh the earlier ownership read after waiting, so READY isn't stale.
    locked = await session.scalar(
        select(Repository)
        .where(Repository.id == repository.id, Repository.user_id == repository.user_id)
        .with_for_update(read=shared)
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise ApiError(
            403, "RESOURCE_ACCESS_DENIED", "Repository not found or access denied."
        )
    if locked.status != "READY":
        raise ApiError(
            409, "REPOSITORY_NOT_READY", "Repository ingestion is not ready."
        )
    return locked


@router.post("/{repo_id}/seams/analyze", response_model=SeamsResponse)
async def analyze_seams(
    request: Request,
    repository: Annotated[Repository, Depends(get_owned_repository)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SeamsResponse:
    settings = cast(Settings, request.app.state.settings)
    try:
        async with asyncio.timeout(settings.analysis_timeout_seconds):
            repository = await _lock_repository(session, repository)
            driver = _driver(request)
            graph = Neo4jService(driver)
            version = await graph.snapshot_version(repository.id)
            if version is None:
                raise ApiError(
                    409,
                    "REPOSITORY_GRAPH_EMPTY",
                    "Repository graph is unavailable.",
                )
            result = await BoundaryMapperAgent(driver).analyze(repository.id)
            existing = list(
                await session.scalars(
                    select(SeamCluster)
                    .join(Repository, Repository.id == SeamCluster.repo_id)
                    .where(
                        Repository.id == repository.id,
                        Repository.user_id == repository.user_id,
                    )
                    .order_by(SeamCluster.community_id, SeamCluster.id)
                )
            )
            by_members = {tuple(sorted(row.node_ids)): row for row in existing}
            unnamed = [
                c for c in result.communities if tuple(c.node_ids) not in by_members
            ]
            names = await SeamNamingService(
                request.app.state.http_client, settings
            ).suggest(unnamed)
            next_id = max((row.community_id for row in existing), default=-1) + 1
            remap: dict[int, int] = {}
            rows: list[SeamCluster] = []
            for community in result.communities:
                members = tuple(community.node_ids)
                row = by_members.get(members)
                if row is None:
                    row = SeamCluster(
                        id=uuid5(repository.id, "seam:" + json.dumps(members)),
                        repo_id=repository.id,
                        community_id=next_id,
                        suggested_name=names[community.community_id],
                        snapshot_version=version,
                        node_ids=list(members),
                    )
                    next_id += 1
                    session.add(row)
                row.cohesion_score = community.cohesion_score
                row.shared_db_tables = list(community.shared_db_tables)
                row.snapshot_version = version
                remap[community.community_id] = row.community_id
                rows.append(row)
            await session.flush()
            if version is not None:
                await graph.set_communities(
                    repository.id,
                    {name: remap[value] for name, value in result.assignments.items()},
                    version,
                )
            # Preserve previous rows/IDs for migration history. IDs are never
            # recycled; only communities present in the current graph are active.
            response = SeamsResponse(
                seams=[SeamResponse.model_validate(row) for row in rows]
            )
            await session.commit()
            return response
    except TimeoutError:
        raise ApiError(
            504, "ANALYSIS_TIMEOUT", "Repository analysis timed out."
        ) from None
    except (DriverError, Neo4jError):
        raise ApiError(
            503, "ANALYSIS_UNAVAILABLE", "Repository analysis is unavailable."
        ) from None


def impact_cache_key(
    user_id: UUID, repo_id: UUID, version: str | None, payload: ImpactRequest
) -> str:
    digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    return f"core:impact:v1:{user_id}:{repo_id}:{version or 'empty'}:{digest}"


@router.post("/{repo_id}/impact", response_model=ImpactResponse)
async def analyze_impact(
    payload: ImpactRequest,
    request: Request,
    repository: Annotated[Repository, Depends(get_owned_repository)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImpactResponse:
    settings = cast(Settings, request.app.state.settings)
    try:
        async with asyncio.timeout(settings.analysis_timeout_seconds):
            repository = await _lock_repository(session, repository, shared=True)
            driver = _driver(request)
            redis = cast(Redis | None, request.app.state.redis)
            if redis is None:
                raise ApiError(
                    503,
                    "ANALYSIS_CACHE_UNAVAILABLE",
                    "Analysis caching is unavailable.",
                )
            version = await Neo4jService(driver).snapshot_version(repository.id)
            key = impact_cache_key(user.id, repository.id, version, payload)
            cached = await redis.get(key)
            if cached is not None:
                try:
                    response = ImpactResponse.model_validate_json(cached)
                    if response.target == payload.target_qualified_name:
                        return response
                except ValidationError:
                    pass
                await redis.delete(key)
            response = await ImpactPredictorAgent(driver).analyze(
                repository.id, payload
            )
            await redis.set(
                key, response.model_dump_json(), ex=BLAST_RADIUS_TTL_SECONDS
            )
            return response
    except TimeoutError:
        raise ApiError(
            504, "ANALYSIS_TIMEOUT", "Repository analysis timed out."
        ) from None
    except (DriverError, Neo4jError):
        raise ApiError(
            503, "ANALYSIS_UNAVAILABLE", "Repository analysis is unavailable."
        ) from None
    except RedisError:
        raise ApiError(
            503, "ANALYSIS_CACHE_UNAVAILABLE", "Analysis caching is unavailable."
        ) from None
