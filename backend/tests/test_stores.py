"""Real Neo4j 5/Redis 7 checks; never replace unavailable stores with mocks."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from app.core.errors import ApiError
from app.core.rate_limit import SLIDING_WINDOW_SCRIPT, enforce_rate_limit, rate_bucket
from app.db.models import User
from app.db.neo4j import initialize_schema
from app.db.redis import BLAST_RADIUS_TTL_SECONDS
from fastapi import FastAPI, Request
from neo4j import AsyncDriver
from neo4j.exceptions import ConstraintError
from redis.asyncio import Redis


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v1/repos", ("graph", 60)),
        ("POST", "/api/v1/repos", ("jobs", 10)),
        ("POST", "/api/v1/repos/id/migrations", ("jobs", 10)),
        ("POST", "/api/v1/repos/id/migrations/job/pr", ("jobs", 10)),
        ("POST", "/api/v1/repos/id/impact", ("graph", 60)),
    ],
)
def test_rate_limit_contract(method: str, path: str, expected: tuple[str, int]) -> None:
    assert rate_bucket(method, path) == expected


@pytest.mark.asyncio
async def test_atomic_redis_limit_and_expiry(redis_client: Redis) -> None:
    key = f"core:test:{uuid4()}"
    try:
        results = await asyncio.gather(
            *(
                redis_client.eval(
                    SLIDING_WINDOW_SCRIPT, 1, key, 60_000, 10, str(uuid4())
                )
                for _ in range(25)
            )
        )
        assert sum(result[0] == 1 for result in results) == 10
        assert 0 < await redis_client.pttl(key) <= 61_000
        assert all(1 <= result[1] <= 60 for result in results if result[0] == 0)
        seconds, microseconds = await redis_client.time()
        old = seconds * 1000 + microseconds // 1000 - 60_001
        members = await redis_client.zrange(key, 0, -1)
        await redis_client.zadd(key, dict.fromkeys(members, old))
        result = await redis_client.eval(
            SLIDING_WINDOW_SCRIPT, 1, key, 60_000, 10, str(uuid4())
        )
        assert result == [1, 9]
    finally:
        await redis_client.delete(key)


@pytest.mark.asyncio
async def test_user_isolation_and_rate_limit_http_error(redis_client: Redis) -> None:
    application = FastAPI()
    application.state.redis = redis_client
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/repos",
            "headers": [],
            "app": application,
        }
    )
    alice = User(id=uuid4(), clerk_id="user_alice", email="alice@example.test")
    bob = User(id=uuid4(), clerk_id="user_bob", email="bob@example.test")
    keys = [f"core:rate:{user.id}:graph" for user in [alice, bob]]
    try:
        for _ in range(60):
            await enforce_rate_limit(request, alice)
        with pytest.raises(ApiError) as caught:
            await enforce_rate_limit(request, alice)
        assert caught.value.status_code == 429
        assert int(caught.value.headers["Retry-After"]) > 0
        await enforce_rate_limit(request, bob)
    finally:
        await redis_client.delete(*keys)


@pytest.mark.asyncio
async def test_missing_redis_configuration_fails_closed() -> None:
    application = FastAPI()
    application.state.redis = None
    request = Request({"type": "http", "app": application})
    user = User(id=uuid4(), clerk_id="user_alice", email="alice@example.test")
    with pytest.raises(ApiError) as caught:
        await enforce_rate_limit(request, user)
    assert caught.value.status_code == 503
    assert caught.value.code == "RATE_LIMITER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_cache_ttl_and_pubsub(redis_client: Redis) -> None:
    key = f"core:test:{uuid4()}"
    channel = f"core:events:{uuid4()}"
    try:
        await redis_client.set(key, '{"risk_score": 42}', ex=BLAST_RADIUS_TTL_SECONDS)
        assert 0 < await redis_client.ttl(key) <= 300
        async with redis_client.pubsub() as subscription:
            await subscription.subscribe(channel)
            await subscription.get_message(timeout=1)
            await redis_client.publish(channel, "AST_PARSING")
            message = await subscription.get_message(
                ignore_subscribe_messages=True, timeout=2
            )
            assert message is not None and message["data"] == "AST_PARSING"
    finally:
        await redis_client.delete(key)


@pytest.mark.asyncio
async def test_neo4j_schema_is_idempotent_and_vector_dimensions(
    neo4j_driver: AsyncDriver,
) -> None:
    await initialize_schema(neo4j_driver)
    await initialize_schema(neo4j_driver)
    records, _, _ = await neo4j_driver.execute_query(
        "SHOW INDEXES YIELD name, options RETURN name, options"
    )
    indexes = {record["name"]: record["options"] for record in records}
    assert {"code_node_repo_idx", "code_node_qname_idx"} <= indexes.keys()
    vector = indexes["function_embedding_idx"]["indexConfig"]
    assert vector["vector.dimensions"] == 1536
    assert vector["vector.similarity_function"].lower() == "cosine"
    records, _, _ = await neo4j_driver.execute_query(
        "SHOW CONSTRAINTS YIELD name RETURN name"
    )
    assert "code_node_id_unique" in {record["name"] for record in records}


@pytest.mark.asyncio
async def test_neo4j_enforces_unique_node_id(neo4j_driver: AsyncDriver) -> None:
    await initialize_schema(neo4j_driver)
    repo_id = str(uuid4())
    node_id = f"{repo_id}:payments.execute"
    try:
        await neo4j_driver.execute_query(
            "CREATE (:CodeNode:Function {id: $id, repo_id: $repo_id})",
            id=node_id,
            repo_id=repo_id,
        )
        with pytest.raises(ConstraintError):
            await neo4j_driver.execute_query(
                "CREATE (:CodeNode:Function {id: $id, repo_id: $repo_id})",
                id=node_id,
                repo_id=repo_id,
            )
    finally:
        await neo4j_driver.execute_query(
            "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n", repo_id=repo_id
        )
