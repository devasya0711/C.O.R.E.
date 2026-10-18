"""Generated signing keys and JWT fixtures; no live credentials are stored."""

import time
from collections.abc import Callable

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

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
