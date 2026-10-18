"""Real RSA signing/verification against a test-only HTTP JWKS boundary."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import jwt
import pytest
from app.core.auth import ClerkVerifier
from app.core.config import Settings
from app.core.errors import ApiError
from cryptography.hazmat.primitives.asymmetric import rsa

from auth_helpers import ISSUER, jwks_response


@pytest.mark.asyncio
async def test_valid_rsa_session_and_cached_jwks(
    signing_key: rsa.RSAPrivateKey, sign: Callable[..., str]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url == f"{ISSUER}/.well-known/jwks.json"
        return httpx.Response(200, json=jwks_response(signing_key))

    settings = Settings(
        _env_file=None, clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = ClerkVerifier(settings, client)
        for _ in range(2):
            assert (await verifier.verify(sign())).sub == "user_alice"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://untrusted.test"},
        {"exp": 1},
        {"nbf": 9999999999},
        {"iat": 9999999999},
        {"sub": "not-a-clerk-subject"},
        {"azp": "https://untrusted.test"},
    ],
)
async def test_rejects_invalid_signed_claims(
    signing_key: rsa.RSAPrivateKey,
    sign: Callable[..., str],
    overrides: dict[str, object],
) -> None:
    settings = Settings(
        _env_file=None,
        clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json",
        clerk_authorized_parties=["http://localhost:3000"],
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=jwks_response(signing_key))
        )
    ) as client:
        with pytest.raises(ApiError) as caught:
            await ClerkVerifier(settings, client).verify(sign(**overrides))
    assert caught.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["invalid", "", "a.b.c"])
async def test_malformed_token_is_unauthorized(token: str) -> None:
    async with httpx.AsyncClient() as client:
        settings = Settings(
            _env_file=None, clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json"
        )
        with pytest.raises(ApiError) as caught:
            await ClerkVerifier(settings, client).verify(token)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_algorithm_confusion_is_rejected() -> None:
    token = jwt.encode({"sub": "user_alice"}, "test-only" * 8, algorithm="HS256")
    async with httpx.AsyncClient() as client:
        settings = Settings(
            _env_file=None, clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json"
        )
        with pytest.raises(ApiError) as caught:
            await ClerkVerifier(settings, client).verify(token)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_jwks_outage_is_explicit_service_error(sign: Callable[..., str]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as client:
        settings = Settings(
            _env_file=None, clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json"
        )
        with pytest.raises(ApiError) as caught:
            await ClerkVerifier(settings, client).verify(sign())
    assert caught.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["sub", "iss", "exp", "iat", "nbf"])
async def test_required_claims_cannot_be_omitted(
    signing_key: rsa.RSAPrivateKey, sign: Callable[..., str], missing: str
) -> None:
    payload = jwt.decode(sign(), options={"verify_signature": False})
    del payload[missing]
    token = jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": "test"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=jwks_response(signing_key))
        )
    ) as client:
        settings = Settings(
            _env_file=None, clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json"
        )
        with pytest.raises(ApiError) as caught:
            await ClerkVerifier(settings, client).verify(token)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_signature_and_unknown_kid_are_rejected(
    signing_key: rsa.RSAPrivateKey, sign: Callable[..., str]
) -> None:
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = jwt.decode(sign(), options={"verify_signature": False})
    tokens = [
        jwt.encode(payload, wrong, algorithm="RS256", headers={"kid": "test"}),
        jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": "unknown"}),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=jwks_response(signing_key))
        )
    ) as client:
        settings = Settings(
            _env_file=None, clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json"
        )
        for token in tokens:
            with pytest.raises(ApiError) as caught:
                await ClerkVerifier(settings, client).verify(token)
            assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_configured_audience_is_enforced(
    signing_key: rsa.RSAPrivateKey, sign: Callable[..., str]
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=jwks_response(signing_key))
        )
    ) as client:
        settings = Settings(
            _env_file=None,
            clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json",
            clerk_audience="core-api",
        )
        verifier = ClerkVerifier(settings, client)
        with pytest.raises(ApiError):
            await verifier.verify(sign(aud="wrong-api"))
        assert (await verifier.verify(sign(aud="core-api"))).sub == "user_alice"
