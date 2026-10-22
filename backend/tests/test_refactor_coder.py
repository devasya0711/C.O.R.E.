"""Focused provider-boundary tests for the Phase 5 refactor coder."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from app.agents.refactor_coder import (
    MAX_ARTIFACT_BYTES,
    RefactorCoderAgent,
    RefactorCoderRequest,
)
from app.core.config import Settings
from app.core.errors import ApiError
from pydantic import SecretStr, ValidationError


def complete_artifacts() -> dict[str, str]:
    return {
        "service/main.py": (
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n"
            "@app.get('/health')\n"
            "def health() -> dict[str, str]:\n"
            "    return {'status': 'ok'}\n"
        ),
        "Dockerfile": "FROM python:3.12-slim\nCMD ['python', '-m', 'service.main']\n",
        "deploy/nginx.conf": (
            "location /payments/ {\n    proxy_pass http://payments_service;\n}\n"
        ),
        "adapter/monolith_adapter.py": (
            "def forward(request: object) -> object:\n    return request\n"
        ),
        "tests/test_service.py": ("def test_health() -> None:\n    assert True\n"),
        "requirements.txt": "fastapi>=0.115\n",
    }


def request(**overrides: object) -> RefactorCoderRequest:
    values: dict[str, object] = {
        "seam_name": "payments",
        "source_files": {"payments.py": "def pay() -> None:\n    pass\n"},
    }
    values.update(overrides)
    return RefactorCoderRequest.model_validate(values)


class FixtureProvider:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests: list[RefactorCoderRequest] = []

    def generate(self, value: RefactorCoderRequest) -> object:
        self.requests.append(value)
        return self.payload


@pytest.mark.asyncio
async def test_injected_structured_provider_returns_complete_artifact_map() -> None:
    provider = FixtureProvider({"artifacts": complete_artifacts()})

    result = await RefactorCoderAgent(provider, settings=Settings()).generate(request())

    assert result.attempt == 0
    assert result.artifacts == complete_artifacts()
    assert set(result.components) == {
        "fastapi_service",
        "dockerfile",
        "strangler_fig_proxy",
        "monolith_adapter",
        "tests",
        "support_files",
    }
    assert provider.requests[0].seam_name == "payments"


@pytest.mark.asyncio
async def test_no_provider_or_key_fails_without_fabricating_code() -> None:
    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(settings=Settings(openai_api_key=None)).generate(
            request()
        )

    assert caught.value.code == "REFACTOR_PROVIDER_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_provider_with_configured_key_but_no_boundary_is_unavailable() -> None:
    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(
            settings=Settings(openai_api_key=SecretStr("fixture-key"))
        ).generate(request())

    assert caught.value.code == "REFACTOR_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"artifacts": {"service.py": 1}},
        {"artifacts": "not-a-map"},
        {"artifacts": []},
    ],
)
async def test_malformed_provider_response_is_rejected(payload: object) -> None:
    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(
            FixtureProvider(payload), settings=Settings()
        ).generate(request())

    assert caught.value.code == "REFACTOR_PROVIDER_INVALID_RESPONSE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["../outside.py", "/absolute.py", "C:/outside.py", "service\\main.py"],
)
async def test_unsafe_provider_artifact_paths_are_rejected(path: str) -> None:
    artifacts = complete_artifacts()
    artifacts[path] = artifacts.pop("adapter/monolith_adapter.py")

    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(
            FixtureProvider({"artifacts": artifacts}), settings=Settings()
        ).generate(request())

    assert caught.value.code == "REFACTOR_ARTIFACT_INVALID"


@pytest.mark.asyncio
async def test_oversized_provider_artifact_is_rejected() -> None:
    artifacts = complete_artifacts()
    artifacts["adapter/monolith_adapter.py"] = "x" * (MAX_ARTIFACT_BYTES + 1)

    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(
            FixtureProvider({"artifacts": artifacts}), settings=Settings()
        ).generate(request())

    assert caught.value.code == "REFACTOR_ARTIFACT_INVALID"


@pytest.mark.asyncio
async def test_duplicate_provider_artifact_paths_are_rejected() -> None:
    entries = [
        {"path": "service/main.py", "content": "one"},
        {"path": "SERVICE/MAIN.py", "content": "two"},
    ]

    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(
            FixtureProvider({"artifacts": entries}), settings=Settings()
        ).generate(request())

    assert caught.value.code == "REFACTOR_PROVIDER_INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_missing_required_components_is_explicit() -> None:
    artifacts = {"service/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}

    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(
            FixtureProvider({"artifacts": artifacts}), settings=Settings()
        ).generate(request())

    assert caught.value.code == "REFACTOR_ARTIFACTS_INCOMPLETE"
    assert "dockerfile" in caught.value.details["missing_components"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content", "error_code", "missing_component"),
    [
        (
            "service/main.py",
            "from fastapi import FastAPI\napp = FastAPI(\n",
            "REFACTOR_ARTIFACT_INVALID",
            None,
        ),
        (
            "Dockerfile",
            "# FROM python:3.12-slim\nCMD ['python']\n",
            "REFACTOR_ARTIFACTS_INCOMPLETE",
            "dockerfile",
        ),
        (
            "tests/test_service.py",
            "'''def test_fake(): pass'''\n",
            "REFACTOR_ARTIFACTS_INCOMPLETE",
            "tests",
        ),
        (
            "service/main.py",
            "'''from fastapi import FastAPI\\napp = FastAPI()'''\n",
            "REFACTOR_ARTIFACTS_INCOMPLETE",
            "fastapi_service",
        ),
    ],
)
async def test_adversarial_artifacts_must_have_structural_components(
    path: str,
    content: str,
    error_code: str,
    missing_component: str | None,
) -> None:
    artifacts = complete_artifacts()
    artifacts[path] = content

    with pytest.raises(ApiError) as caught:
        await RefactorCoderAgent(
            FixtureProvider({"artifacts": artifacts}), settings=Settings()
        ).generate(request())

    assert caught.value.code == error_code
    if missing_component is not None:
        assert missing_component in caught.value.details["missing_components"]


@pytest.mark.asyncio
async def test_retry_passes_prior_sandbox_stderr_to_provider() -> None:
    provider = FixtureProvider(complete_artifacts())
    retry_stderr = "E   AssertionError: expected payment status\n"

    result = await RefactorCoderAgent(provider, settings=Settings()).generate(
        request(retry_count=1, prior_sandbox_stderr=retry_stderr)
    )

    assert result.attempt == 1
    assert provider.requests[0].prior_sandbox_stderr == retry_stderr


def test_retry_without_stderr_and_malformed_requests_fail_validation() -> None:
    with pytest.raises(ValidationError):
        RefactorCoderRequest.model_validate({"seam_name": "payments", "retry_count": 1})

    with pytest.raises(ApiError) as caught:
        RefactorCoderAgent()._request({"seam_name": "payments", "unknown": True})
    assert caught.value.code == "REFACTOR_INPUT_INVALID"


def test_provider_fixture_can_be_a_callable_mapping_boundary() -> None:
    calls: list[str] = []

    def provider(value: RefactorCoderRequest) -> Mapping[str, object]:
        calls.append(value.seam_name)
        return {"artifacts": complete_artifacts()}

    agent = RefactorCoderAgent(provider, settings=Settings())
    assert agent.provider is provider
    assert calls == []
