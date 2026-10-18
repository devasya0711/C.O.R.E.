"""Durable Redis-stream ingestion worker with tenant-scoped PostgreSQL state."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import suppress
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import TokenCipher
from app.db.models import IngestionJob, Repository, User
from app.schemas.repos import IngestionEvent, IngestionMessage
from app.services.embedding_service import EmbeddingService
from app.services.neo4j_service import Neo4jService
from app.services.parse_source import analyze_source
from app.services.repository_source import RepositorySource

QUEUE = "core:ingestion:jobs"
GROUP = "core-ingestion-v1"
_LOGGER = logging.getLogger(__name__)
_UNLOCK = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) else return 0 end"
)
_ACK_DELETE = (
    "redis.call('XACK', KEYS[1], ARGV[1], ARGV[2]); "
    "return redis.call('XDEL', KEYS[1], ARGV[2])"
)


async def _redis[T](operation: Awaitable[T] | T) -> T:
    """Redis's shared sync/async stubs need narrowing at this async-only boundary."""
    return await cast(Awaitable[T], operation)


def progress_key(message: IngestionMessage) -> str:
    return f"core:repos:{message.repo_id}:ingestion:{message.job_id}:events"


class IngestionService:
    """One bounded worker per API process; pending stream entries survive restart."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        graph: Neo4jService,
        source: RepositorySource,
        embeddings: EmbeddingService,
        settings: Settings,
        *,
        queue_name: str = QUEUE,
    ) -> None:
        self.factory = factory
        self.redis = redis
        self.graph = graph
        self.source = source
        self.embeddings = embeddings
        self.settings = settings
        self.queue = queue_name
        self.consumer = f"worker-{uuid4()}"
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        try:
            await self.redis.xgroup_create(self.queue, GROUP, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise
        self._task = asyncio.create_task(self._consume(), name="core-ingestion")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def enqueue(self, message: IngestionMessage) -> None:
        """Call only after the repository/job transaction has been committed."""
        try:
            await _redis(
                self.redis.xadd(self.queue, {"job": message.model_dump_json()})
            )
        except RedisError:
            raise ApiError(
                503,
                "INGESTION_QUEUE_UNAVAILABLE",
                "Repository ingestion is unavailable.",
            ) from None

    async def _consume(self) -> None:
        while True:
            try:
                # Hard timeout is <=240s; reclaim only after its 300s lease expires.
                claimed = await self.redis.xautoclaim(
                    self.queue, GROUP, self.consumer, 300_000, "0-0", count=1
                )
                pending = claimed[1]
                if pending:
                    for entry_id, fields in pending:
                        await self._dispatch(entry_id, fields)
                    continue
                batches = await self.redis.xreadgroup(
                    GROUP, self.consumer, {self.queue: ">"}, count=1, block=500
                )
                for _stream, entries in batches:
                    for entry_id, fields in entries:
                        await self._dispatch(entry_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Log a class, never queue payloads, URLs, source, or tokens.
                _LOGGER.warning(
                    "ingestion_worker_retry error_type=%s", type(error).__name__
                )
                await asyncio.sleep(1)

    async def _dispatch(self, entry_id: str, fields: dict[str, str]) -> None:
        try:
            message = IngestionMessage.model_validate_json(fields["job"])
        except (ValidationError, KeyError):
            await self._ack(entry_id)
            return
        if await self.process(message):
            await self._ack(entry_id)

    async def _ack(self, entry_id: str) -> None:
        await _redis(self.redis.eval(_ACK_DELETE, 1, self.queue, GROUP, entry_id))

    async def _rows(
        self, session: AsyncSession, message: IngestionMessage
    ) -> tuple[IngestionJob, Repository] | None:
        row = (
            await session.execute(
                select(IngestionJob, Repository)
                .join(Repository, Repository.id == IngestionJob.repo_id)
                .where(
                    Repository.user_id == message.user_id,
                    Repository.id == message.repo_id,
                    IngestionJob.id == message.job_id,
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def _progress(
        self,
        message: IngestionMessage,
        stage: str,
        progress: int,
        *,
        error: str | None = None,
        warnings: list[str] | None = None,
        commit_sha: str | None = None,
        counts: tuple[int, int] | None = None,
    ) -> None:
        status = (
            "FAILED" if error else "COMPLETED" if stage == "COMPLETED" else "RUNNING"
        )
        async with self.factory() as session, session.begin():
            rows = await self._rows(session, message)
            if rows is None:
                raise ApiError(
                    403, "RESOURCE_ACCESS_DENIED", "Repository access denied."
                )
            job, repository = rows
            job.stage, job.progress, job.status, job.error = (
                stage,
                progress,
                status,
                error,
            )
            now = datetime.now(UTC)
            if job.started_at is None:
                job.started_at = now
            job.updated_at = now
            repository.status = (
                "FAILED" if error else "READY" if status == "COMPLETED" else "INGESTING"
            )
            if status in {"COMPLETED", "FAILED"}:
                job.completed_at = now
            if commit_sha is not None:
                repository.last_commit_sha = commit_sha
            if counts is not None:
                repository.node_count, repository.edge_count = counts
        event = IngestionEvent(
            job_id=message.job_id,
            repo_id=message.repo_id,
            stage=stage,
            progress=progress,
            status=status,
            error=error,
            warnings=(warnings or [])[:500],
        )
        key = progress_key(message)
        await _redis(
            self.redis.xadd(
                key, {"event": event.model_dump_json()}, maxlen=100, approximate=False
            )
        )
        await self.redis.expire(key, 86400)
        await _redis(self.redis.publish(key, event.model_dump_json()))

    async def process(self, message: IngestionMessage) -> bool:
        """Process/ack a committed job, with a per-repository distributed lease."""
        key, token = f"core:ingestion:lease:{message.repo_id}", str(uuid4())
        acquired = await _redis(self.redis.set(key, token, nx=True, ex=300))
        if not acquired:
            # Never acknowledge a competing/new job merely because another job
            # holds the repository lease. Pending entries are reclaimed later.
            return False
        stage, progress = "CLONING", 5
        try:
            async with self.factory() as session:
                rows = await self._rows(session, message)
                if rows is None or rows[0].status in {"FAILED", "COMPLETED"}:
                    return True
                _job, repository = rows
                repo_url, branch = repository.repo_url, repository.default_branch
                user = await session.scalar(
                    select(User).where(User.id == message.user_id)
                )
                if user is None:
                    return True
                encrypted_pat = user.encrypted_github_pat
            pat = (
                TokenCipher(self.settings.encryption_secret_key).decrypt(encrypted_pat)
                if encrypted_pat and repo_url != "local://sample_monolith"
                else None
            )
            async with asyncio.timeout(self.settings.ingestion_timeout_seconds):
                await self._progress(message, stage, progress)
                async with self.source.acquire(repo_url, branch, pat) as checkout:
                    stage, progress = "AST_PARSING", 25
                    await self._progress(message, stage, progress)
                    graph = await analyze_source(checkout.path)
                    if not graph.nodes:
                        raise ApiError(
                            422, "NO_PYTHON_SOURCE", "No Python source was found."
                        )
                    stage, progress = "VECTOR_EMBEDDING", 55
                    await self._progress(
                        message, stage, progress, warnings=graph.warnings
                    )
                    functions = [
                        node for node in graph.nodes if node.label == "Function"
                    ]
                    if functions:
                        vectors = await self.embeddings.embed_texts(
                            [
                                f"{node.docstring}\n{node.source_code}"
                                for node in functions
                            ]
                        )
                        for node, vector in zip(functions, vectors, strict=True):
                            node.embedding = vector
                    stage, progress = "GRAPH_INDEXING", 80
                    await self._progress(message, stage, progress)
                    await self.graph.upsert_graph(message.repo_id, graph)
                    await self._progress(
                        message,
                        "COMPLETED",
                        100,
                        commit_sha=checkout.commit_sha,
                        counts=(len(graph.nodes), len(graph.edges)),
                        warnings=graph.warnings,
                    )
            return True
        except asyncio.CancelledError:
            # Leave the durable queue entry pending; a replacement worker reclaims it.
            raise
        except Exception as error:
            if isinstance(error, ApiError):
                code, detail = error.code, error.message
            elif isinstance(error, TimeoutError):
                code, detail = "INGESTION_TIMEOUT", "Repository ingestion timed out."
            else:
                code, detail = "INGESTION_FAILED", "Repository ingestion failed."
            _LOGGER.warning(
                "ingestion_failed code=%s error_type=%s", code, type(error).__name__
            )
            await self._progress(message, stage, progress, error=f"{code}: {detail}")
            return True
        finally:
            with suppress(RedisError):
                await _redis(self.redis.eval(_UNLOCK, 1, key, token))
