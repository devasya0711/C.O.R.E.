"""GitHub credential settings I/O; ciphertext is never part of a response."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class GitHubTokenRequest(BaseModel):
    """API.md defines the endpoint, but leaves its JSON field unspecified."""

    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        """Reject blank/whitespace credentials without returning their contents."""
        if any(character.isspace() for character in value.get_secret_value()):
            raise ValueError("Token must not contain whitespace")
        return value


class GitHubTokenResponse(BaseModel):
    """An acknowledgement only, with no plaintext or encrypted credential."""

    status: Literal["saved"] = "saved"
