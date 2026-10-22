"""Request correlation and structured logging regression tests."""

from __future__ import annotations

import json
import logging
import re

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.core.redaction import SecretRedactor
from app.main import create_app
from fastapi.testclient import TestClient


def test_request_id_is_honored_and_generated_when_missing() -> None:
    application = create_app(Settings(_env_file=None))
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
    application = create_app(Settings(_env_file=None))
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
