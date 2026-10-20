"""Runtime service that assembles and supervises one migration workflow."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.crag_evaluator import CRAGEvaluatorAgent
from app.agents.refactor_coder import RefactorCoderAgent
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import TokenCipher
from app.db.models import MigrationJob, Repository, SeamCluster, User
from app.schemas.graph import CodeGraph
from app.schemas.migrations import (
    MigrationEventType,
    MigrationPRRequest,
    MigrationPRResponse,
)
from app.services.github_service import GitHubArtifact, GitHubPRRequest, GitHubService
from app.services.migration_events import (
    MigrationEventService,
    RedisStreamClient,
)
from app.services.neo4j_service import Neo4jService
from app.services.repository_source import RepositorySource
from app.services.sandbox_runner import SandboxRunnerConfig, SandboxRunnerService
from app.workflows.migration_graph import (
    MigrationDependencies,
    MigrationState,
    build_migration_graph,
)

_LOGGER = logging.getLogger(__name__)

_REPOSITORY_LEASE_TTL_SECONDS = 300
_REFRESH_REPOSITORY_LEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_REPOSITORY_LEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


async def _resolve_redis(value: object) -> object:
    if inspect.isawaitable(value):
        return await cast(Awaitable[object], value)
    return value


@dataclass(slots=True)
class _RepositoryLease:
    key: str
    token: str
    heartbeat: asyncio.Task[None]
    lost: asyncio.Event


class _CachedMigrationGraph:
    """Keep one context read while snapshot checks continue hitting Neo4j."""

    def __init__(
        self,
        service: Neo4jService,
        context: CodeGraph,
        seam_node_ids: set[str],
        factory: async_sessionmaker[AsyncSession],
        lease: _RepositoryLease,
    ) -> None:
        self.service = service
        self.factory = factory
        self.lease = lease
        selected = {
            node.qualified_name: node
            for node in context.nodes
            if node.qualified_name in seam_node_ids
        }
        self.context = CodeGraph(
            nodes=list(selected.values()),
            edges=[
                edge
                for edge in context.edges
                if edge.source in selected and edge.target in selected
            ],
            warnings=context.warnings,
        )

    async def retrieve_seam_context(self, repo_id: UUID, seam_id: UUID) -> CodeGraph:
        del repo_id, seam_id
        return self.context

    async def snapshot_version(self, repo_id: UUID) -> str | None:
        return await self.service.snapshot_version(repo_id)

    async def source_commit_sha(self, repo_id: UUID) -> str | None:
        async with self.factory() as session:
            repository = await session.get(Repository, repo_id)
            return repository.last_commit_sha if repository is not None else None

    async def lease_valid(self, repo_id: UUID) -> bool:
        del repo_id
        return not self.lease.lost.is_set()


def _safe_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:200]


def _migration_diagram(graph: CodeGraph, node_ids: set[str]) -> str:
    selected = {
        node.qualified_name: node
        for node in graph.nodes
        if node.qualified_name in node_ids
    }
    identifiers = {
        name: f"n{hashlib.sha256(name.encode()).hexdigest()[:10]}"
        for name in sorted(selected)
    }
    lines = ["graph TD"]
    for name in sorted(selected):
        lines.append(f'    {identifiers[name]}["{_safe_label(name)}"]')
    for edge in sorted(
        graph.edges, key=lambda item: (item.source, item.target, item.relationship)
    ):
        if edge.source in identifiers and edge.target in identifiers:
            lines.append(
                f"    {identifiers[edge.source]} -->|{edge.relationship}| "
                f"{identifiers[edge.target]}"
            )
    return "\n".join(lines)


def _blast_radius_summary(seam: SeamCluster) -> str:
    tables = ", ".join(seam.shared_db_tables) or "none"
    return (
        "| Metric | Value |\n|---|---:|\n"
        f"| Candidate symbols | {len(seam.node_ids)} |\n"
        f"| Cohesion score | {seam.cohesion_score:.3f} |\n"
        f"| Shared database tables | {tables} |\n"
        "| Sandbox verification | pending workflow result |"
    )


class MigrationService:
    """Create, run, and manually publish durable migration jobs."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        redis: RedisStreamClient,
        graph: Neo4jService,
        client: httpx.AsyncClient,
        settings: Settings,
        tasks: set[asyncio.Task[None]],
    ) -> None:
        self.factory = factory
        self.redis = redis
        self.graph = graph
        self.client = client
        self.settings = settings
        self.tasks = tasks

    def start(self, state: MigrationState) -> None:
        task = asyncio.create_task(
            self.run(state), name=f"core-migration-{state['job_id']}"
        )
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _acquire_repository_lease(self, repo_id: UUID) -> _RepositoryLease:
        setter = getattr(self.redis, "set", None)
        evaluator = getattr(self.redis, "eval", None)
        if not callable(setter) or not callable(evaluator):
            raise ApiError(
                503,
                "MIGRATION_UNAVAILABLE",
                "Repository migration coordination is unavailable.",
            )
        key = f"core:ingestion:lease:{repo_id}"
        token = str(uuid4())
        acquired = await _resolve_redis(
            cast(Callable[..., object], setter)(
                key, token, nx=True, ex=_REPOSITORY_LEASE_TTL_SECONDS
            )
        )
        if acquired is not True:
            raise ApiError(
                409,
                "REPOSITORY_BUSY",
                "Repository ingestion or another migration is in progress.",
            )
        lost = asyncio.Event()

        async def heartbeat() -> None:
            try:
                while True:
                    await asyncio.sleep(_REPOSITORY_LEASE_TTL_SECONDS // 3)
                    result = await _resolve_redis(
                        cast(Callable[..., object], evaluator)(
                            _REFRESH_REPOSITORY_LEASE,
                            1,
                            key,
                            token,
                            str(_REPOSITORY_LEASE_TTL_SECONDS),
                        )
                    )
                    if result != 1:
                        lost.set()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                lost.set()
                _LOGGER.warning("migration_lease_refresh_failed error_type=RedisError")

        heartbeat_task = asyncio.create_task(
            heartbeat(), name=f"core-migration-lease-{repo_id}"
        )
        return _RepositoryLease(
            key=key, token=token, heartbeat=heartbeat_task, lost=lost
        )

    async def _release_repository_lease(self, lease: _RepositoryLease) -> None:
        lease.heartbeat.cancel()
        await asyncio.gather(lease.heartbeat, return_exceptions=True)
        evaluator = getattr(self.redis, "eval", None)
        if callable(evaluator):
            try:
                await _resolve_redis(
                    cast(Callable[..., object], evaluator)(
                        _RELEASE_REPOSITORY_LEASE, 1, lease.key, lease.token
                    )
                )
            except Exception:
                _LOGGER.warning("migration_lease_release_failed error_type=RedisError")

    async def close(self) -> None:
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    async def resume_unfinished(self) -> None:
        """Requeue durable jobs left behind by a process restart."""
        async with self.factory() as session:
            jobs = list(
                await session.scalars(
                    select(MigrationJob).where(
                        MigrationJob.status.notin_(["COMPLETED", "FAILED"])
                    )
                )
            )
            states = [
                {
                    "job_id": job.id,
                    "repo_id": job.repo_id,
                    "seam_id": job.seam_id,
                    "create_github_pr": job.create_github_pr,
                    "expected_snapshot_version": job.snapshot_version or "",
                }
                for job in jobs
            ]
        for state in states:
            self.start(cast(MigrationState, state))

    async def run(self, state: MigrationState) -> None:
        lease: _RepositoryLease | None = None
        try:
            lease = await self._acquire_repository_lease(_uuid(state, "repo_id"))
            repository, seam, encrypted_pat, context = await self._metadata(state)
            source_files = {
                node.file_path: node.source_code
                for node in context.nodes
                if node.label == "File" and node.source_code
            }
            graph = _CachedMigrationGraph(
                self.graph,
                context,
                set(seam.node_ids),
                self.factory,
                lease,
            )
            event_service = MigrationEventService(self.redis)
            source_token = None
            if encrypted_pat and repository.repo_url != "local://sample_monolith":
                source_token = TokenCipher(self.settings.encryption_secret_key).decrypt(
                    encrypted_pat
                )
            source = RepositorySource(
                self.settings.sample_monolith_path,
                self.settings.clone_timeout_seconds,
                self.settings.ingestion_max_source_bytes,
            )
            async with source.acquire(
                repository.repo_url, repository.default_branch, source_token
            ) as checkout:
                expected_commit = repository.last_commit_sha
                if expected_commit and checkout.commit_sha != expected_commit:
                    raise ApiError(
                        409,
                        "REPOSITORY_SOURCE_CHANGED",
                        "Repository source changed; re-ingest before migrating.",
                    )
                github = GitHubService(
                    timeout_seconds=self.settings.github_timeout_seconds
                )
                sandbox = SandboxRunnerService(
                    SandboxRunnerConfig(
                        timeout_seconds=self.settings.sandbox_timeout_seconds,
                        max_output_bytes=self.settings.sandbox_max_output_bytes,
                        docker_executable=self.settings.sandbox_docker_executable,
                        docker_image=self.settings.sandbox_docker_image,
                    )
                ).with_source(
                    checkout.path,
                    subdirectory=(
                        "sample_monolith"
                        if repository.repo_url == "local://sample_monolith"
                        else None
                    ),
                )
                dependencies = MigrationDependencies(
                    graph=graph,
                    source_reader=source_files,
                    crag_evaluator=CRAGEvaluatorAgent(source_files),
                    coder=RefactorCoderAgent(
                        settings=self.settings,
                        client=self.client,
                    ),
                    sandbox=sandbox,
                    github_service=github,
                    session_factory=self.factory,
                    event_publisher=event_service,
                    github_request_factory=self._github_request_factory(
                        state, repository, seam, encrypted_pat
                    ),
                )
                initial = cast(MigrationState, dict(state))
                initial["graph_context"] = context
                initial["source_files"] = source_files
                initial["sandbox_source_files"] = source_files
                initial["seam_name"] = seam.suggested_name
                initial["seam_node_ids"] = list(seam.node_ids)
                initial["repo_url"] = repository.repo_url
                initial["base_branch"] = repository.default_branch
                initial["source_commit_sha"] = repository.last_commit_sha or ""
                initial["blast_radius_summary"] = _blast_radius_summary(seam)
                initial["dependency_diagram"] = _migration_diagram(
                    context, set(seam.node_ids)
                )
                expected_snapshot = state.get(
                    "expected_snapshot_version", state.get("snapshot_version", "")
                )
                initial["expected_snapshot_version"] = (
                    expected_snapshot if isinstance(expected_snapshot, str) else ""
                )
                await asyncio.wait_for(
                    build_migration_graph(dependencies).ainvoke(initial),
                    timeout=self.settings.migration_timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _LOGGER.warning("migration_failed error_type=%s", type(error).__name__)
            await self._fail(state, error)
        finally:
            if lease is not None:
                await self._release_repository_lease(lease)

    async def _metadata(
        self, state: MigrationState
    ) -> tuple[Repository, SeamCluster, str | None, CodeGraph]:
        async with self.factory() as session:
            repository = await session.get(Repository, _uuid(state, "repo_id"))
            seam = await session.get(SeamCluster, _uuid(state, "seam_id"))
            job = await session.get(MigrationJob, _uuid(state, "job_id"))
            if repository is None or seam is None or job is None:
                raise ApiError(
                    404, "RESOURCE_NOT_FOUND", "Migration job was not found."
                )
            if seam.repo_id != repository.id or job.repo_id != repository.id:
                raise ApiError(
                    403, "RESOURCE_ACCESS_DENIED", "Migration access denied."
                )
            if (
                job.snapshot_version is None
                or seam.snapshot_version != job.snapshot_version
            ):
                raise ApiError(
                    409,
                    "SEAM_SNAPSHOT_STALE",
                    "Re-run seam analysis before starting a migration.",
                )
            if (
                job.source_commit_sha is not None
                and job.source_commit_sha != repository.last_commit_sha
            ):
                raise ApiError(
                    409,
                    "REPOSITORY_SOURCE_CHANGED",
                    "Repository source changed; re-ingest before migrating.",
                )
            user = await session.get(User, repository.user_id)
            encrypted = user.encrypted_github_pat if user is not None else None
        context = await self.graph.load_graph(repository.id)
        current = await self.graph.snapshot_version(repository.id)
        if current is None or current != job.snapshot_version:
            raise ApiError(
                409,
                "REPOSITORY_SNAPSHOT_CHANGED",
                "Repository changed; rerun the migration.",
            )
        active = {node.qualified_name for node in context.nodes}
        if not set(seam.node_ids).issubset(active):
            raise ApiError(
                409,
                "SEAM_SNAPSHOT_STALE",
                "The selected seam is no longer present in the active graph.",
            )
        return repository, seam, encrypted, context

    def _github_request_factory(
        self,
        state: MigrationState,
        repository: Repository,
        seam: SeamCluster,
        encrypted_pat: str | None,
    ) -> Callable[[MigrationState], GitHubPRRequest]:
        def factory(current: MigrationState) -> GitHubPRRequest:
            if not encrypted_pat:
                raise ApiError(
                    401, "GITHUB_AUTH_FAILED", "GitHub authentication failed."
                )
            token = TokenCipher(self.settings.encryption_secret_key).decrypt(
                encrypted_pat
            )
            return GitHubPRRequest(
                repo_url=repository.repo_url,
                base_branch=repository.default_branch,
                seam_name=seam.suggested_name,
                artifacts=[
                    GitHubArtifact(path=path, content=content)
                    for path, content in dict(
                        current.get("generated_artifacts", {})
                    ).items()
                ],
                dependency_diagram=str(current.get("dependency_diagram", "graph TD")),
                blast_radius_summary=str(
                    current.get("blast_radius_summary", _blast_radius_summary(seam))
                ),
                token=token,
                source_commit_sha=(
                    current.get("source_commit_sha")
                    if isinstance(current.get("source_commit_sha"), str)
                    else None
                ),
            )

        del state
        return factory

    async def publish_job(
        self,
        job: MigrationJob,
        repository: Repository,
        seam: SeamCluster,
        user: User,
        payload: MigrationPRRequest,
    ) -> MigrationPRResponse:
        lease = await self._acquire_repository_lease(repository.id)
        try:
            return await self._publish_job_locked(job, repository, seam, user, payload)
        finally:
            await self._release_repository_lease(lease)

    async def _publish_job_locked(
        self,
        job: MigrationJob,
        repository: Repository,
        seam: SeamCluster,
        user: User,
        payload: MigrationPRRequest,
    ) -> MigrationPRResponse:
        if job.pr_url:
            return MigrationPRResponse(job_id=job.id, pr_url=job.pr_url)
        if job.status != "COMPLETED" or not job.sandbox_passed:
            raise ApiError(
                409,
                "MIGRATION_NOT_VERIFIED",
                "Migration artifacts are not sandbox-verified.",
            )
        if repository.status != "READY":
            raise ApiError(
                409, "REPOSITORY_NOT_READY", "Repository ingestion is not ready."
            )
        if job.source_commit_sha != repository.last_commit_sha:
            raise ApiError(
                409,
                "REPOSITORY_SOURCE_CHANGED",
                "Repository source changed; re-ingest before publishing.",
            )
        current = await self.graph.snapshot_version(repository.id)
        if not current or current != job.snapshot_version:
            raise ApiError(
                409,
                "REPOSITORY_SNAPSHOT_CHANGED",
                "Repository changed; rerun the migration.",
            )
        context = await self.graph.load_graph(repository.id)
        node_ids = set(seam.node_ids)
        if seam.snapshot_version != current or not node_ids.issubset(
            {node.qualified_name for node in context.nodes}
        ):
            raise ApiError(409, "SEAM_SNAPSHOT_STALE", "The selected seam is stale.")
        encrypted = user.encrypted_github_pat
        if not encrypted:
            raise ApiError(401, "GITHUB_AUTH_FAILED", "GitHub authentication failed.")
        request = GitHubPRRequest(
            repo_url=repository.repo_url,
            base_branch=payload.base_branch or repository.default_branch,
            seam_name=seam.suggested_name,
            artifacts=[
                GitHubArtifact(path=path, content=content)
                for path, content in job.generated_artifacts.items()
            ],
            dependency_diagram=_migration_diagram(context, node_ids),
            blast_radius_summary=_blast_radius_summary(seam),
            token=TokenCipher(self.settings.encryption_secret_key).decrypt(encrypted),
            title=payload.title,
            source_commit_sha=job.source_commit_sha,
        )
        result = await GitHubService(timeout_seconds=15).create_pull_request(request)
        async with self.factory() as session, session.begin():
            saved = await session.get(MigrationJob, job.id, with_for_update=True)
            if saved is None:
                raise ApiError(
                    404, "RESOURCE_NOT_FOUND", "Migration job was not found."
                )
            saved.pr_url = result.pr_url
        event_service = MigrationEventService(self.redis)
        try:
            await event_service.emit(
                job.id,
                repository.id,
                MigrationEventType.PR_CREATED,
                {"pr_number": result.pr_number, "branch": result.branch},
            )
        except Exception:
            _LOGGER.warning("migration_pr_event_failed error_type=RedisError")
        return MigrationPRResponse(
            job_id=job.id,
            pr_url=result.pr_url,
            pr_number=result.pr_number,
            branch=result.branch,
        )

    async def _fail(self, state: MigrationState, error: BaseException) -> None:
        job_id = _uuid(state, "job_id")
        repo_id = _uuid(state, "repo_id")
        async with self.factory() as session, session.begin():
            job = await session.get(MigrationJob, job_id)
            if job is not None and job.repo_id == repo_id:
                job.status = "FAILED"
        try:
            await MigrationEventService(self.redis).publish_failure(
                job_id,
                repo_id,
                error,
                error_code=(
                    error.code if isinstance(error, ApiError) else "MIGRATION_FAILED"
                ),
                stage=(
                    "MIGRATION_TIMEOUT"
                    if isinstance(error, (TimeoutError, asyncio.TimeoutError))
                    else "MIGRATION"
                ),
            )
        except Exception:
            _LOGGER.warning("migration_failure_event_failed error_type=RedisError")


def _uuid(state: MigrationState, key: str) -> UUID:
    value = state.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ApiError(422, "MIGRATION_INPUT_INVALID", "Migration identifiers are invalid.")


__all__ = ["MigrationService"]
