"""Actual repository APIs, SQL/Redis/Neo4j, and a test-only OpenAI HTTP boundary."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from app.core.auth import ClerkVerifier
from app.core.config import Settings
from app.db.models import IngestionJob, Repository
from app.main import create_app
from app.schemas.repos import IngestionMessage
from app.services.embedding_service import EmbeddingService
from app.services.ingestion_service import GROUP, IngestionService, progress_key
from app.services.neo4j_service import Neo4jService
from app.services.repository_source import RepositorySource
from cryptography.hazmat.primitives.asymmetric import rsa
from neo4j import AsyncDriver
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auth_helpers import ISSUER, jwks_response

SAMPLE = Path(__file__).resolve().parents[2] / "sample_monolith"


@dataclass
class Runtime:
    client: httpx.AsyncClient
    worker: IngestionService
    factory: async_sessionmaker[AsyncSession]
    redis: Redis
    graph: Neo4jService


@pytest_asyncio.fixture
async def runtime(
    sql_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
    redis_client: Redis,
    signing_key: rsa.RSAPrivateKey,
) -> AsyncIterator[Runtime]:
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Repository worker integration requires real PostgreSQL")
    settings = Settings(
        _env_file=None,
        clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json",
        openai_api_key=SecretStr("test-provider-boundary-only"),
    )

    def provider(request: httpx.Request) -> httpx.Response:
        if request.url.host == "clerk.example.test":
            return httpx.Response(200, json=jwks_response(signing_key))
        assert request.url == "https://api.openai.com/v1/embeddings"
        body = json.loads(request.content)
        assert body["model"] == "text-embedding-3-small"
        assert body["dimensions"] == 1536
        # These vectors exist only in the external HTTP fixture, never in an
        # application fallback. Actual store writes/queries remain fully real.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [1.0] + [0.0] * 1535}
                    for index, _ in enumerate(body["input"])
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    application = create_app(settings)
    queue = f"core:test:ingestion:{uuid4()}"
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(transport=httpx.MockTransport(provider)) as external,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://api.test"
        ) as client,
    ):
        graph = Neo4jService(neo4j_driver)
        worker = IngestionService(
            sql_factory,
            redis_client,
            graph,
            RepositorySource(SAMPLE),
            EmbeddingService(external, settings.openai_api_key),
            settings,
            queue_name=queue,
        )
        application.state.session_factory = sql_factory
        application.state.neo4j = neo4j_driver
        application.state.redis = redis_client
        application.state.clerk_verifier = ClerkVerifier(settings, external)
        application.state.ingestion = worker
        try:
            yield Runtime(client, worker, sql_factory, redis_client, graph)
        finally:
            await worker.close()
            async with sql_factory() as session:
                repositories = list(await session.scalars(select(Repository)))
                jobs = list(await session.scalars(select(IngestionJob)))
            for repository in repositories:
                await neo4j_driver.execute_query(
                    "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n",
                    repo_id=str(repository.id),
                )
                await redis_client.delete(f"core:ingestion:lease:{repository.id}")
                await redis_client.delete(
                    f"core:rate:{repository.user_id}:jobs",
                    f"core:rate:{repository.user_id}:graph",
                )
            for job in jobs:
                await redis_client.delete(
                    f"core:repos:{job.repo_id}:ingestion:{job.id}:events"
                )
            await redis_client.delete(queue)


async def latest_message(runtime: Runtime) -> IngestionMessage:
    messages = await runtime.redis.xrange(runtime.worker.queue)
    return IngestionMessage.model_validate_json(messages[-1][1]["job"])


async def wait_ready(runtime: Runtime, repo_id: UUID) -> None:
    async with asyncio.timeout(20):
        while True:
            async with runtime.factory() as session:
                repository = await session.get(Repository, repo_id)
                assert repository is not None
                if repository.status == "FAILED":
                    jobs = list(await session.scalars(select(IngestionJob)))
                    pytest.fail(f"Test ingestion failed: {[job.error for job in jobs]}")
                if repository.status == "READY":
                    return
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_sample_ingestion_end_to_end_and_idempotent_reingestion(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    headers = {"Authorization": f"Bearer {sign()}"}
    payload = {"name": "sample", "use_sample_monolith": True}
    before = await runtime.client.get("/api/v1/repos", headers=headers)
    assert before.status_code == 200 and before.json() == []
    response = await runtime.client.post("/api/v1/repos", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "INGESTING"
    repo_id = UUID(response.json()["repo_id"])
    message = await latest_message(runtime)
    not_ready = await runtime.client.get(
        f"/api/v1/repos/{repo_id}/graph", headers=headers
    )
    assert not_ready.status_code == 409
    started = time.monotonic()
    assert await runtime.worker.process(message)
    assert time.monotonic() - started < 30
    response = await runtime.client.get("/api/v1/repos", headers=headers)
    summary = response.json()[0]
    assert summary["status"] == "READY", summary
    assert (summary["node_count"], summary["edge_count"]) == (73, 132)
    assert len(summary["last_commit_sha"]) == 64
    assert summary["ingestion"]["progress"] == 100
    response = await runtime.client.get(
        f"/api/v1/repos/{repo_id}/graph", headers=headers
    )
    assert response.status_code == 200, response.text
    graph = response.json()
    assert len(graph["nodes"]) == 73 and len(graph["edges"]) == 132
    assert all(node["data"]["embedding"] is None for node in graph["nodes"])
    stored = await runtime.graph.load_graph(repo_id, embeddings=True)
    functions = [node for node in stored.nodes if node.label == "Function"]
    assert len(functions) == 41
    assert all(
        node.embedding is not None and len(node.embedding) == 1536 for node in functions
    )
    events = await runtime.redis.xrange(progress_key(message))
    assert [json.loads(fields["event"])["stage"] for _, fields in events] == [
        "CLONING",
        "AST_PARSING",
        "VECTOR_EMBEDDING",
        "GRAPH_INDEXING",
        "COMPLETED",
    ]
    response = await runtime.client.post("/api/v1/repos", headers=headers, json=payload)
    assert response.json()["repo_id"] == str(repo_id)
    second = await latest_message(runtime)
    assert second.job_id != message.job_id
    assert await runtime.worker.process(second)
    assert len((await runtime.graph.load_graph(repo_id)).nodes) == 73
    assert len((await runtime.client.get("/api/v1/repos", headers=headers)).json()) == 1


@pytest.mark.asyncio
async def test_repository_routes_enforce_authentication_and_tenant_isolation(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    assert (await runtime.client.get("/api/v1/repos")).status_code == 401
    headers = {"Authorization": f"Bearer {sign()}"}
    response = await runtime.client.post(
        "/api/v1/repos",
        headers=headers,
        json={"name": "private", "use_sample_monolith": True},
    )
    repo_id = response.json()["repo_id"]
    other = {
        "Authorization": f"Bearer {sign(sub='user_bob', email='bob@example.test')}"
    }
    listed = await runtime.client.get("/api/v1/repos", headers=other)
    assert listed.status_code == 200 and listed.json() == []
    forbidden = await runtime.client.get(
        f"/api/v1/repos/{repo_id}/graph", headers=other
    )
    assert forbidden.status_code == 403
    unknown = await runtime.client.get(f"/api/v1/repos/{uuid4()}/graph", headers=other)
    assert unknown.json() == forbidden.json()
    message = await latest_message(runtime)
    assert await runtime.worker.process(message.model_copy(update={"user_id": uuid4()}))
    assert not (await runtime.graph.load_graph(message.repo_id)).nodes


@pytest.mark.asyncio
async def test_missing_embedding_key_is_visible_failure_not_fake_vectors(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    headers = {"Authorization": f"Bearer {sign()}"}
    response = await runtime.client.post(
        "/api/v1/repos",
        headers=headers,
        json={"name": "sample", "use_sample_monolith": True},
    )
    runtime.worker.embeddings = EmbeddingService(
        runtime.worker.embeddings._client, None
    )
    message = await latest_message(runtime)
    assert await runtime.worker.process(message)
    listed = (await runtime.client.get("/api/v1/repos", headers=headers)).json()[0]
    assert listed["status"] == "FAILED"
    assert listed["ingestion"]["stage"] == "VECTOR_EMBEDDING"
    assert "EMBEDDING_NOT_CONFIGURED" in listed["ingestion"]["error"]
    assert "test-provider-boundary-only" not in json.dumps(listed)
    assert not (await runtime.graph.load_graph(message.repo_id)).nodes
    response = await runtime.client.get(
        f"/api/v1/repos/{message.repo_id}/graph", headers=headers
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_queue_consumer_processes_already_committed_job(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    response = await runtime.client.post(
        "/api/v1/repos",
        headers={"Authorization": f"Bearer {sign()}"},
        json={"name": "sample", "use_sample_monolith": True},
    )
    assert response.status_code == 202
    message = await latest_message(runtime)
    await runtime.worker.start()
    await wait_ready(runtime, message.repo_id)
    async with asyncio.timeout(5):
        while await runtime.redis.xlen(runtime.worker.queue):
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_consumer_reclaims_job_from_dead_worker(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    await runtime.client.post(
        "/api/v1/repos",
        headers={"Authorization": f"Bearer {sign()}"},
        json={"name": "sample", "use_sample_monolith": True},
    )
    message = await latest_message(runtime)
    await runtime.redis.xgroup_create(runtime.worker.queue, GROUP, id="0")
    entries = await runtime.redis.xreadgroup(
        GROUP, "dead-worker", {runtime.worker.queue: ">"}, count=1
    )
    entry_id = entries[0][1][0][0]
    await runtime.redis.xclaim(
        runtime.worker.queue, GROUP, "dead-worker", 0, [entry_id], idle=301_000
    )
    await runtime.worker.start()
    await wait_ready(runtime, message.repo_id)


@pytest.mark.asyncio
async def test_repeated_posts_share_a_pending_job(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    headers = {"Authorization": f"Bearer {sign()}"}
    payload = {"name": "sample", "use_sample_monolith": True}
    first = await runtime.client.post("/api/v1/repos", headers=headers, json=payload)
    message = await latest_message(runtime)
    responses = await asyncio.gather(
        *(
            runtime.client.post("/api/v1/repos", headers=headers, json=payload)
            for _ in range(3)
        )
    )
    assert all(response.status_code == 202 for response in responses)
    assert all(
        response.json()["repo_id"] == first.json()["repo_id"] for response in responses
    )
    assert (await latest_message(runtime)).job_id == message.job_id
    async with runtime.factory() as session:
        assert len(list(await session.scalars(select(Repository)))) == 1
        assert len(list(await session.scalars(select(IngestionJob)))) == 1


@pytest.mark.asyncio
async def test_queue_outage_keeps_one_retriable_durable_job(
    runtime: Runtime, sign: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = {"Authorization": f"Bearer {sign()}"}
    payload = {"name": "sample", "use_sample_monolith": True}
    with monkeypatch.context() as patch:
        patch.setattr(
            runtime.redis, "xadd", AsyncMock(side_effect=RedisConnectionError)
        )
        failed = await runtime.client.post(
            "/api/v1/repos", headers=headers, json=payload
        )
    assert failed.status_code == 503, failed.text
    assert failed.json()["error"]["code"] == "INGESTION_QUEUE_UNAVAILABLE"
    async with runtime.factory() as session:
        job = await session.scalar(select(IngestionJob))
        assert job is not None and job.status == "QUEUED"
        job_id = job.id
    retry = await runtime.client.post("/api/v1/repos", headers=headers, json=payload)
    assert retry.status_code == 202
    message = await latest_message(runtime)
    assert message.job_id == job_id
    assert await runtime.worker.process(message)


@pytest.mark.asyncio
async def test_competing_lease_never_acknowledges_an_unprocessed_job(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    await runtime.client.post(
        "/api/v1/repos",
        headers={"Authorization": f"Bearer {sign()}"},
        json={"name": "sample", "use_sample_monolith": True},
    )
    message = await latest_message(runtime)
    lease = f"core:ingestion:lease:{message.repo_id}"
    await runtime.redis.set(lease, "another-worker", ex=300)
    assert await runtime.worker.process(message) is False
    assert await runtime.redis.get(lease) == "another-worker"
    await runtime.redis.delete(lease)
    assert await runtime.worker.process(message)


@pytest.mark.asyncio
async def test_invalid_source_fails_at_ast_stage_without_a_graph(
    runtime: Runtime, sign: Callable[..., str], tmp_path: Path
) -> None:
    (tmp_path / "bad.py").write_text("def bad(:\n    pass\n")
    runtime.worker.source = RepositorySource(tmp_path)
    headers = {"Authorization": f"Bearer {sign()}"}
    await runtime.client.post(
        "/api/v1/repos",
        headers=headers,
        json={"name": "sample", "use_sample_monolith": True},
    )
    message = await latest_message(runtime)
    assert await runtime.worker.process(message)
    summary = (await runtime.client.get("/api/v1/repos", headers=headers)).json()[0]
    assert summary["status"] == "FAILED"
    assert summary["ingestion"]["stage"] == "AST_PARSING"
    assert "AST_PARSE_ERROR" in summary["ingestion"]["error"]
    assert not (await runtime.graph.load_graph(message.repo_id)).nodes


@pytest.mark.asyncio
async def test_ingestion_endpoint_enforces_ten_requests_per_minute(
    runtime: Runtime, sign: Callable[..., str]
) -> None:
    headers = {"Authorization": f"Bearer {sign()}"}
    payload = {"name": "sample", "use_sample_monolith": True}
    for _ in range(10):
        response = await runtime.client.post(
            "/api/v1/repos", headers=headers, json=payload
        )
        assert response.status_code == 202
    response = await runtime.client.post("/api/v1/repos", headers=headers, json=payload)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_five_hundred_file_ingestion_with_real_stores_and_provider_fixture(
    runtime: Runtime, sign: Callable[..., str], tmp_path: Path
) -> None:
    for index in range(500):
        (tmp_path / f"module_{index:03d}.py").write_text(
            f"def execute():\n    return {index}\n"
        )
    runtime.worker.source = RepositorySource(tmp_path)
    await runtime.client.post(
        "/api/v1/repos",
        headers={"Authorization": f"Bearer {sign()}"},
        json={"name": "bounded-500-file-fixture", "use_sample_monolith": True},
    )
    started = time.monotonic()
    message = await latest_message(runtime)
    assert await runtime.worker.process(message)
    async with runtime.factory() as session:
        repository = await session.get(Repository, message.repo_id)
        assert repository is not None
        assert repository.status == "READY"
        assert repository.node_count == 1000 and repository.edge_count == 500
    assert time.monotonic() - started < 90
