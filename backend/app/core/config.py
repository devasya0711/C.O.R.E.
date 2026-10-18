"""Typed application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by the API and service probes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "C.O.R.E. API"
    environment: str = "development"
    database_url: str | None = Field(default=None, repr=False)
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: SecretStr | None = None
    redis_url: str | None = Field(default=None, repr=False)
    health_probe_timeout_seconds: float = Field(default=1.5, gt=0, le=30)
    clerk_jwks_url: str | None = None
    clerk_issuer: str | None = None
    clerk_audience: str | None = None
    clerk_secret_key: SecretStr | None = None
    clerk_authorized_parties: list[str] = Field(default_factory=list)
    encryption_secret_key: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator(
        "database_url",
        "redis_url",
        "neo4j_password",
        "clerk_jwks_url",
        "clerk_issuer",
        "clerk_audience",
        "clerk_secret_key",
        "encryption_secret_key",
        mode="before",
    )
    @classmethod
    def empty_optional_setting(cls, value: object) -> object:
        """Empty .env.example values mean unconfigured, not a usable credential."""
        return None if value == "" else value

    @field_validator("clerk_jwks_url")
    @classmethod
    def validate_jwks_url(cls, value: str | None) -> str | None:
        """Only fetch signing keys over authenticated HTTPS, from config."""
        from urllib.parse import urlsplit

        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("CLERK_JWKS_URL must be an HTTPS URL without credentials")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, value: list[str]) -> list[str]:
        """Do not allow wildcard origins on credential-bearing requests."""
        from urllib.parse import urlsplit

        for origin in value:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("CORS_ORIGINS must contain explicit HTTP(S) origins")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings snapshot."""

    return Settings()
