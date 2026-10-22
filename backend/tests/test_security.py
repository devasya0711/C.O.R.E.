"""Token encryption, input redaction, and secret configuration boundaries."""

import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.redaction import SecretRedactor
from app.core.security import TokenCipher
from app.schemas.settings import GitHubTokenRequest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError


def test_cipher_encrypts_randomly_and_authenticates_ciphertext() -> None:
    cipher = TokenCipher(SecretStr(Fernet.generate_key().decode()))
    token = SecretStr("only-a-test-credential")
    first, second = cipher.encrypt(token), cipher.encrypt(token)
    assert first != second
    assert token.get_secret_value() not in first
    assert cipher.decrypt(first) == token
    assert token.get_secret_value() not in repr(cipher.decrypt(first))
    wrong_key = TokenCipher(SecretStr(Fernet.generate_key().decode()))
    with pytest.raises(ApiError, match="credential is unavailable"):
        wrong_key.decrypt(first)
    with pytest.raises(ApiError):
        cipher.decrypt("tampered-ciphertext")


@pytest.mark.parametrize("key", [None, SecretStr("not-a-key"), SecretStr("λ")])
def test_invalid_encryption_configuration_fails_closed(key: SecretStr | None) -> None:
    with pytest.raises(ApiError) as caught:
        TokenCipher(key)
    assert caught.value.status_code == 503
    assert caught.value.code == "ENCRYPTION_NOT_CONFIGURED"


@pytest.mark.parametrize("token", ["", " ", "secret\nvalue", "x" * 4097])
def test_token_schema_rejects_invalid_credentials(token: str) -> None:
    with pytest.raises(ValidationError):
        GitHubTokenRequest(token=token)


def test_token_schema_does_not_repr_plaintext() -> None:
    model = GitHubTokenRequest(token="test-credential-only")
    assert "test-credential-only" not in repr(model)
    assert "test-credential-only" not in model.model_dump_json()


@pytest.mark.parametrize(
    "origin", ["*", "https://good.test/path", "https://u:p@good.test"]
)
def test_cors_configuration_rejects_wildcard_and_non_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


@pytest.mark.parametrize(
    "url", ["http://clerk.test/keys", "https://u:p@clerk.test/keys"]
)
def test_jwks_requires_https_without_embedded_credentials(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, clerk_jwks_url=url)


def test_secret_redactor_covers_settings_and_url_passwords() -> None:
    api_key = "configured-openai-secret"
    database_password = "db/password-value"
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr(api_key),
        database_url=(
            "postgresql+asyncpg://core:db%2Fpassword-value@localhost:5432/core"
        ),
    )
    redactor = SecretRedactor.from_settings(settings)
    text = f"api={api_key} raw={database_password} encoded=db%2Fpassword-value"

    redacted = redactor.redact(text)

    assert api_key not in redacted
    assert database_password not in redacted
    assert "db%2Fpassword-value" not in redacted
    assert repr(redactor) == "SecretRedactor(<configured>)"
