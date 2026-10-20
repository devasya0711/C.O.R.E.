"""Focused contract and Redis Streams tests for migration progress events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid4

import pytest
from app.schemas.migrations import (
    MigrationAccepted,
    MigrationCreate,
    MigrationEvent,
    MigrationEventType,
    MigrationPRRequest,
    MigrationPRResponse,
)
from app.services.migration_events import (
    MIGRATION_EVENT_STREAM_MAXLEN,
    MIGRATION_EVENT_STREAM_TTL_SECONDS,
    MigrationEventService,
)
from pydantic import ValidationError


class FakeRedisStreams:
    """Async Redis Streams-compatible stub with exact retention semantics."""

    def __init__(self) -> None:
        self.entries: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.xadd_calls: list[tuple[str, int | None, bool]] = []
        self.expire_calls: list[tuple[str, int]] = []
        self._next_id = 0

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._next_id += 1
        entry_id = f"{self._next_id}-0"
        stream = self.entries.setdefault(name, [])
        stream.append((entry_id, dict(fields)))
        self.xadd_calls.append((name, maxlen, approximate))
        if maxlen is not None:
            del stream[:-maxlen]
        return entry_id

    async def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> Sequence[tuple[object, Mapping[object, object]]]:
        del max
        stream = self.entries.get(name, [])
        exclusive = min.startswith("(")
        start = min.removeprefix("(")
        selected = [
            entry
            for entry in stream
            if min == "-" or (entry[0] > start if exclusive else entry[0] >= start)
        ]
        if count is not None:
            selected = selected[:count]
        return [(entry_id, fields) for entry_id, fields in selected]

    async def expire(self, name: str, time: int) -> bool:
        self.expire_calls.append((name, time))
        return True


def _event(
    job_id: UUID,
    repo_id: UUID,
    event_type: MigrationEventType,
    payload: Mapping[str, object] | None = None,
) -> MigrationEvent:
    return MigrationEvent(
        job_id=job_id,
        repo_id=repo_id,
        event_type=event_type,
        payload=payload or {},
    )


def test_migration_contracts_are_strict_and_validate_fields() -> None:
    seam_id = uuid4()
    job_id = uuid4()
    assert MigrationCreate(seam_id=seam_id, create_github_pr=True).seam_id == seam_id
    assert MigrationAccepted(job_id=job_id).status == "QUEUED"
    assert MigrationPRRequest(base_branch="release", title="Extract payments")
    assert (
        MigrationPRResponse(
            job_id=job_id,
            pr_url="https://github.com/example/repo/pull/1",
            pr_number=1,
        ).status
        == "PR_CREATED"
    )

    with pytest.raises(ValidationError):
        MigrationCreate.model_validate(
            {"seam_id": str(seam_id), "create_github_pr": "true"}
        )
    with pytest.raises(ValidationError):
        MigrationEvent(
            job_id=job_id,
            repo_id=uuid4(),
            event_type="UNKNOWN",
        )
    with pytest.raises(ValidationError):
        MigrationPRRequest.model_validate({"base_branch": "main", "unexpected": 1})


def test_event_payload_is_json_safe_and_redacts_sensitive_fields() -> None:
    event = _event(
        uuid4(),
        uuid4(),
        MigrationEventType.MIGRATION_FAILED,
        {
            "error_code": "MIGRATION_FAILED",
            "token": "do-not-publish",
            "exception": "raw provider exception body",
            "nested": {"api_key": "also-secret", "retry": 2},
        },
    )

    serialized = event.model_dump_json()
    assert "do-not-publish" not in serialized
    assert "raw provider exception body" not in serialized
    assert event.payload["token"] == "[REDACTED]"
    assert event.payload["nested"] == {
        "api_key": "[REDACTED]",
        "retry": 2,
    }

    with pytest.raises(ValidationError):
        _event(uuid4(), uuid4(), MigrationEventType.AGENT_START, {"invalid": {1, 2}})


@pytest.mark.asyncio
async def test_publish_replay_resume_order_retention_and_terminal_detection() -> None:
    redis = FakeRedisStreams()
    service = MigrationEventService(redis)
    job_id, repo_id = uuid4(), uuid4()

    first_id = await service.emit(
        job_id, repo_id, MigrationEventType.AGENT_START, {"agent": "crag"}
    )
    second_id = await service.emit(
        job_id, repo_id, MigrationEventType.SANDBOX_ITERATION, {"attempt": 1}
    )
    terminal_id = await service.emit(job_id, repo_id, MigrationEventType.COMPLETED)

    records = await service.read_events(job_id)
    assert [record.id for record in records] == [first_id, second_id, terminal_id]
    assert [record.event_type for record in records] == [
        MigrationEventType.AGENT_START,
        MigrationEventType.SANDBOX_ITERATION,
        MigrationEventType.COMPLETED,
    ]
    assert records[-1].terminal and service.is_terminal(records[-1].event)

    resumed = [
        record
        async for record in service.replay_events(
            job_id, last_event_id=first_id, stop_at_terminal=False
        )
    ]
    assert [record.id for record in resumed] == [second_id, terminal_id]

    assert len(redis.xadd_calls) == 3
    assert all(
        maxlen == MIGRATION_EVENT_STREAM_MAXLEN and approximate is False
        for _, maxlen, approximate in redis.xadd_calls
    )
    assert all(
        ttl == MIGRATION_EVENT_STREAM_TTL_SECONDS for _, ttl in redis.expire_calls
    )


@pytest.mark.asyncio
async def test_failure_event_hides_exception_and_rejects_invalid_cursor() -> None:
    redis = FakeRedisStreams()
    service = MigrationEventService(redis)
    job_id, repo_id = uuid4(), uuid4()
    entry_id = await service.publish_failure(
        job_id, repo_id, RuntimeError("secret provider response")
    )
    stored = redis.entries[next(iter(redis.entries))][0][1]["event"]
    assert entry_id == "1-0"
    assert "secret provider response" not in stored
    assert "RuntimeError" in stored

    with pytest.raises(ValueError):
        await service.read_events(job_id, last_event_id="not-a-stream-id")
