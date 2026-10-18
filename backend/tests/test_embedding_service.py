"""Provider contract checks with test-only HTTP transports and no live API key."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import tiktoken
from app.core.errors import ApiError
from app.services.embedding_service import (
    EMBEDDING_DIMENSIONS,
    MAX_BATCH_INPUTS,
    MAX_BATCH_TOKENS,
    MAX_INPUT_TOKENS,
    EmbeddingService,
)
from pydantic import SecretStr

_KEY = "test-only-openai-credential"


def _response(values: list[float], reverse: bool = False) -> dict[str, object]:
    data = [
        {"index": index, "embedding": [value] * EMBEDDING_DIMENSIONS}
        for index, value in enumerate(values)
    ]
    return {
        "data": data[::-1] if reverse else data,
        "usage": {"prompt_tokens": 12, "total_tokens": 12},
    }


@pytest.fixture(autouse=True)
def no_retry_delays() -> Iterator[AsyncMock]:
    with patch(
        "app.services.embedding_service.asyncio.sleep", new_callable=AsyncMock
    ) as sleep:
        yield sleep


@pytest.mark.asyncio
async def test_real_request_shape_order_and_safe_usage_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inputs = ["def private_code(): return 1", "private docstring"]

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/embeddings"
        assert request.headers["authorization"] == f"Bearer {_KEY}"
        assert json.loads(request.content) == {
            "model": "text-embedding-3-small",
            "dimensions": 1536,
            "encoding_format": "float",
            "input": inputs,
        }
        return httpx.Response(200, json=_response([0.25, -0.5], reverse=True))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with caplog.at_level(logging.INFO, logger="app.services.embedding_service"):
            vectors = await EmbeddingService(client, SecretStr(_KEY)).embed_texts(
                inputs
            )
    assert vectors == [[0.25] * 1536, [-0.5] * 1536]
    assert "prompt_tokens=12 total_tokens=12" in caplog.text
    assert _KEY not in caplog.text
    assert all(text not in caplog.text for text in inputs)


@pytest.mark.asyncio
async def test_missing_key_and_empty_input() -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("No provider request is allowed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        for key in (None, SecretStr(""), SecretStr("  ")):
            with pytest.raises(ApiError) as caught:
                await EmbeddingService(client, key).embed_texts(["code"])
            assert caught.value.code == "EMBEDDING_NOT_CONFIGURED"
        assert await EmbeddingService(client, SecretStr(_KEY)).embed_texts([]) == []


@pytest.mark.parametrize("status", [400, 401, 403, 302])
@pytest.mark.asyncio
async def test_nonretryable_errors_never_reflect_body_or_follow_redirects(
    status: int,
    no_retry_delays: AsyncMock,
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            text=f"{_KEY} private-code",
            headers={"Location": "https://untrusted.example/embeddings"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        follow_redirects=True,
    ) as client:
        with pytest.raises(ApiError) as caught:
            await EmbeddingService(client, SecretStr(_KEY)).embed_texts(
                ["private-code"]
            )
    assert calls == 1
    no_retry_delays.assert_not_awaited()
    assert caught.value.code == (
        "EMBEDDING_AUTH_FAILED" if status == 401 else "EMBEDDING_PROVIDER_ERROR"
    )
    assert _KEY not in str(caught.value)
    assert "private-code" not in str(caught.value)


@pytest.mark.parametrize("failure", [429, 500, 503, "timeout"])
@pytest.mark.asyncio
async def test_backoff_has_at_most_three_retries(
    failure: int | str,
    no_retry_delays: AsyncMock,
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout(f"secret {_KEY}", request=request)
        assert isinstance(failure, int)
        return httpx.Response(failure, text=_KEY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ApiError) as caught:
            await EmbeddingService(client, SecretStr(_KEY)).embed_texts(["code"])
    assert calls == 4
    assert [call.args for call in no_retry_delays.await_args_list] == [(1,), (2,), (4,)]
    assert caught.value.code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert _KEY not in str(caught.value)


@pytest.mark.asyncio
async def test_retry_recovers_and_network_failure_does_not_retry(
    no_retry_delays: AsyncMock,
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=_response([1.0]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await EmbeddingService(client, SecretStr(_KEY)).embed_texts(["code"])
    assert calls == 2
    no_retry_delays.assert_awaited_once_with(1)
    no_retry_delays.reset_mock()

    def disconnected(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(_KEY, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(disconnected)) as client:
        with pytest.raises(ApiError, match="Embedding provider request failed"):
            await EmbeddingService(client, SecretStr(_KEY)).embed_texts(["code"])
    no_retry_delays.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": [1.0] * 1535}]},
        {"data": [{"index": 0, "embedding": [0.0] * 1536}]},
        {"data": [{"index": 0, "embedding": [True] * 1536}]},
        {"data": [{"index": 0, "embedding": ["1.0"] * 1536}]},
        {"data": [{"index": 1, "embedding": [1.0] * 1536}]},
        {"data": [{"index": -1, "embedding": [1.0] * 1536}]},
        {"data": [{"index": True, "embedding": [1.0] * 1536}]},
        {"data": [{"index": 0, "embedding": [float("nan")] * 1536}]},
        {"data": [{"index": 0, "embedding": [float("inf")] * 1536}]},
        {"data": [{"index": 0, "embedding": [1.0] * 1536}] * 2},
        {
            "data": [{"index": 0, "embedding": [1.0] * 1536}],
            "usage": {"prompt_tokens": -1},
        },
    ],
)
@pytest.mark.asyncio
async def test_malformed_vectors_fail_closed(payload: dict[str, object]) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        # Raw JSON permits deliberately malformed NaN/Infinity fixtures.
        return httpx.Response(200, content=json.dumps(payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ApiError) as caught:
            await EmbeddingService(client, SecretStr(_KEY)).embed_texts(["code"])
    assert caught.value.code == "EMBEDDING_RESPONSE_INVALID"


@pytest.mark.asyncio
async def test_duplicate_indices_and_invalid_json() -> None:
    for content, inputs in (
        (
            json.dumps({"data": [{"index": 0, "embedding": [1.0] * 1536}] * 2}),
            ["a", "b"],
        ),
        ("not JSON " + _KEY, ["code"]),
    ):

        def handle(request: httpx.Request, body: str = content) -> httpx.Response:
            return httpx.Response(200, content=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(ApiError) as caught:
                await EmbeddingService(client, SecretStr(_KEY)).embed_texts(inputs)
        assert caught.value.code == "EMBEDDING_RESPONSE_INVALID"
        assert _KEY not in str(caught.value)


@pytest.mark.parametrize(
    "text", ["", "  ", "x " * 8192, "\ud800", "x" * (4 * 1024 * 1024 + 1)]
)
@pytest.mark.asyncio
async def test_invalid_or_oversized_input_is_rejected_before_any_egress(
    text: str,
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(
            "Validation must happen before any request, including earlier inputs"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(ApiError):
            await EmbeddingService(client, SecretStr(_KEY)).embed_texts(["valid", text])


@pytest.mark.parametrize("long_inputs", [False, True])
@pytest.mark.asyncio
async def test_batch_count_token_caps_and_order_across_batches(
    long_inputs: bool,
) -> None:
    encoding = tiktoken.get_encoding("cl100k_base")
    text = "x " * 4000 if long_inputs else "code"
    inputs = [text] * (80 if long_inputs else MAX_BATCH_INPUTS + 1)
    consumed = 0
    batches: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal consumed
        body = json.loads(request.content)
        batch = body["input"]
        assert len(batch) <= MAX_BATCH_INPUTS
        counts = [len(encoding.encode(item)) for item in batch]
        assert max(counts) <= MAX_INPUT_TOKENS
        assert sum(counts) <= MAX_BATCH_TOKENS
        values = [float(consumed + index + 1) for index in range(len(batch))]
        batches.append(len(batch))
        consumed += len(batch)
        return httpx.Response(200, json=_response(values, reverse=True))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await EmbeddingService(client, SecretStr(_KEY)).embed_texts(inputs)
    assert len(batches) == 2
    assert [vector[0] for vector in result] == list(range(1, len(inputs) + 1))


@pytest.mark.parametrize(
    ("base_url", "model", "retries"),
    [
        ("https://other-provider.example/v1", "text-embedding-3-small", 3),
        ("http://api.openai.com/v1", "text-embedding-3-small", 3),
        ("https://api.openai.com/v1", "unknown-model", 3),
        ("https://api.openai.com/v1", "text-embedding-3-small", 4),
    ],
)
@pytest.mark.asyncio
async def test_no_alternate_provider_or_unbounded_retries(
    base_url: str,
    model: str,
    retries: int,
) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ApiError) as caught:
            EmbeddingService(
                client, SecretStr(_KEY), model, base_url, max_retries=retries
            )
    assert caught.value.code == "EMBEDDING_CONFIGURATION_ERROR"
