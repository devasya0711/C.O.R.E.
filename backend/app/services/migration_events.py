"""Typed Redis Streams persistence and replay for migration SSE events."""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast
from uuid import UUID

from pydantic import ValidationError

from app.schemas.migrations import (
    TERMINAL_MIGRATION_EVENTS,
    MigrationEvent,
    MigrationEventType,
)

MIGRATION_EVENT_STREAM_MAXLEN = 100
MIGRATION_EVENT_STREAM_TTL_SECONDS = 86_400
MIGRATION_EVENT_FIELD = "event"
_STREAM_ID_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")
T = TypeVar("T")


class RedisStreamClient(Protocol):
    """Small Redis Streams surface shared by redis.asyncio and test fakes."""

    def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> Awaitable[str] | str:
        """Append fields to a stream and return its Redis entry ID."""

    def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> (
        Awaitable[Sequence[tuple[object, Mapping[object, object]]]]
        | Sequence[tuple[object, Mapping[object, object]]]
    ):
        """Read a bounded range of stream entries."""

    def expire(self, name: str, time: int) -> Awaitable[bool] | bool:
        """Apply a retention timeout to a stream key."""


class MigrationEventStoreError(RuntimeError):
    """Static, credential-free error raised when Redis cannot serve the stream."""


class MigrationEventDecodeError(ValueError):
    """Raised when a stream entry does not contain a valid migration event."""


@dataclass(frozen=True, slots=True)
class MigrationEventRecord:
    """A replayable event together with its Redis ID and terminal state."""

    stream_id: str
    event: MigrationEvent

    @property
    def id(self) -> str:
        """SSE ``id`` value used as the next Last-Event-ID cursor."""
        return self.stream_id

    @property
    def event_type(self) -> MigrationEventType:
        return self.event.event_type

    @property
    def terminal(self) -> bool:
        return self.event.event_type in TERMINAL_MIGRATION_EVENTS

    @property
    def is_terminal(self) -> bool:
        return self.terminal


def migration_stream_key(job_id: UUID) -> str:
    """Return the stable UUID-namespaced stream key for one migration job."""
    if not isinstance(job_id, UUID):
        raise TypeError("migration job ID must be a UUID")
    return f"core:migrations:{job_id}:events"


def migration_event_key(job_id: UUID) -> str:
    """Compatibility name for callers that refer to the stream as an event key."""
    return migration_stream_key(job_id)


def _stream_id(value: object) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="strict")
    if not isinstance(value, str) or _STREAM_ID_PATTERN.fullmatch(value) is None:
        raise MigrationEventDecodeError("migration stream entry ID is invalid")
    return value


def _field_text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            raise MigrationEventDecodeError(
                "migration stream field is invalid"
            ) from None
    if not isinstance(value, str):
        raise MigrationEventDecodeError("migration stream field is invalid")
    return value


async def _resolve(value: Awaitable[T] | T) -> T:  # noqa: UP047
    """Allow async Redis in production and sync-compatible protocol fakes in tests."""
    if inspect.isawaitable(value):
        return await cast(Awaitable[T], value)
    return value


class MigrationEventService:
    """Publish and replay bounded migration event streams."""

    def __init__(self, redis: RedisStreamClient) -> None:
        self.redis = redis

    @staticmethod
    def stream_key(job_id: UUID) -> str:
        return migration_stream_key(job_id)

    async def publish(self, event: MigrationEvent | Mapping[str, object]) -> str:
        """Append one validated event and refresh its one-day retention window."""
        typed_event = (
            event
            if isinstance(event, MigrationEvent)
            else MigrationEvent.model_validate(event)
        )
        key = migration_stream_key(typed_event.job_id)
        try:
            entry_id = await _resolve(
                self.redis.xadd(
                    key,
                    {MIGRATION_EVENT_FIELD: typed_event.model_dump_json()},
                    maxlen=MIGRATION_EVENT_STREAM_MAXLEN,
                    approximate=False,
                )
            )
            return_id = _stream_id(entry_id)
            await _resolve(self.redis.expire(key, MIGRATION_EVENT_STREAM_TTL_SECONDS))
            return return_id
        except MigrationEventDecodeError:
            raise
        except Exception as error:
            raise MigrationEventStoreError(
                "Migration event stream is unavailable."
            ) from error

    async def publish_event(self, event: MigrationEvent | Mapping[str, object]) -> str:
        """Workflow-friendly alias for :meth:`publish`."""
        return await self.publish(event)

    async def emit(
        self,
        job_id: UUID,
        repo_id: UUID,
        event_type: MigrationEventType,
        payload: Mapping[str, object] | None = None,
    ) -> str:
        """Build and publish an event without accepting untyped stream fields."""
        event = MigrationEvent(
            job_id=job_id,
            repo_id=repo_id,
            event_type=event_type,
            payload=dict(payload or {}),
        )
        return await self.publish(event)

    async def publish_failure(
        self,
        job_id: UUID,
        repo_id: UUID,
        error: BaseException | None = None,
        *,
        error_code: str = "MIGRATION_FAILED",
        stage: str = "MIGRATION",
    ) -> str:
        """Publish a safe failure marker without serializing exception text."""
        error_type = type(error).__name__ if error is not None else "MigrationError"
        if not error_type.isidentifier() or len(error_type) > 128:
            error_type = "MigrationError"
        safe_code = (
            error_code
            if error_code.isidentifier() and len(error_code) <= 128
            else "MIGRATION_FAILED"
        )
        safe_stage = (
            stage if stage.isidentifier() and len(stage) <= 128 else "MIGRATION"
        )
        return await self.emit(
            job_id,
            repo_id,
            MigrationEventType.MIGRATION_FAILED,
            {
                "error_code": safe_code,
                "stage": safe_stage,
                "error_type": error_type,
            },
        )

    async def read_events(
        self,
        job_id: UUID,
        *,
        last_event_id: str | None = None,
        limit: int = MIGRATION_EVENT_STREAM_MAXLEN,
        stop_at_terminal: bool = True,
    ) -> list[MigrationEventRecord]:
        """Read at most ``limit`` events after an optional Last-Event-ID."""
        if type(limit) is not int or not 1 <= limit <= MIGRATION_EVENT_STREAM_MAXLEN:
            raise ValueError("migration event read limit must be between 1 and 100")
        minimum = self._range_start(last_event_id)
        key = migration_stream_key(job_id)
        try:
            entries = await _resolve(
                self.redis.xrange(key, min=minimum, max="+", count=limit)
            )
        except Exception as error:
            raise MigrationEventStoreError(
                "Migration event stream is unavailable."
            ) from error

        records: list[MigrationEventRecord] = []
        for raw_id, raw_fields in entries:
            record = self._decode_record(raw_id, raw_fields)
            records.append(record)
            if stop_at_terminal and record.terminal:
                break
        return records

    async def replay_events(
        self,
        job_id: UUID,
        *,
        last_event_id: str | None = None,
        limit: int = MIGRATION_EVENT_STREAM_MAXLEN,
        stop_at_terminal: bool = True,
    ) -> AsyncIterator[MigrationEventRecord]:
        """Yield a bounded replay suitable for an SSE response."""
        records = await self.read_events(
            job_id,
            last_event_id=last_event_id,
            limit=limit,
            stop_at_terminal=stop_at_terminal,
        )
        for record in records:
            yield record

    async def replay(
        self,
        job_id: UUID,
        *,
        last_event_id: str | None = None,
        limit: int = MIGRATION_EVENT_STREAM_MAXLEN,
        stop_at_terminal: bool = True,
    ) -> AsyncIterator[MigrationEventRecord]:
        """Short alias for :meth:`replay_events`."""
        async for record in self.replay_events(
            job_id,
            last_event_id=last_event_id,
            limit=limit,
            stop_at_terminal=stop_at_terminal,
        ):
            yield record

    async def read(
        self,
        job_id: UUID,
        *,
        last_event_id: str | None = None,
        limit: int = MIGRATION_EVENT_STREAM_MAXLEN,
        stop_at_terminal: bool = True,
    ) -> list[MigrationEventRecord]:
        """Short alias for bounded :meth:`read_events` callers."""
        return await self.read_events(
            job_id,
            last_event_id=last_event_id,
            limit=limit,
            stop_at_terminal=stop_at_terminal,
        )

    async def iter_events(
        self,
        job_id: UUID,
        *,
        last_event_id: str | None = None,
        limit: int = MIGRATION_EVENT_STREAM_MAXLEN,
        stop_at_terminal: bool = True,
    ) -> AsyncIterator[MigrationEventRecord]:
        """Alias for :meth:`replay_events` used by SSE workflow callers."""
        async for record in self.replay_events(
            job_id,
            last_event_id=last_event_id,
            limit=limit,
            stop_at_terminal=stop_at_terminal,
        ):
            yield record

    @staticmethod
    def is_terminal(event: MigrationEvent | MigrationEventType | str) -> bool:
        """Return whether an event ends the migration stream."""
        event_type = event.event_type if isinstance(event, MigrationEvent) else event
        try:
            return MigrationEventType(event_type) in TERMINAL_MIGRATION_EVENTS
        except ValueError:
            return False

    @staticmethod
    def _range_start(last_event_id: str | None) -> str:
        if last_event_id in (None, ""):
            return "-"
        if (
            not isinstance(last_event_id, str)
            or _STREAM_ID_PATTERN.fullmatch(last_event_id) is None
        ):
            raise ValueError("Last-Event-ID must be a Redis stream ID")
        return f"({last_event_id}"

    @staticmethod
    def _decode_record(
        raw_id: object, raw_fields: Mapping[object, object]
    ) -> MigrationEventRecord:
        stream_id = _stream_id(raw_id)
        fields = {_field_text(key): value for key, value in raw_fields.items()}
        raw_event = fields.get(MIGRATION_EVENT_FIELD)
        if raw_event is None:
            raise MigrationEventDecodeError("migration stream event field is missing")
        try:
            event = MigrationEvent.model_validate_json(_field_text(raw_event))
        except (TypeError, ValueError, ValidationError):
            raise MigrationEventDecodeError(
                "migration stream event is invalid"
            ) from None
        return MigrationEventRecord(stream_id=stream_id, event=event)


MigrationEventStream = MigrationEventService
MigrationEventsService = MigrationEventService


__all__ = [
    "MIGRATION_EVENT_FIELD",
    "MIGRATION_EVENT_STREAM_MAXLEN",
    "MIGRATION_EVENT_STREAM_TTL_SECONDS",
    "MigrationEventDecodeError",
    "MigrationEventRecord",
    "MigrationEventService",
    "MigrationEventStoreError",
    "MigrationEventStream",
    "MigrationEventsService",
    "RedisStreamClient",
    "migration_event_key",
    "migration_stream_key",
]
