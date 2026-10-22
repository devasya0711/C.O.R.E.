"""Structured application logging and request correlation for C.O.R.E."""

from __future__ import annotations

import contextvars
import json
import logging as std_logging
import re
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.redaction import SecretRedactor

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "core_request_id", default=None
)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<credentials>[^/\s@]+)@"
)
_INLINE_SECRET_PATTERN = re.compile(
    r"(?P<label>authorization|proxy-authorization|cookie|set-cookie|"
    r"x-api-key|api[_-]?key|password|passwd|secret|access[_-]?token|"
    r"refresh[_-]?token|bearer)\s*(?P<separator>[:=])\s*"
    r"(?P<value>Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?:^|[_-])(authorization|cookie|password|passwd|secret|token|"
    r"api[_-]?key|access[_-]?key|private[_-]?key|credential|dsn|"
    r"database_url|redis_url|neo4j_uri|connection_url)(?:$|[_-])",
    re.IGNORECASE,
)
_STANDARD_RECORD_FIELDS = frozenset(
    std_logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)


def get_request_id() -> str | None:
    """Return the request ID associated with the current async context."""

    return _REQUEST_ID.get()


def request_id_from_header(value: str | None) -> str:
    """Honor a safe incoming ID, or generate one for malformed/missing input."""

    if value is not None and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


def _redact_text(value: str, redactor: SecretRedactor) -> str:
    """Remove configured and inline credential values from free-form text."""

    redacted = redactor.redact(value)
    redacted = _URL_CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group('scheme')}[REDACTED]@", redacted
    )
    return _INLINE_SECRET_PATTERN.sub(
        lambda match: f"{match.group('label')}{match.group('separator')}[REDACTED]",
        redacted,
    )


def _safe_value(value: object, redactor: SecretRedactor, field_name: str) -> object:
    """Convert an arbitrary logging value into redacted JSON-compatible data."""

    if _SENSITIVE_FIELD_PATTERN.search(field_name):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item, redactor, str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item, redactor, field_name) for item in value]
    if isinstance(value, str):
        return _redact_text(value, redactor)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value), redactor)


class JsonFormatter(std_logging.Formatter):
    """Emit one redacted JSON object per log record using only the stdlib."""

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or SecretRedactor()

    def format(self, record: std_logging.LogRecord) -> str:
        """Serialize the record and its structured extras as a JSON object."""

        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage(), self.redactor),
            "request_id": getattr(record, "request_id", get_request_id()),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = _safe_value(value, self.redactor, key)
        if record.exc_info is not None:
            payload["exception"] = _redact_text(
                self.formatException(record.exc_info), self.redactor
            )
        if record.stack_info is not None:
            payload["stack"] = _redact_text(record.stack_info, self.redactor)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(settings: object | None = None) -> None:
    """Install the process-wide JSON handler without adding a dependency."""

    redactor = SecretRedactor.from_settings(settings)
    root = std_logging.getLogger()
    root.setLevel(std_logging.INFO)
    for handler in root.handlers:
        if getattr(handler, "_core_json_handler", False):
            handler.setFormatter(JsonFormatter(redactor))
            return
    handler = std_logging.StreamHandler(sys.stdout)
    handler._core_json_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter(redactor))
    root.addHandler(handler)


class RequestLoggingMiddleware:
    """Correlate HTTP requests and emit a redacted completion log."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.logger = std_logging.getLogger("app.http")

    async def __call__(self, scope: dict[str, object], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = request_id_from_header(_request_header(scope, b"x-request-id"))
        token = _REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: dict[str, object]) -> None:
            nonlocal response_started, status_code
            if message.get("type") == "http.response.start":
                response_started = True
                raw_status = message.get("status")
                if isinstance(raw_status, int):
                    status_code = raw_status
                headers = message.setdefault("headers", [])
                if isinstance(headers, list) and not any(
                    isinstance(header, (tuple, list))
                    and len(header) >= 2
                    and isinstance(header[0], bytes)
                    and header[0].lower() == b"x-request-id"
                    for header in headers
                ):
                    headers.append((b"x-request-id", request_id.encode("ascii")))
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except BaseException:
            self.logger.exception(
                "request_failed",
                extra={
                    "event": "http_request",
                    "method": _scope_value(scope, "method"),
                    "path": _scope_value(scope, "path"),
                    "status_code": status_code,
                    "latency_ms": _elapsed_ms(started),
                    "response_started": response_started,
                },
            )
            raise
        finally:
            latency_ms = _elapsed_ms(started)
            extra = {
                "event": "http_request",
                "method": _scope_value(scope, "method"),
                "path": _scope_value(scope, "path"),
                "status_code": status_code,
                "latency_ms": latency_ms,
                "response_started": response_started,
            }
            if status_code >= 500:
                self.logger.warning("request_completed", extra=extra)
            else:
                self.logger.info("request_completed", extra=extra)
            _REQUEST_ID.reset(token)


def _request_header(scope: Mapping[str, object], name: bytes) -> str | None:
    headers = scope.get("headers", [])
    if not isinstance(headers, list):
        return None
    for header in headers:
        if not isinstance(header, (tuple, list)) or len(header) < 2:
            continue
        if not isinstance(header[0], bytes) or header[0].lower() != name:
            continue
        value = header[1]
        if isinstance(value, bytes):
            return value.decode("latin-1")
    return None


def _scope_value(scope: Mapping[str, object], key: str) -> str:
    value = scope.get(key, "")
    return value if isinstance(value, str) else ""


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


__all__ = [
    "JsonFormatter",
    "RequestLoggingMiddleware",
    "configure_logging",
    "get_request_id",
    "request_id_from_header",
]
