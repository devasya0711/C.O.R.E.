"""Structured application logging and request correlation for C.O.R.E."""

from __future__ import annotations

import contextvars
import json
import logging as std_logging
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
_EXCEPTION_FIELD_PATTERN = re.compile(
    r"^(?:exception|exception[_-]?message|exc|traceback|stack)$", re.IGNORECASE
)
_STANDARD_RECORD_FIELDS = frozenset(
    std_logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)
_REQUEST_ID_SCOPE_KEY = "core.request_id"
_ACTIVE_REDACTOR = SecretRedactor()
_ORIGINAL_RECORD_FACTORY: Callable[..., std_logging.LogRecord] = (
    std_logging.getLogRecordFactory()
)
_RECORD_FACTORY_INSTALLED = False


def get_request_id() -> str | None:
    """Return the request ID associated with the current async context."""

    return _REQUEST_ID.get()


def request_id_from_header(value: str | None) -> str:
    """Honor a safe incoming ID, or generate one for malformed/missing input."""

    if value is not None and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


def ensure_request_id(scope: Scope) -> str:
    """Return and persist the correlation ID for an ASGI request scope."""

    existing = scope.get(_REQUEST_ID_SCOPE_KEY)
    if isinstance(existing, str) and _REQUEST_ID_PATTERN.fullmatch(existing):
        return existing
    request_id = request_id_from_header(_request_header(scope, b"x-request-id"))
    scope[_REQUEST_ID_SCOPE_KEY] = request_id
    return request_id


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

    if _SENSITIVE_FIELD_PATTERN.search(field_name) or (
        _EXCEPTION_FIELD_PATTERN.fullmatch(field_name) is not None
    ):
        return "[REDACTED]"
    if isinstance(value, BaseException):
        return type(value).__name__
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


def _sanitize_record(record: std_logging.LogRecord, redactor: SecretRedactor) -> None:
    """Sanitize a record before any configured handler can render it."""

    try:
        message = record.getMessage()
    except (KeyError, TypeError, ValueError):
        message = str(record.msg)
    record.msg = _redact_text(message, redactor)
    record.args = ()

    if record.exc_info is not None:
        exception = record.exc_info[1]
        if isinstance(exception, BaseException):
            record.exception_type = type(exception).__name__
        record.exc_info = None
    record.exc_text = None
    record.stack_info = None

    for key, value in list(record.__dict__.items()):
        if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
            continue
        if key == "exception_type" and isinstance(value, str):
            continue
        setattr(record, key, _safe_value(value, redactor, key))


def _redacting_record_factory(*args: object, **kwargs: object) -> std_logging.LogRecord:
    """Create records that are safe for every current and future handler."""

    record = _ORIGINAL_RECORD_FACTORY(*args, **kwargs)
    _sanitize_record(record, _ACTIVE_REDACTOR)
    return record


class _RedactionFilter(std_logging.Filter):
    """Apply the active redactor to handlers that already exist."""

    _core_redaction_filter = True

    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: std_logging.LogRecord) -> bool:
        _sanitize_record(record, self.redactor)
        return True


def _all_handlers() -> list[std_logging.Handler]:
    """Return existing handlers without removing or replacing user handlers."""

    root = std_logging.getLogger()
    handlers = list(root.handlers)
    for logger in std_logging.Logger.manager.loggerDict.values():
        if isinstance(logger, std_logging.Logger):
            handlers.extend(logger.handlers)
    return handlers


def _install_record_redaction(redactor: SecretRedactor) -> None:
    """Protect existing handlers and install the process-wide record factory."""

    global _ACTIVE_REDACTOR, _RECORD_FACTORY_INSTALLED
    _ACTIVE_REDACTOR = redactor
    if not _RECORD_FACTORY_INSTALLED:
        std_logging.setLogRecordFactory(_redacting_record_factory)
        _RECORD_FACTORY_INSTALLED = True

    for handler in _all_handlers():
        for current_filter in handler.filters:
            if isinstance(current_filter, _RedactionFilter):
                current_filter.redactor = redactor
                break
        else:
            handler.addFilter(_RedactionFilter(redactor))


class JsonFormatter(std_logging.Formatter):
    """Emit one redacted JSON object per log record using only the stdlib."""

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or SecretRedactor()

    def format(self, record: std_logging.LogRecord) -> str:
        """Serialize the record and its structured extras as a JSON object."""

        _sanitize_record(record, self.redactor)
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
        exception_type = getattr(record, "exception_type", None)
        if isinstance(exception_type, str):
            payload["exception_type"] = exception_type
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(settings: object | None = None) -> None:
    """Install the process-wide JSON handler without adding a dependency."""

    redactor = SecretRedactor.from_settings(settings)
    _install_record_redaction(redactor)
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

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = std_logging.getLogger("app.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = ensure_request_id(scope)
        token = _REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False
        request_failed = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message.get("type") == "http.response.start":
                response_started = True
                raw_status = message.get("status")
                if isinstance(raw_status, int):
                    status_code = raw_status
                headers = message.get("headers")
                if not isinstance(headers, list):
                    headers = list(headers) if isinstance(headers, tuple) else []
                    message["headers"] = headers
                if not any(
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
            request_failed = True
            self.logger.exception(
                "request_failed",
                extra={
                    "event": "http_request",
                    "request_id": request_id,
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
                "request_id": request_id,
                "method": _scope_value(scope, "method"),
                "path": _scope_value(scope, "path"),
                "status_code": status_code,
                "latency_ms": latency_ms,
                "response_started": response_started,
            }
            if not request_failed:
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
