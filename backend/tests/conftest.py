"""Generated signing keys and JWT fixtures; no live credentials are stored."""

import os
import time
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

import aiosqlite
import jwt
import pytest
import pytest_asyncio
from app.db.models import Base
from app.db.neo4j import initialize_schema
from app.db.redis import create_redis_client
from app.db.session import create_database
from cryptography.hazmat.primitives.asymmetric import rsa
from neo4j import AsyncDriver, AsyncGraphDatabase
from redis.asyncio import Redis
from sqlalchemy import event, text
from sqlalchemy.engine import AdaptedConnection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from auth_helpers import ISSUER


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def sign(signing_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def encode(**overrides: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "sub": "user_alice",
            "iss": ISSUER,
            "iat": now - 2,
            "nbf": now - 2,
            "exp": now + 120,
            "azp": "http://localhost:3000",
            "email": "alice@example.test",
        }
        claims.update(overrides)
        return jwt.encode(
            claims, signing_key, algorithm="RS256", headers={"kid": "test"}
        )

    return encode


@pytest_asyncio.fixture
async def sql_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Independent SQL connections in one isolated PostgreSQL schema per test."""
    url = os.environ.get("TEST_DATABASE_URL")
    engine, _ = create_database(url or "sqlite+aiosqlite:///:memory:")
    schema = f"api_test_{uuid4().hex}"
    if not url:

        async def configure(connection: aiosqlite.Connection) -> None:
            await connection.create_function("gen_random_uuid", 0, lambda: uuid4().hex)
            await connection.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(engine.sync_engine, "connect")
        def sqlite_settings(
            connection: AdaptedConnection, record: ConnectionPoolEntry
        ) -> None:
            connection.run_async(configure)

    try:
        if url:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            scoped = engine.execution_options(schema_translate_map={None: schema})
        else:
            scoped = engine
        async with scoped.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(scoped, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            if url:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            else:
                await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Real Redis tests require TEST_REDIS_URL")
    client = create_redis_client(url)
    try:
        assert await client.ping()
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def neo4j_driver() -> AsyncIterator[AsyncDriver]:
    uri = os.environ.get("TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Real Neo4j tests require TEST_NEO4J_URI")
    password = os.environ.get("TEST_NEO4J_PASSWORD")
    auth = (os.environ.get("TEST_NEO4J_USER", "neo4j"), password) if password else None
    async with AsyncGraphDatabase.driver(uri, auth=auth) as driver:
        await driver.verify_connectivity()
        await initialize_schema(driver)
        yield driver
