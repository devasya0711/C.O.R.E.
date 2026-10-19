"""Real-store analysis API tests; only Clerk/OpenAI HTTP are test boundaries."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from app.agents.static_analyzer import StaticAnalyzerAgent
from app.api.v1.analysis import impact_cache_key
from app.core.auth import ClerkVerifier
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import MigrationJob, Repository, SeamCluster, User
from app.main import create_app
from app.schemas.analysis import ImpactRequest
from app.schemas.graph import CodeGraph
from app.services.neo4j_service import Neo4jService
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from neo4j import AsyncDriver
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auth_helpers import ISSUER, jwks_response

SAMPLE = Path(__file__).resolve().parents[2] / "sample_monolith"
TARGET = "payments.PaymentProcessor.execute"


@dataclass
class AnalysisRuntime:
    client: httpx.AsyncClient
    app: FastAPI
    factory: async_sessionmaker[AsyncSession]
    redis: Redis
    graph: Neo4jService
    repo_id: UUID
    user_id: UUID
    headers: dict[str, str]
    naming_calls: list[httpx.Request]


@pytest_asyncio.fixture
async def analysis_runtime(
    sql_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
    redis_client: Redis,
    signing_key: rsa.RSAPrivateKey,
    sign: Callable[..., str],
) -> AsyncIterator[AnalysisRuntime]:
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Analysis API integration requires real PostgreSQL")
    settings = Settings(
        _env_file=None,
        clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json",
        openai_api_key=SecretStr("naming-http-fixture-only"),
    )
    calls: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        if request.url.host == "clerk.example.test":
            return httpx.Response(200, json=jwks_response(signing_key))
        assert request.url == "https://api.openai.com/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o"
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        communities = json.loads(body["messages"][1]["content"])
        assert all("source_code" not in item for item in communities)
        calls.append(request)
        names = [
            {
                "community_id": item["community_id"],
                "suggested_name": f"component-{item['community_id']}-service",
            }
            for item in communities
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"names": names})},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 10},
            },
        )

    application = create_app(settings)
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(transport=httpx.MockTransport(provider)) as external,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://api.test",
        ) as client,
    ):
        application.state.session_factory = sql_factory
        application.state.neo4j = neo4j_driver
        application.state.redis = redis_client
        application.state.http_client = external
        application.state.clerk_verifier = ClerkVerifier(settings, external)
        headers = {"Authorization": f"Bearer {sign()}"}
        assert (await client.get("/api/v1/repos", headers=headers)).status_code == 200
        async with sql_factory() as session, session.begin():
            user = await session.scalar(select(User))
            assert user is not None
            user_id = user.id
            repo = Repository(
                user_id=user.id,
                name="sample",
                repo_url="local://sample_monolith",
                default_branch="main",
                status="READY",
                last_commit_sha="a" * 64,
                node_count=73,
                edge_count=132,
            )
            session.add(repo)
            await session.flush()
            repo_id = repo.id
        graph = Neo4jService(neo4j_driver)
        await graph.upsert_graph(repo_id, StaticAnalyzerAgent().analyze(SAMPLE))
        try:
            yield AnalysisRuntime(
                client,
                application,
                sql_factory,
                redis_client,
                graph,
                repo_id,
                user_id,
                headers,
                calls,
            )
        finally:
            async with sql_factory() as session:
                owners = list(await session.scalars(select(User)))
                repos = list(await session.scalars(select(Repository)))
            for owned in repos:
                await neo4j_driver.execute_query(
                    "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n",
                    repo_id=str(owned.id),
                )
                for owner in owners:
                    async for key in redis_client.scan_iter(
                        match=f"core:impact:v1:{owner.id}:{owned.id}:*"
                    ):
                        await redis_client.delete(key)
            for owner in owners:
                await redis_client.delete(
                    f"core:rate:{owner.id}:graph", f"core:rate:{owner.id}:jobs"
                )


@pytest.mark.asyncio
async def test_seams_persist_and_annotate_graph_idempotently(
    analysis_runtime: AnalysisRuntime,
) -> None:
    rt = analysis_runtime
    url = f"/api/v1/repos/{rt.repo_id}/seams/analyze"
    response = await rt.client.post(url, headers=rt.headers)
    assert response.status_code == 200, response.text
    seams = response.json()["seams"]
    assert len(seams) > 1
    assert all(
        set(item)
        == {
            "id",
            "community_id",
            "suggested_name",
            "cohesion_score",
            "shared_db_tables",
            "node_ids",
        }
        for item in seams
    )
    assert any(item["shared_db_tables"] for item in seams)
    assert all(
        not table.startswith("db:") for s in seams for table in s["shared_db_tables"]
    )
    assert len(rt.naming_calls) == 1
    second = await rt.client.post(url, headers=rt.headers)
    assert second.json() == response.json()
    assert len(rt.naming_calls) == 1
    async with rt.factory() as session:
        saved = list(await session.scalars(select(SeamCluster)))
    assert len(saved) == len(seams)
    response = await rt.client.get(
        f"/api/v1/repos/{rt.repo_id}/graph", headers=rt.headers
    )
    assert response.status_code == 200
    nodes = response.json()["nodes"]
    assert all(
        n["data"]["community_id"] is not None
        for n in nodes
        if n["data"]["label"] != "DBTable"
    )
    conflicts = {name for s in seams for name in s["shared_db_tables"]}
    for node in nodes:
        data = node["data"]
        if data["label"] == "DBTable":
            assert data["shared_db_tables"] == (
                [data["name"]] if data["name"] in conflicts else []
            )


@pytest.mark.asyncio
async def test_impact_cache_has_ttl_and_is_invalidated_by_snapshot(
    analysis_runtime: AnalysisRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = analysis_runtime
    url = f"/api/v1/repos/{rt.repo_id}/impact"
    payload = ImpactRequest(target_qualified_name=TARGET)
    response = await rt.client.post(url, headers=rt.headers, json=payload.model_dump())
    assert response.status_code == 200, response.text
    assert "orders.place_order" in response.json()["affected_nodes"]
    assert "ledger_entries" in response.json()["affected_db_tables"]
    version = await rt.graph.snapshot_version(rt.repo_id)
    key = impact_cache_key(rt.user_id, rt.repo_id, version, payload)
    assert 295 <= await rt.redis.ttl(key) <= 300
    from app.agents.impact_predictor import ImpactPredictorAgent

    with monkeypatch.context() as context:
        context.setattr(
            ImpactPredictorAgent,
            "analyze",
            AsyncMock(side_effect=AssertionError("cache miss")),
        )
        cached = await rt.client.post(
            url, headers=rt.headers, json=payload.model_dump()
        )
        assert cached.json() == response.json()
    graph = await rt.graph.load_graph(rt.repo_id)
    graph.edges = [
        edge
        for edge in graph.edges
        if edge.source != "orders.place_order" or edge.target != TARGET
    ]
    await rt.graph.upsert_graph(rt.repo_id, graph)
    changed = await rt.client.post(url, headers=rt.headers, json=payload.model_dump())
    assert changed.status_code == 200
    assert "orders.place_order" not in changed.json()["affected_nodes"]
    assert changed.json() != response.json()
    assert await rt.redis.exists(key)  # Old key expires; it is never reused.


@pytest.mark.asyncio
async def test_analysis_enforces_auth_owner_readiness_and_validation(
    analysis_runtime: AnalysisRuntime,
    sign: Callable[..., str],
) -> None:
    rt = analysis_runtime
    other = {
        "Authorization": f"Bearer {sign(sub='user_bob', email='bob@example.test')}"
    }
    for suffix, payload in (
        ("seams/analyze", {}),
        ("impact", {"target_qualified_name": TARGET}),
    ):
        url = f"/api/v1/repos/{rt.repo_id}/{suffix}"
        assert (await rt.client.post(url, json=payload)).status_code == 401
        foreign = await rt.client.post(url, json=payload, headers=other)
        missing = await rt.client.post(
            f"/api/v1/repos/{uuid4()}/{suffix}", json=payload, headers=other
        )
        assert foreign.status_code == 403 and missing.json() == foreign.json()
    assert not rt.naming_calls
    url = f"/api/v1/repos/{rt.repo_id}/impact"
    for depth in (0, 6, -1, 1.5, True, "3"):
        response = await rt.client.post(
            url,
            headers=rt.headers,
            json={"target_qualified_name": TARGET, "max_hops": depth},
        )
        assert response.status_code == 422
    assert (
        await rt.client.post(
            url, headers=rt.headers, json={"target_qualified_name": " "}
        )
    ).status_code == 422
    assert (
        await rt.client.post(
            url, headers=rt.headers, json={"target_qualified_name": "absent.symbol"}
        )
    ).status_code == 404
    assert (
        await rt.client.post(
            url, headers=rt.headers, json={"target_qualified_name": "file:payments.py"}
        )
    ).status_code == 422
    async with rt.factory() as session, session.begin():
        repository = await session.get(Repository, rt.repo_id)
        assert repository is not None
        repository.status = "INGESTING"
    for suffix, payload in (
        ("seams/analyze", {}),
        ("impact", {"target_qualified_name": TARGET}),
    ):
        response = await rt.client.post(
            f"/api/v1/repos/{rt.repo_id}/{suffix}", headers=rt.headers, json=payload
        )
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_missing_llm_key_leaves_no_partial_seams(
    analysis_runtime: AnalysisRuntime,
) -> None:
    rt = analysis_runtime
    rt.app.state.settings.openai_api_key = None
    response = await rt.client.post(
        f"/api/v1/repos/{rt.repo_id}/seams/analyze", headers=rt.headers
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert not rt.naming_calls
    async with rt.factory() as session:
        assert list(await session.scalars(select(SeamCluster))) == []
    assert all(
        n.community_id is None for n in (await rt.graph.load_graph(rt.repo_id)).nodes
    )


@pytest.mark.asyncio
async def test_concurrent_seam_requests_share_persisted_names(
    analysis_runtime: AnalysisRuntime,
) -> None:
    rt = analysis_runtime
    results = await asyncio.gather(
        *[
            rt.client.post(
                f"/api/v1/repos/{rt.repo_id}/seams/analyze", headers=rt.headers
            )
            for _ in range(3)
        ]
    )
    assert all(response.status_code == 200 for response in results)
    assert all(response.json() == results[0].json() for response in results)
    assert len(rt.naming_calls) == 1


@pytest.mark.asyncio
async def test_reanalysis_preserves_migration_history_and_hides_stale_metadata(
    analysis_runtime: AnalysisRuntime,
) -> None:
    rt = analysis_runtime
    url = f"/api/v1/repos/{rt.repo_id}/seams/analyze"
    original = await rt.client.post(url, headers=rt.headers)
    assert original.status_code == 200
    original_seam = original.json()["seams"][0]
    async with rt.factory() as session, session.begin():
        job = MigrationJob(repo_id=rt.repo_id, seam_id=UUID(original_seam["id"]))
        session.add(job)
        await session.flush()
        job_id = job.id
    graph = await rt.graph.load_graph(rt.repo_id)
    await rt.graph.upsert_graph(rt.repo_id, CodeGraph(nodes=graph.nodes, edges=[]))
    fresh = await rt.client.post(url, headers=rt.headers)
    assert fresh.status_code == 200, fresh.text
    async with rt.factory() as session:
        job = await session.get(MigrationJob, job_id)
        assert job is not None and str(job.seam_id) == original_seam["id"]
    response = await rt.client.get(
        f"/api/v1/repos/{rt.repo_id}/graph", headers=rt.headers
    )
    assert all(n["data"]["shared_db_tables"] == [] for n in response.json()["nodes"])


@pytest.mark.asyncio
async def test_snapshot_assignment_failure_rolls_back_graph_annotations(
    analysis_runtime: AnalysisRuntime,
) -> None:
    rt = analysis_runtime
    graph = await rt.graph.load_graph(rt.repo_id)
    with pytest.raises(ApiError, match="Repository changed"):
        await rt.graph.set_communities(rt.repo_id, {TARGET: 10}, "stale-version")
    assert await rt.graph.load_graph(rt.repo_id) == graph
    version = await rt.graph.snapshot_version(rt.repo_id)
    assert version is not None
    with pytest.raises(ApiError):
        await rt.graph.set_communities(
            rt.repo_id, {TARGET: 10, "missing.symbol": 1}, version
        )
    assert await rt.graph.load_graph(rt.repo_id) == graph


@pytest.mark.asyncio
async def test_analysis_outages_are_safe_errors(
    analysis_runtime: AnalysisRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = analysis_runtime
    with monkeypatch.context() as context:
        context.setattr(
            rt.redis,
            "get",
            AsyncMock(side_effect=RedisConnectionError("private-error-text")),
        )
        response = await rt.client.post(
            f"/api/v1/repos/{rt.repo_id}/impact",
            headers=rt.headers,
            json={"target_qualified_name": TARGET},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ANALYSIS_CACHE_UNAVAILABLE"
        assert "private-error-text" not in response.text
    rt.app.state.neo4j = None
    response = await rt.client.post(
        f"/api/v1/repos/{rt.repo_id}/seams/analyze", headers=rt.headers
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_impact_uses_graph_rate_limit_even_for_cached_responses(
    analysis_runtime: AnalysisRuntime,
) -> None:
    rt = analysis_runtime
    await rt.redis.delete(f"core:rate:{rt.user_id}:graph")
    for _ in range(60):
        response = await rt.client.post(
            f"/api/v1/repos/{rt.repo_id}/impact",
            headers=rt.headers,
            json={"target_qualified_name": TARGET},
        )
        assert response.status_code == 200, response.text
    response = await rt.client.post(
        f"/api/v1/repos/{rt.repo_id}/impact",
        headers=rt.headers,
        json={"target_qualified_name": TARGET},
    )
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["not-json", "wrong-target"])
async def test_invalid_cache_entries_are_recomputed(
    analysis_runtime: AnalysisRuntime,
    corruption: str,
) -> None:
    rt = analysis_runtime
    payload = ImpactRequest(target_qualified_name=TARGET)
    url = f"/api/v1/repos/{rt.repo_id}/impact"
    original = await rt.client.post(url, headers=rt.headers, json=payload.model_dump())
    assert original.status_code == 200
    version = await rt.graph.snapshot_version(rt.repo_id)
    key = impact_cache_key(rt.user_id, rt.repo_id, version, payload)
    if corruption == "wrong-target":
        broken = original.json()
        broken["target"] = "wrong.symbol"
        corruption = json.dumps(broken)
    await rt.redis.set(key, corruption, ex=300)
    fresh = await rt.client.post(url, headers=rt.headers, json=payload.model_dump())
    assert fresh.json() == original.json()
    assert json.loads(await rt.redis.get(key) or "null") == original.json()


@pytest.mark.asyncio
async def test_waiting_analysis_refreshes_repository_readiness_after_lock(
    analysis_runtime: AnalysisRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import analysis

    rt = analysis_runtime
    entered = asyncio.Event()
    original_lock = analysis._lock_repository

    async def lock(
        session: AsyncSession, repository: Repository, *, shared: bool = False
    ) -> Repository:
        assert repository.status == "READY"  # Earlier, deliberately stale read.
        entered.set()
        return await original_lock(session, repository, shared=shared)

    monkeypatch.setattr(analysis, "_lock_repository", lock)
    async with rt.factory() as session, session.begin():
        repository = await session.scalar(
            select(Repository).where(Repository.id == rt.repo_id).with_for_update()
        )
        assert repository is not None
        repository.status = "INGESTING"
        await session.flush()
        pending = asyncio.create_task(
            rt.client.post(
                f"/api/v1/repos/{rt.repo_id}/impact",
                headers=rt.headers,
                json={"target_qualified_name": TARGET},
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
    response = await asyncio.wait_for(pending, timeout=5)
    assert response.status_code == 409
