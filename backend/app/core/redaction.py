"""Central redaction of configured credentials from persisted text."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from pydantic import SecretStr

_REDACTED = "[REDACTED]"
_SECRET_FIELDS = (
    "database_url",
    "redis_url",
    "neo4j_uri",
    "neo4j_password",
    "clerk_secret_key",
    "encryption_secret_key",
    "openai_api_key",
)
_URL_FIELDS = {"database_url", "redis_url", "neo4j_uri"}


def _secret_text(value: object) -> str | None:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if isinstance(value, str) and value:
        return value
    return None


@dataclass(frozen=True, slots=True, repr=False, init=False)
class SecretRedactor:
    """Replace configured credential values without exposing them in ``repr``."""

    _secrets: tuple[str, ...]

    def __init__(self, secrets: Iterable[str | SecretStr] = ()) -> None:
        values: set[str] = set()
        for candidate in secrets:
            value = _secret_text(candidate)
            if value is not None:
                values.add(value)
        object.__setattr__(
            self, "_secrets", tuple(sorted(values, key=len, reverse=True))
        )

    def __repr__(self) -> str:
        return "SecretRedactor(<configured>)"

    @classmethod
    def from_settings(
        cls,
        settings: object | None,
        *,
        extra_secrets: Iterable[str | SecretStr] = (),
    ) -> SecretRedactor:
        """Build a redactor from application settings and transient credentials."""
        values: list[str | SecretStr] = list(extra_secrets)
        if settings is None:
            return cls(values)

        for field in _SECRET_FIELDS:
            candidate = getattr(settings, field, None)
            value = _secret_text(candidate)
            if value is None:
                continue
            values.append(value)
            if field in _URL_FIELDS:
                try:
                    parsed = urlsplit(value)
                    if parsed.password:
                        decoded = unquote(parsed.password)
                        values.extend((decoded, quote(decoded, safe="")))
                except ValueError:
                    continue
        return cls(values)

    def redact(self, text: str) -> str:
        """Replace every configured secret in a text log."""
        redacted = text
        for secret in self._secrets:
            redacted = redacted.replace(secret, _REDACTED)
        return redacted


__all__ = ["SecretRedactor"]
