"""Validated request, response, and event contracts for migrations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Literal, TypeAlias, cast
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
)


class MigrationEventType(StrEnum):
    """Stable event names consumed by the migration SSE client."""

    AGENT_START = "AGENT_START"
    CRAG_DECISION = "CRAG_DECISION"
    SANDBOX_ITERATION = "SANDBOX_ITERATION"
    ARTIFACTS_READY = "ARTIFACTS_READY"
    PR_CREATED = "PR_CREATED"
    MIGRATION_FAILED = "MIGRATION_FAILED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    COMPLETED = "COMPLETED"


MigrationEventLiteral: TypeAlias = Literal[  # noqa: UP040
    "AGENT_START",
    "CRAG_DECISION",
    "SANDBOX_ITERATION",
    "ARTIFACTS_READY",
    "PR_CREATED",
    "MIGRATION_FAILED",
    "HUMAN_REVIEW",
    "COMPLETED",
]

TERMINAL_MIGRATION_EVENTS = frozenset(
    {
        MigrationEventType.PR_CREATED,
        MigrationEventType.MIGRATION_FAILED,
        MigrationEventType.HUMAN_REVIEW,
        MigrationEventType.COMPLETED,
    }
)
TERMINAL_EVENT_TYPES = TERMINAL_MIGRATION_EVENTS

MigrationStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
MigrationPRStatus = Literal["PR_CREATED", "COMPLETED"]

JSONScalar: TypeAlias = None | bool | int | float | str  # noqa: UP040
JSONValue: TypeAlias = (  # noqa: UP040
    JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
)

_MAX_EVENT_PAYLOAD_BYTES = 32 * 1024
_MAX_EVENT_PAYLOAD_DEPTH = 8
_MAX_EVENT_PAYLOAD_ITEMS = 128
_MAX_EVENT_TEXT_BYTES = 4 * 1024
_REDACTED = "[REDACTED]"
_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "exception",
        "password",
        "pat",
        "raw_error",
        "secret",
        "stderr",
        "stdout",
        "token",
        "traceback",
        "private_key",
    }
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return normalized in _SENSITIVE_PAYLOAD_KEYS or any(
        marker in normalized
        for marker in (
            "_secret",
            "_token",
            "_password",
            "_exception",
            "_traceback",
            "_credential",
        )
    )


def _safe_payload_value(
    value: object, *, key: str | None = None, depth: int = 0
) -> JSONValue:
    """Convert a bounded payload to JSON data without reflecting secrets/errors."""
    if depth > _MAX_EVENT_PAYLOAD_DEPTH:
        raise ValueError("event payload is too deeply nested")
    if key is not None and _is_sensitive_key(key):
        return _REDACTED
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("event payload contains a non-finite number")
        return value
    if isinstance(value, str):
        if "\x00" in value or any(
            (ord(character) < 32 and character not in "\t\n\r") or ord(character) == 127
            for character in value
        ):
            raise ValueError("event payload contains unsupported text")
        if len(value.encode("utf-8")) > _MAX_EVENT_TEXT_BYTES:
            raise ValueError("event payload text is too large")
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("event payload datetimes must include a timezone")
        return value.isoformat()
    if isinstance(value, Mapping):
        if len(value) > _MAX_EVENT_PAYLOAD_ITEMS:
            raise ValueError("event payload object is too large")
        converted: dict[str, JSONValue] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise ValueError("event payload keys must be non-empty strings")
            if "\x00" in raw_key or len(raw_key.encode("utf-8")) > 128:
                raise ValueError("event payload key is invalid")
            converted[raw_key] = _safe_payload_value(
                raw_value, key=raw_key, depth=depth + 1
            )
        return converted
    if isinstance(value, list):
        if len(value) > _MAX_EVENT_PAYLOAD_ITEMS:
            raise ValueError("event payload array is too large")
        return [_safe_payload_value(item, depth=depth + 1) for item in value]
    raise ValueError("event payload must contain JSON-safe values")


def _safe_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("event payload must be an object")
    converted = _safe_payload_value(value)
    if not isinstance(converted, dict):
        raise ValueError("event payload must be an object")
    try:
        encoded = json.dumps(converted, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("event payload must be JSON serializable") from None
    if len(encoded.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError("event payload is too large")
    return cast(dict[str, object], converted)


class MigrationCreate(BaseModel):
    """Validated input for starting one seam migration."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    seam_id: UUID
    create_github_pr: StrictBool = False


class MigrationAccepted(BaseModel):
    """Safe response returned after the migration job is durably queued."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    job_id: UUID
    status: Literal["QUEUED"] = "QUEUED"


class MigrationEvent(BaseModel):
    """One JSON-safe migration event stored as a Redis stream field."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    job_id: UUID = Field(validation_alias=AliasChoices("job_id", "migration_id"))
    repo_id: UUID
    event_type: MigrationEventType = Field(
        validation_alias=AliasChoices("event_type", "type", "event")
    )
    # The validator below narrows this object map to JSON-safe values.  Keeping
    # the annotation non-recursive also supports the project's Python 3.12
    # target and the Python 3.11 test runner used by local tooling.
    payload: dict[str, object] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("payload", "data"),
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        validation_alias=AliasChoices("timestamp", "occurred_at", "created_at"),
    )

    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload(cls, value: object) -> dict[str, object]:
        return _safe_payload(value)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("event timestamp must include a timezone")
        return value

    @property
    def type(self) -> MigrationEventType:
        """Compatibility spelling for SSE serializers using ``event.type``."""
        return self.event_type

    @property
    def event(self) -> MigrationEventType:
        """Compatibility spelling for serializers using ``event.event``."""
        return self.event_type

    @property
    def data(self) -> dict[str, object]:
        """Compatibility spelling for callers that use ``data`` for payload."""
        return self.payload

    @property
    def created_at(self) -> datetime:
        """Compatibility spelling for durable-event consumers."""
        return self.timestamp

    @property
    def is_terminal(self) -> bool:
        return self.event_type in TERMINAL_MIGRATION_EVENTS


class MigrationPRRequest(BaseModel):
    """Optional metadata for manually creating a PR from a completed job."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    job_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("job_id", "migration_id"),
    )
    title: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    base_branch: StrictStr = Field(default="main", min_length=1, max_length=255)

    @field_validator("title", "base_branch")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or any(
            (ord(character) < 32 and character not in "\t\n\r") or ord(character) == 127
            for character in value
        ):
            raise ValueError("migration PR text is invalid")
        return value


class MigrationPRResponse(BaseModel):
    """Safe PR result; credentials and provider exception details never cross it."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    job_id: UUID
    status: MigrationPRStatus = "PR_CREATED"
    pr_url: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        validation_alias=AliasChoices("pr_url", "url", "pull_request_url"),
    )
    pr_number: StrictInt | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("pr_number", "number", "pull_request_number"),
    )
    branch: StrictStr | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("pr_url", "branch")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and any(
            (ord(character) < 32 and character not in "\t\n\r") or ord(character) == 127
            for character in value
        ):
            raise ValueError("migration PR response text is invalid")
        return value


# Explicit aliases keep the contracts discoverable to workflow/API callers that
# use request/response terminology instead of the shorter schema names.
MigrationCreateRequest = MigrationCreate
MigrationRequest = MigrationCreate
MigrationAcceptedResponse = MigrationAccepted
MigrationEventName = MigrationEventType
MigrationEventKind = MigrationEventType
MigrationPRCreateRequest = MigrationPRRequest
MigrationPRCreateResponse = MigrationPRResponse
MigrationStatusLiteral = MigrationStatus
PRRequest = MigrationPRRequest
PRResponse = MigrationPRResponse


__all__ = [
    "JSONValue",
    "MigrationAccepted",
    "MigrationAcceptedResponse",
    "MigrationCreate",
    "MigrationCreateRequest",
    "MigrationEvent",
    "MigrationEventLiteral",
    "MigrationEventKind",
    "MigrationEventName",
    "MigrationEventType",
    "MigrationPRCreateRequest",
    "MigrationPRCreateResponse",
    "MigrationPRRequest",
    "MigrationPRResponse",
    "MigrationRequest",
    "MigrationStatus",
    "MigrationStatusLiteral",
    "PRRequest",
    "PRResponse",
    "TERMINAL_EVENT_TYPES",
    "TERMINAL_MIGRATION_EVENTS",
]
