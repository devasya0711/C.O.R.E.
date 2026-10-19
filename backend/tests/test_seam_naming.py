"""Structured naming validation and provider failure behavior, HTTP boundary only."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.schemas.analysis import Community
from app.services.seam_naming import SeamNamingService
from pydantic import SecretStr


def communities() -> list[Community]:
    return [
        Community(
            community_id=index,
            node_ids=[f"module{index}.execute"],
            cohesion_score=0.9,
            coupling_score=0.1,
            shared_db_tables=["ledger"],
        )
        for index in range(2)
    ]


def envelope(names: object, **message: object) -> dict[str, object]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps({"names": names}),
                    **message,
                },
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3},
    }


def valid_names() -> list[dict[str, object]]:
    return [
        {"community_id": 0, "suggested_name": "payment-service"},
        {"community_id": 1, "suggested_name": "order-service"},
    ]


@pytest.mark.asyncio
async def test_naming_uses_strict_schema_and_returns_validated_names() -> None:
    def provider(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/chat/completions"
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] and schema["schema"]["additionalProperties"] is False
        assert request.headers["Authorization"] == "Bearer fixture-key"
        assert body["model"] == "gpt-4o"
        return httpx.Response(200, json=envelope(list(reversed(valid_names()))))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        service = SeamNamingService(
            client, Settings(_env_file=None, openai_api_key=SecretStr("fixture-key"))
        )
        assert await service.suggest(communities()) == {
            0: "payment-service",
            1: "order-service",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names",
    [
        [],
        valid_names()[:1],
        valid_names() + valid_names()[:1],
        [{"community_id": 5, "suggested_name": "payment-service"}, valid_names()[1]],
        [{"community_id": 0, "suggested_name": "../escape-service"}, valid_names()[1]],
        [{"community_id": True, "suggested_name": "payment-service"}, valid_names()[1]],
        [{"community_id": 0, "suggested_name": "order-service"}, valid_names()[1]],
        [
            {
                "community_id": 0,
                "suggested_name": "payment-service",
                "node_ids": ["injected"],
            },
            valid_names()[1],
        ],
    ],
)
async def test_naming_rejects_incomplete_duplicate_or_unsafe_output(
    names: object,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=envelope(names))
        )
    ) as client:
        service = SeamNamingService(
            client, Settings(_env_file=None, openai_api_key=SecretStr("fixture-key"))
        )
        with pytest.raises(ApiError) as error:
            await service.suggest(communities())
        assert error.value.code == "SEAM_NAMING_RESPONSE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"choices": [{"finish_reason": "stop", "message": None}]},
        {"choices": [None]},
        {"choices": []},
        envelope(valid_names(), refusal="cannot name"),
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}]},
    ],
)
async def test_naming_rejects_refused_or_truncated_provider_response(
    payload: object,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        service = SeamNamingService(
            client, Settings(_env_file=None, openai_api_key=SecretStr("fixture-key"))
        )
        with pytest.raises(ApiError, match="invalid structured output"):
            await service.suggest(communities())


@pytest.mark.asyncio
async def test_naming_missing_key_and_empty_input_make_no_http_request() -> None:
    def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("No provider request expected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        service = SeamNamingService(client, Settings(_env_file=None))
        assert await service.suggest([]) == {}
        with pytest.raises(ApiError) as error:
            await service.suggest(communities())
        assert error.value.code == "LLM_NOT_CONFIGURED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, calls_expected", [(429, 4), (503, 4), (401, 1), (302, 1)]
)
async def test_naming_backoff_is_bounded_and_errors_are_redacted(
    status: int,
    calls_expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def provider(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            text="private-provider-body",
            headers={"Location": "https://example.test"},
        )

    sleep = AsyncMock()
    monkeypatch.setattr("app.services.seam_naming.asyncio.sleep", sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        service = SeamNamingService(
            client, Settings(_env_file=None, openai_api_key=SecretStr("fixture-key"))
        )
        with pytest.raises(ApiError) as error:
            await service.suggest(communities())
    assert calls == calls_expected
    assert sleep.await_count == calls_expected - 1
    assert "private-provider-body" not in str(error.value)


@pytest.mark.asyncio
async def test_naming_success_after_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def provider(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            httpx.Response(503)
            if calls == 1
            else httpx.Response(200, json=envelope(valid_names()))
        )

    monkeypatch.setattr("app.services.seam_naming.asyncio.sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        service = SeamNamingService(
            client, Settings(_env_file=None, openai_api_key=SecretStr("fixture-key"))
        )
        assert await service.suggest(communities()) == {
            0: "payment-service",
            1: "order-service",
        }
        assert calls == 2
