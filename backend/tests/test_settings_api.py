"""Settings endpoint verification with actual SQL transactions and signed JWTs."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from app.api.dependencies import get_owned_repository
from app.core.auth import ClerkVerifier
from app.core.config import Settings
from app.core.rate_limit import enforce_rate_limit
from app.core.security import TokenCipher
from app.db.models import Base, Repository, User
from app.db.session import create_database
from app.main import create_app
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from pydantic import SecretStr
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auth_helpers import ISSUER, jwks_response


@pytest_asyncio.fixture
async def sql_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Use PostgreSQL when supplied, otherwise explicitly portable SQLite."""
    url = os.environ.get("TEST_DATABASE_URL")
    engine, _ = create_database(url or "sqlite+aiosqlite:///:memory:")
    if not url:

        @event.listens_for(engine.sync_engine, "connect")
        def sqlite_uuid(connection: object, record: object) -> None:
            connection.create_function("gen_random_uuid", 0, lambda: uuid4().hex)  # type: ignore[attr-defined]

    schema = f"settings_test_{uuid4().hex}"
    try:
        async with engine.connect() as connection:
            if url:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET search_path TO "{schema}"'))
            await connection.run_sync(Base.metadata.create_all)
            await connection.commit()
            yield async_sessionmaker(connection, expire_on_commit=False)
            await connection.rollback()
            if url:
                await connection.execute(text("SET search_path TO public"))
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            else:
                await connection.run_sync(Base.metadata.drop_all)
            await connection.commit()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api(
    sql_factory: async_sessionmaker[AsyncSession], signing_key: rsa.RSAPrivateKey
) -> AsyncIterator[tuple[httpx.AsyncClient, Settings]]:
    settings = Settings(
        _env_file=None,
        clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json",
        encryption_secret_key=SecretStr(Fernet.generate_key().decode()),
    )
    application = create_app(settings)

    async def no_limit() -> None:
        """Rate limits have separate real Redis tests; isolate endpoint I/O here."""

    application.dependency_overrides[enforce_rate_limit] = no_limit

    @application.get("/test/owned/{repo_id}")
    async def owned_route(
        repo_id: UUID,
        repository: Annotated[Repository, Depends(get_owned_repository)],
    ) -> dict[str, str]:
        return {"id": str(repository.id)}

    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=jwks_response(signing_key))
            )
        ) as keys_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://api.test"
        ) as client,
    ):
        application.state.session_factory = sql_factory
        application.state.clerk_verifier = ClerkVerifier(settings, keys_client)
        yield client, settings


@pytest.mark.asyncio
async def test_github_token_is_encrypted_and_never_echoed(
    api: tuple[httpx.AsyncClient, Settings],
    sign: Callable[..., str],
    sql_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, settings = api
    credential = "test-only-personal-access-token"
    response = await client.put(
        "/api/v1/settings/github-token",
        headers={"Authorization": f"Bearer {sign()}"},
        json={"token": credential},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "saved"}
    assert credential not in response.text
    async with sql_factory() as session:
        user = await session.scalar(select(User).where(User.clerk_id == "user_alice"))
        assert user is not None and user.encrypted_github_pat
        assert user.encrypted_github_pat != credential
        assert (
            TokenCipher(settings.encryption_secret_key)
            .decrypt(user.encrypted_github_pat)
            .get_secret_value()
        ) == credential


@pytest.mark.asyncio
async def test_invalid_jwt_and_validation_errors_do_not_leak_inputs(
    api: tuple[httpx.AsyncClient, Settings], sign: Callable[..., str]
) -> None:
    client, _ = api
    response = await client.put(
        "/api/v1/settings/github-token",
        headers={"Authorization": "Bearer invalid-private-input"},
        json={"token": "test-secret"},
    )
    assert response.status_code == 401
    assert "invalid-private-input" not in response.text
    response = await client.put(
        "/api/v1/settings/github-token",
        headers={"Authorization": f"Bearer {sign()}"},
        json={"token": "invalid test-secret"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "test-secret" not in response.text


@pytest.mark.asyncio
async def test_cross_tenant_repository_access_is_denied(
    api: tuple[httpx.AsyncClient, Settings],
    sign: Callable[..., str],
    sql_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with sql_factory() as session, session.begin():
        owner = User(clerk_id="user_bob", email="bob@example.test")
        repository = Repository(user=owner, name="private", repo_url="local://private")
        session.add(repository)
        await session.flush()
        repo_id = repository.id
    client, _ = api
    headers = {"Authorization": f"Bearer {sign()}"}
    for target in (repo_id, uuid4()):
        response = await client.get(f"/test/owned/{target}", headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "RESOURCE_ACCESS_DENIED"
    owner_response = await client.get(
        f"/test/owned/{repo_id}",
        headers={
            "Authorization": f"Bearer {sign(sub='user_bob', email='bob@example.test')}"
        },
    )
    assert owner_response.status_code == 200


@pytest.mark.asyncio
async def test_subjects_cannot_link_by_email(
    api: tuple[httpx.AsyncClient, Settings],
    sign: Callable[..., str],
    sql_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with sql_factory() as session, session.begin():
        session.add(User(clerk_id="user_bob", email="alice@example.test"))
    client, _ = api
    response = await client.put(
        "/api/v1/settings/github-token",
        headers={"Authorization": f"Bearer {sign()}"},
        json={"token": "test-only"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDENTITY_CONFLICT"


@pytest.mark.asyncio
async def test_clerk_primary_email_is_fetched_when_not_in_token(
    signing_key: rsa.RSAPrivateKey, sign: Callable[..., str]
) -> None:
    from app.core.auth import _provisioning_email
    from fastapi import Request

    settings = Settings(_env_file=None, clerk_secret_key=SecretStr("test-only"))
    application = create_app(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.clerk.com/v1/users/user_alice"
        assert request.headers["Authorization"] == "Bearer test-only"
        return httpx.Response(
            200,
            json={
                "primary_email_address_id": "email_primary",
                "email_addresses": [
                    {"id": "email_other", "email_address": "other@example.test"},
                    {"id": "email_primary", "email_address": "alice@example.test"},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        import jwt
        from app.core.auth import SessionClaims

        application.state.http_client = client
        request = Request({"type": "http", "app": application})
        # Test fixture creates the signed payload; the verifier is independently
        # covered above. Only exercise the profile HTTP boundary here.
        claims = SessionClaims.model_validate(
            jwt.decode(sign(email=None), options={"verify_signature": False})
        )
        assert await _provisioning_email(request, claims) == "alice@example.test"
