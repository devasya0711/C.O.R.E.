"""Request correlation and structured logging regression tests."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections.abc import AsyncIterator
from io import StringIO
from typing import cast

import pytest
from app.core.config import Settings
from app.core.logging import (
    JsonFormatter,
    RequestLoggingMiddleware,
    get_request_id,
)
from app.core.redaction import SecretRedactor
from app.main import create_app
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.types import Message, Receive, Scope, Send


def _settings(openai_api_key: SecretStr | None = None) -> Settings:
    if openai_api_key is None:
        return Settings(_env_file=None)  # type: ignore[call-arg]
    return Settings(  # type: ignore[call-arg]
        _env_file=None, openai_api_key=openai_api_key
    )


def _record_value(record: logging.LogRecord, field: str) -> object:
    return record.__dict__[field]


def test_request_id_is_honored_and_generated_when_missing() -> None:
    application = create_app(_settings())
    with TestClient(application) as client:
        honored = client.get(
            "/api/v1/health", headers={"X-Request-ID": "batch12-request-7"}
        )
        generated = client.get("/api/v1/health")

    assert honored.headers["x-request-id"] == "batch12-request-7"
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        generated.headers["x-request-id"],
    )


def test_malformed_request_id_is_replaced() -> None:
    application = create_app(_settings())
    with TestClient(application) as client:
        response = client.get(
            "/api/v1/health", headers={"X-Request-ID": "contains spaces"}
        )

    assert response.headers["x-request-id"] != "contains spaces"
    assert len(response.headers["x-request-id"]) == 36


def test_json_formatter_redacts_credentials_and_preserves_latency_fields() -> None:
    secret = "log-test-secret"
    formatter = JsonFormatter(SecretRedactor([secret]))
    record = logging.LogRecord(
        "test.logger",
        logging.INFO,
        __file__,
        1,
        "connection failed password=%s",
        (secret,),
        None,
    )
    record.request_id = "request-123"
    record.latency_ms = 12.34
    record.authorization = f"Bearer {secret}"
    record.details = {"database_url": f"postgresql://user:{secret}@db/core"}

    payload = json.loads(formatter.format(record))

    assert payload["request_id"] == "request-123"
    assert payload["latency_ms"] == 12.34
    assert secret not in json.dumps(payload)
    assert payload["authorization"] == "[REDACTED]"
    assert payload["details"]["database_url"] == "[REDACTED]"


def test_configured_and_inline_redaction_removes_exception_details() -> None:
    configured_secret = "configured-log-secret"
    inline_secret = "inline-log-secret"
    formatter = JsonFormatter(
        SecretRedactor.from_settings(
            _settings(openai_api_key=SecretStr(configured_secret))
        )
    )
    try:
        raise ValueError(f"raw exception body: {inline_secret}")
    except ValueError:
        record = logging.LogRecord(
            "test.logger",
            logging.ERROR,
            __file__,
            1,
            "GET /health?access_token=%s configured=%s",
            (inline_secret, configured_secret),
            sys.exc_info(),
        )
        record.path = f"/health?password={inline_secret}"
        record.stack_info = "source line includes configured-log-secret"

    payload = json.loads(formatter.format(record))
    serialized = json.dumps(payload)

    assert configured_secret not in serialized
    assert inline_secret not in serialized
    assert "raw exception body" not in serialized
    assert "source line includes" not in serialized
    assert payload["exception_type"] == "ValueError"


def test_existing_handler_receives_redacted_records_without_replacement() -> None:
    secret = "existing-handler-secret"
    logger = logging.getLogger("test.existing-handler")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        from app.core.logging import configure_logging

        configure_logging(_settings(openai_api_key=SecretStr(secret)))
        logger.error("provider failed password=%s", secret)
        assert logger.handlers == [handler]
        assert secret not in stream.getvalue()
        assert "[REDACTED]" in stream.getvalue()
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


def test_unhandled_500_has_request_id_and_only_failed_request_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = create_app(_settings())

    async def raise_error() -> None:
        raise RuntimeError("raw exception body")

    application.add_api_route("/test-unhandled-error", raise_error)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.http"):
        with TestClient(application, raise_server_exceptions=False) as client:
            response = client.get(
                "/test-unhandled-error", headers={"X-Request-ID": "error-request"}
            )

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "error-request"
    assert "raw exception body" not in response.text
    request_logs = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request"
    ]
    assert [record.getMessage() for record in request_logs] == ["request_failed"]
    assert cast(int, _record_value(request_logs[0], "status_code")) == 500
    assert cast(str, _record_value(request_logs[0], "request_id")) == "error-request"
    assert cast(str, _record_value(request_logs[0], "exception_type")) == "RuntimeError"


def test_success_log_contains_structured_request_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = create_app(_settings())
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.http"):
        with TestClient(application) as client:
            response = client.get(
                "/api/v1/health", headers={"X-Request-ID": "structured-request"}
            )

    assert response.headers["x-request-id"] == "structured-request"
    request_logs = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request"
    ]
    assert [record.getMessage() for record in request_logs] == ["request_completed"]
    assert (
        cast(str, _record_value(request_logs[0], "request_id")) == "structured-request"
    )
    assert cast(str, _record_value(request_logs[0], "method")) == "GET"
    assert cast(str, _record_value(request_logs[0], "path")) == "/api/v1/health"
    assert cast(int, _record_value(request_logs[0], "status_code")) in {200, 503}
    assert isinstance(_record_value(request_logs[0], "latency_ms"), float)


def test_sse_response_keeps_body_and_request_correlation() -> None:
    application = create_app(_settings())

    async def stream_body() -> AsyncIterator[str]:
        yield "data: one\n\n"
        await asyncio.sleep(0)
        yield "data: two\n\n"

    async def stream() -> StreamingResponse:
        return StreamingResponse(stream_body(), media_type="text/event-stream")

    application.add_api_route("/test-stream", stream)
    with TestClient(application) as client:
        response = client.get("/test-stream", headers={"X-Request-ID": "sse-request"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "sse-request"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == "data: one\n\ndata: two\n\n"


@pytest.mark.asyncio
async def test_request_context_isolated_across_concurrent_asgi_calls() -> None:
    observed: list[str | None] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        del scope, receive
        observed.append(get_request_id())
        await asyncio.sleep(0)
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestLoggingMiddleware(downstream)

    async def invoke(request_id: str) -> None:
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/context",
            "raw_path": b"/context",
            "query_string": b"",
            "headers": [(b"x-request-id", request_id.encode("ascii"))],
            "client": ("test", 0),
            "server": ("test", 80),
            "root_path": "",
        }

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            del message

        await middleware(scope, receive, send)

    await asyncio.gather(invoke("context-one"), invoke("context-two"))

    assert set(observed) == {"context-one", "context-two"}
    assert get_request_id() is None
