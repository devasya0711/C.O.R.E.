"""Real OpenAI embeddings, with bounded inputs and no fallback provider."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Annotated

import httpx
import tiktoken
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictFloat,
    StrictInt,
    ValidationError,
)

from app.core.errors import ApiError

EMBEDDING_DIMENSIONS = 1536
MAX_INPUT_TOKENS = 8191
MAX_BATCH_TOKENS = 300_000
# A smaller batch than the provider's 2,048-input limit also bounds responses.
MAX_BATCH_INPUTS = 128
MAX_BATCH_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)


class _Embedding(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)

    index: Annotated[StrictInt, Field(ge=0)]
    embedding: Annotated[
        list[StrictFloat | StrictInt],
        Field(min_length=EMBEDDING_DIMENSIONS, max_length=EMBEDDING_DIMENSIONS),
    ]


class _Usage(BaseModel):
    model_config = ConfigDict(strict=True)

    prompt_tokens: Annotated[StrictInt, Field(ge=0)]
    total_tokens: Annotated[StrictInt, Field(ge=0)]


class _Response(BaseModel):
    model_config = ConfigDict(strict=True)

    data: list[_Embedding]
    usage: _Usage | None = None


def _provider_error() -> ApiError:
    return ApiError(
        502, "EMBEDDING_PROVIDER_ERROR", "Embedding provider request failed."
    )


class EmbeddingService:
    """Embed docstrings/bodies in input order, or fail with a safe ApiError.

    Oversized individual inputs are rejected rather than silently truncated.
    Only the official OpenAI endpoint and its cl100k_base embedding models are
    supported. The caller owns the HTTP client and the secret's persistence.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr | None,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30,
        max_retries: int = 3,
    ) -> None:
        if (
            base_url.rstrip("/") != "https://api.openai.com/v1"
            or model not in {"text-embedding-3-small", "text-embedding-3-large"}
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or type(max_retries) is not int
            or not 0 <= max_retries <= 3
        ):
            raise ApiError(
                503,
                "EMBEDDING_CONFIGURATION_ERROR",
                "Embedding configuration is invalid.",
            )
        self._client = client
        self._api_key = api_key
        self._model = model
        self._url = "https://api.openai.com/v1/embeddings"
        self._timeout = timeout_seconds
        self._max_retries = max_retries

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Request provider vectors only; never manufacture substitute vectors."""
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            raise ApiError(
                503, "EMBEDDING_NOT_CONFIGURED", "OpenAI embeddings are not configured."
            )
        if not texts:
            return []
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Loading the tokenizer can fail if its data is not pre-cached.
            raise ApiError(
                503,
                "EMBEDDING_TOKENIZER_UNAVAILABLE",
                "Embedding tokenizer is unavailable.",
            ) from None
        batches: list[list[str]] = []
        batch: list[str] = []
        tokens_in_batch = 0
        bytes_in_batch = 0
        # Validate everything before making the first provider request.
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ApiError(
                    422, "EMBEDDING_INPUT_INVALID", "Embedding input is invalid."
                )
            try:
                encoded_size = len(text.encode("utf-8"))
                if encoded_size > MAX_BATCH_BYTES:
                    raise ValueError
                count = len(encoding.encode(text, disallowed_special=()))
                body_size = len(json.dumps(text, ensure_ascii=True).encode("ascii")) + 1
            except (ValueError, UnicodeError):
                raise ApiError(
                    422,
                    "EMBEDDING_INPUT_TOO_LARGE",
                    "Embedding input exceeds provider limits.",
                ) from None
            if count > MAX_INPUT_TOKENS or body_size > MAX_BATCH_BYTES - 1024:
                raise ApiError(
                    422,
                    "EMBEDDING_INPUT_TOO_LARGE",
                    "Embedding input exceeds provider limits.",
                )
            if batch and (
                len(batch) >= MAX_BATCH_INPUTS
                or tokens_in_batch + count > MAX_BATCH_TOKENS
                or bytes_in_batch + body_size > MAX_BATCH_BYTES - 1024
            ):
                batches.append(batch)
                batch, tokens_in_batch, bytes_in_batch = [], 0, 0
            batch.append(text)
            tokens_in_batch += count
            bytes_in_batch += body_size
        if batch:
            batches.append(batch)
        result: list[list[float]] = []
        for inputs in batches:
            result.extend(await self._embed_batch(inputs))
        return result

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        assert self._api_key is not None
        body = json.dumps(
            {
                "model": self._model,
                "dimensions": EMBEDDING_DIMENSIONS,
                "encoding_format": "float",
                "input": texts,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        authorization = f"Bearer {self._api_key.get_secret_value()}"
        for attempt in range(self._max_retries + 1):
            try:
                # The outer deadline also bounds slow trickle responses, for
                # which HTTPX's per-read timeout alone is insufficient.
                async with asyncio.timeout(self._timeout):
                    async with self._client.stream(
                        "POST",
                        self._url,
                        headers={
                            "Authorization": authorization,
                            "Content-Type": "application/json",
                        },
                        content=body,
                        timeout=self._timeout,
                        follow_redirects=False,
                        auth=None,
                    ) as response:
                        status = response.status_code
                        if 200 <= status < 300:
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > MAX_RESPONSE_BYTES:
                                    raise _provider_error()
                            return self._parse_response(bytes(content), len(texts))
                        # Never consume, log, or reflect error bodies.
                        retry = status == 429 or 500 <= status <= 599
                        if not retry:
                            if status == 401:
                                raise ApiError(
                                    502,
                                    "EMBEDDING_AUTH_FAILED",
                                    "OpenAI authentication failed.",
                                )
                            raise _provider_error()
            except (httpx.TimeoutException, TimeoutError):
                retry = True
            except httpx.HTTPError:
                raise _provider_error() from None
            except (ValueError, UnicodeError):
                raise _provider_error() from None
            if not retry or attempt >= self._max_retries:
                raise ApiError(
                    503,
                    "EMBEDDING_PROVIDER_UNAVAILABLE",
                    "Embedding provider is unavailable.",
                ) from None
            await asyncio.sleep(2**attempt)
        raise _provider_error()  # Defensive; the bounded loop always returns or raises.

    @staticmethod
    def _parse_response(content: bytes, expected: int) -> list[list[float]]:
        try:
            response = _Response.model_validate_json(content)
            if len(response.data) != expected:
                raise ValueError
            ordered: list[list[float] | None] = [None] * expected
            for item in response.data:
                if item.index >= expected or ordered[item.index] is not None:
                    raise ValueError
                vector = [float(value) for value in item.embedding]
                if not all(math.isfinite(value) for value in vector) or not any(vector):
                    raise ValueError
                ordered[item.index] = vector
            if any(vector is None for vector in ordered):
                raise ValueError
        except (ValidationError, ValueError, OverflowError):
            raise ApiError(
                502,
                "EMBEDDING_RESPONSE_INVALID",
                "Embedding provider returned invalid vectors.",
            ) from None
        if response.usage is not None:
            _LOGGER.info(
                "OpenAI embedding token usage: prompt_tokens=%d total_tokens=%d",
                response.usage.prompt_tokens,
                response.usage.total_tokens,
            )
        return [vector for vector in ordered if vector is not None]
