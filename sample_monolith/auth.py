"""Authentication and checkout authorization for the sample monolith."""

from __future__ import annotations

from dataclasses import dataclass

from .models import InMemoryDatabase, User, find_user


@dataclass(frozen=True)
class AuthenticationError(Exception):
    """Expected authentication failure returned by an API route."""

    code: str
    message: str

    def __str__(self) -> str:
        """Render a stable diagnostic for logs and API responses."""

        return self.message


def authenticate_request(
    authorization_header: str | None,
    db: InMemoryDatabase,
) -> User:
    """Resolve a bearer user reference without contacting an auth service.

    The fixture uses the explicit ``user:<id>`` format so tests are
    deterministic and do not contain credentials or require a network.
    """

    if authorization_header is None:
        raise AuthenticationError("AUTH_REQUIRED", "Authorization is required")

    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError("AUTH_INVALID", "Expected a bearer token")

    token = parts[1]
    if not token.startswith("user:"):
        raise AuthenticationError("AUTH_INVALID", "Unsupported user token format")

    user_id = token.removeprefix("user:")
    user = find_user(db, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("AUTH_INVALID", "User is not authorized")
    return user


def authorize_checkout(db: InMemoryDatabase, user_id: str) -> User:
    """Verify that an existing active user may create an order."""

    user = find_user(db, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("CHECKOUT_FORBIDDEN", "User cannot place orders")
    return user
