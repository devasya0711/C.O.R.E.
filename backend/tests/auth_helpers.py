"""Non-secret helpers for the local, test-only Clerk HTTP boundary."""

import json

from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://clerk.example.test"


def jwks_response(signing_key: rsa.RSAPrivateKey) -> dict[str, object]:
    key = json.loads(RSAAlgorithm.to_jwk(signing_key.public_key()))
    key.update({"kid": "test", "alg": "RS256", "use": "sig"})
    return {"keys": [key]}
