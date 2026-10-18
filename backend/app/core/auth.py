"""Async Clerk JWKS verification and server-derived workspace identity."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, cast
from urllib.parse import quote

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_session

bearer = HTTPBearer(auto_error=False)


class SessionClaims(BaseModel):
    """Only verified JWT claims enter the provisioning path."""

    model_config = ConfigDict(extra="ignore", strict=True)
    sub: str = Field(min_length=1, max_length=128, pattern=r"^user_[A-Za-z0-9_-]+$")
    iss: str
    exp: int
    iat: int
    nbf: int
    azp: str | None = None
    email: str | None = Field(default=None, min_length=3, max_length=255)


def unauthorized() -> ApiError:
    """Use one non-diagnostic response for all invalid session tokens."""
    return ApiError(
        401,
        "UNAUTHORIZED",
        "A valid Clerk session token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


class ClerkVerifier:
    """Verify RS256 sessions against a bounded, asynchronously refreshed JWKS."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def _signing_key(self, kid: str) -> jwt.PyJWK:
        async with self._lock:
            age = time.monotonic() - self._fetched_at
            # Refresh periodically, or on key rotation. Throttle unknown kid spam.
            if not self._keys or age > 300 or (kid not in self._keys and age > 5):
                try:
                    response = await self.client.get(self.settings.clerk_jwks_url or "")
                    response.raise_for_status()
                    payload = response.json()
                    entries = payload.get("keys") if isinstance(payload, dict) else None
                    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
                        raise ValueError("Invalid signing-key set")
                    keys: dict[str, jwt.PyJWK] = {}
                    for entry in entries:
                        if (
                            not isinstance(entry, dict)
                            or entry.get("kty") != "RSA"
                            or entry.get("use", "sig") != "sig"
                            or entry.get("alg", "RS256") != "RS256"
                            or not isinstance(entry.get("kid"), str)
                        ):
                            continue
                        keys[entry["kid"]] = jwt.PyJWK.from_dict(
                            entry, algorithm="RS256"
                        )
                    if not keys:
                        raise ValueError("No RS256 signing keys")
                    self._keys = keys
                    self._fetched_at = time.monotonic()
                except (httpx.HTTPError, ValueError, jwt.PyJWTError) as error:
                    raise ApiError(
                        503,
                        "AUTH_PROVIDER_UNAVAILABLE",
                        "Authentication is unavailable.",
                    ) from error
            if kid not in self._keys:
                raise unauthorized()
            return self._keys[kid]

    async def verify(self, token: str) -> SessionClaims:
        """Reject unsigned tokens, wrong issuer/audience, and invalid timestamps."""
        if not self.settings.clerk_jwks_url:
            raise ApiError(503, "AUTH_NOT_CONFIGURED", "Authentication is unavailable.")
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (
                header.get("alg") != "RS256"
                or not isinstance(kid, str)
                or len(kid) > 128
            ):
                raise unauthorized()
            key = await self._signing_key(kid)
            issuer = self.settings.clerk_issuer or (
                self.settings.clerk_jwks_url.removesuffix("/.well-known/jwks.json")
            )
            payload = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=self.settings.clerk_audience,
                options={
                    "require": ["sub", "iss", "exp", "iat", "nbf"],
                    "verify_aud": self.settings.clerk_audience is not None,
                },
                leeway=5,
            )
            claims = SessionClaims.model_validate(payload)
            allowed = self.settings.clerk_authorized_parties
            if allowed and claims.azp not in allowed:
                raise unauthorized()
            return claims
        except (jwt.PyJWTError, ValidationError, ValueError, TypeError) as error:
            raise unauthorized() from error


async def get_verified_claims(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> SessionClaims:
    """Authenticate before a database dependency is evaluated."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    if len(credentials.credentials) > 16384:
        raise unauthorized()
    verifier = cast(ClerkVerifier, request.app.state.clerk_verifier)
    return await verifier.verify(credentials.credentials)


async def _provisioning_email(request: Request, claims: SessionClaims) -> str:
    """Use a signed custom email claim or fetch the real primary Clerk email."""
    if claims.email:
        email = claims.email
    else:
        settings = cast(Settings, request.app.state.settings)
        if settings.clerk_secret_key is None:
            raise ApiError(
                503, "IDENTITY_PROFILE_UNAVAILABLE", "User profile is unavailable."
            )
        client = cast(httpx.AsyncClient, request.app.state.http_client)
        secret = settings.clerk_secret_key.get_secret_value()
        try:
            response = await client.get(
                f"https://api.clerk.com/v1/users/{quote(claims.sub, safe='')}",
                headers={"Authorization": f"Bearer {secret}"},
            )
            response.raise_for_status()
            profile = response.json()
            primary = profile["primary_email_address_id"]
            email = next(
                address["email_address"]
                for address in profile["email_addresses"]
                if address["id"] == primary
            )
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            StopIteration,
        ) as error:
            raise ApiError(
                503, "IDENTITY_PROFILE_UNAVAILABLE", "User profile is unavailable."
            ) from error
    if not isinstance(email, str) or "@" not in email or len(email) > 255:
        raise ApiError(
            503, "IDENTITY_PROFILE_UNAVAILABLE", "User profile is unavailable."
        )
    return email


async def get_current_user(
    request: Request,
    claims: Annotated[SessionClaims, Depends(get_verified_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve/provision by verified subject, never by a client-provided user ID."""
    query = select(User).where(User.clerk_id == claims.sub)
    user = await session.scalar(query)
    if user is not None:
        return user
    email = await _provisioning_email(request, claims)
    user = User(clerk_id=claims.sub, email=email)
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError as error:
        # A concurrent request may have provisioned this subject. Never link
        # different subjects just because they claim the same email address.
        existing = await session.scalar(query)
        if existing is None:
            raise ApiError(
                409, "IDENTITY_CONFLICT", "User profile could not be linked."
            ) from error
        return existing
    return user
