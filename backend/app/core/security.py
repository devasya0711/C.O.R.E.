"""Authenticated encryption for stored GitHub personal-access tokens."""

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.core.errors import ApiError


class TokenCipher:
    """Fernet encryption using the spec's base64-encoded 32-byte key."""

    def __init__(self, key: SecretStr | None) -> None:
        if key is None:
            raise ApiError(
                503, "ENCRYPTION_NOT_CONFIGURED", "Token storage is unavailable."
            )
        try:
            self._fernet = Fernet(key.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeError) as error:
            raise ApiError(
                503, "ENCRYPTION_NOT_CONFIGURED", "Token storage is unavailable."
            ) from error

    def encrypt(self, token: SecretStr) -> str:
        """Encrypt a token; never expose the plaintext in a representation."""
        return self._fernet.encrypt(token.get_secret_value().encode()).decode("ascii")

    def decrypt(self, ciphertext: str) -> SecretStr:
        """Verify authenticity before returning plaintext to an integration."""
        try:
            return SecretStr(self._fernet.decrypt(ciphertext.encode("ascii")).decode())
        except (InvalidToken, UnicodeError) as error:
            raise ApiError(
                503,
                "TOKEN_DECRYPTION_FAILED",
                "Stored GitHub credential is unavailable.",
            ) from error
