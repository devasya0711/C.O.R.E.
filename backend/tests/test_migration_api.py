"""Focused API contract tests for migration SSE replay and terminal handling."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.api.v1 import migrations as migration_api
from app.api.v1.migrations import (
    create_migration,
    create_migration_pr,
    migration_stream,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.schemas.graph import CodeGraph, CodeNode
from app.schemas.migrations import (
    MigrationCreate,
    MigrationEventType,
    MigrationPRRequest,
    MigrationPRResponse,
)
from fastapi import FastAPI
from starlette.requests import Request

from test_migration_events import FakeRedisStreams


class _FakeGraph:
    def __init__(self, snapshot: str = "snapshot-1") -> None:
        self.snapshot = snapshot

    async def snapshot_version(self, repo_id: object) -> str:
        del repo_id
        return self.snapshot

    async def load_graph(self, repo_id: object) -> CodeGraph:
        del repo_id
        return CodeGraph(
            nodes=[
                CodeNode(
                    label="Function",
                    qualified_name="payments.pay",
                    name="pay",
                    file_path="payments.py",
                    start_line=1,
                    end_line=2,
                    source_code="def pay() -> None:\n    return None\n",
                )
            ],
            edges=[],
        )


class _FakeSession:
    def __init__(self, seam: object | None = None) -> None:
        self.seam = seam
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.seam

    async def flush(self) -> None:
        job = self.added[-1]
        job.id = uuid4()

    async def commit(self) -> None:
        self.commits += 1


class _FakeMigrationService:
    def __init__(self) -> None:
        self.states: list[dict[str, object]] = []

    def start(self, state: dict[str, object]) -> None:
        self.states.append(state)

    async def publish_job(
        self,
        job: object,
        repository: object,
        seam: object,
        user: object,
        payload: MigrationPRRequest,
    ) -> MigrationPRResponse:
        del repository, seam, user, payload
        return MigrationPRResponse(
            job_id=job.id,
            pr_url="https://github.com/example/core/pull/7",
            pr_number=7,
            branch="refactor/extract-payments",
        )


def _request(app: FastAPI) -> Request:
    app.state.neo4j = object()
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/repos/migrations/stream",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )


@pytest.mark.asyncio
async def test_sse_terminal_reconnect_returns_without_waiting() -> None:
    app = FastAPI()
    redis = FakeRedisStreams()
    app.state.redis = redis
    job_id, repo_id, user_id = uuid4(), uuid4(), uuid4()
    events = migration_api.MigrationEventService(redis)
    terminal_id = await events.emit(job_id, repo_id, MigrationEventType.COMPLETED)
    job = SimpleNamespace(create_github_pr=False, status="COMPLETED")

    async def owned_job(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return job

    original = migration_api._owned_job
    migration_api._owned_job = owned_job  # type: ignore[assignment]
    try:
        response = await migration_stream(
            repo_id,
            job_id,
            _request(app),
            SimpleNamespace(id=repo_id),  # type: ignore[arg-type]
            SimpleNamespace(id=user_id),  # type: ignore[arg-type]
            SimpleNamespace(),  # type: ignore[arg-type]
            last_event_id=terminal_id,
        )
        chunks = [chunk async for chunk in response.body_iterator]
    finally:
        migration_api._owned_job = original

    assert chunks == []


@pytest.mark.asyncio
async def test_create_migration_validates_snapshot_and_starts_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_id, user_id, seam_id = uuid4(), uuid4(), uuid4()
    repository = SimpleNamespace(
        id=repo_id,
        user_id=user_id,
        status="READY",
        last_commit_sha="a" * 64,
        repo_url="local://sample_monolith",
        default_branch="main",
    )
    seam = SimpleNamespace(
        id=seam_id,
        repo_id=repo_id,
        snapshot_version="snapshot-1",
        node_ids=["payments.pay"],
        suggested_name="payments",
    )
    session = _FakeSession(seam)
    service = _FakeMigrationService()
    app = FastAPI()
    app.state.neo4j = object()
    request = _request(app)
    monkeypatch.setattr(
        migration_api,
        "_lock_repository",
        lambda _session, value: _async_value(value),
    )
    monkeypatch.setattr(migration_api, "_service", lambda _request: service)
    monkeypatch.setattr(migration_api, "Neo4jService", lambda _driver: _FakeGraph())

    result = await create_migration(
        repo_id,
        MigrationCreate(seam_id=seam_id),
        request,
        repository,  # type: ignore[arg-type]
        SimpleNamespace(id=user_id),  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
    )

    assert result.status == "QUEUED"
    assert session.commits == 1
    assert len(service.states) == 1
    assert service.states[0]["expected_snapshot_version"] == "snapshot-1"


@pytest.mark.asyncio
async def test_create_migration_rejects_stale_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_id, user_id, seam_id = uuid4(), uuid4(), uuid4()
    repository = SimpleNamespace(
        id=repo_id,
        user_id=user_id,
        status="READY",
        last_commit_sha="a" * 64,
        repo_url="local://sample_monolith",
        default_branch="main",
    )
    seam = SimpleNamespace(
        id=seam_id,
        repo_id=repo_id,
        snapshot_version="old",
        node_ids=["payments.pay"],
    )
    monkeypatch.setattr(
        migration_api,
        "_lock_repository",
        lambda _session, value: _async_value(value),
    )
    monkeypatch.setattr(
        migration_api, "_service", lambda _request: _FakeMigrationService()
    )
    monkeypatch.setattr(migration_api, "Neo4jService", lambda _driver: _FakeGraph())
    with pytest.raises(ApiError, match="Re-run seam analysis") as error:
        await create_migration(
            repo_id,
            MigrationCreate(seam_id=seam_id),
            _request(FastAPI()),
            repository,  # type: ignore[arg-type]
            SimpleNamespace(id=user_id),  # type: ignore[arg-type]
            _FakeSession(seam),  # type: ignore[arg-type]
        )
    assert error.value.status_code == 409


async def _async_value(value: object) -> object:
    return value


@pytest.mark.asyncio
async def test_manual_pr_endpoint_checks_job_identifier_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_id, user_id, seam_id, job_id = uuid4(), uuid4(), uuid4(), uuid4()
    repository = SimpleNamespace(
        id=repo_id,
        user_id=user_id,
        status="READY",
        default_branch="main",
    )
    seam = SimpleNamespace(id=seam_id, repo_id=repo_id)
    job = SimpleNamespace(
        id=job_id,
        repo_id=repo_id,
        seam_id=seam_id,
        status="COMPLETED",
        sandbox_passed=True,
    )
    service = _FakeMigrationService()
    monkeypatch.setattr(
        migration_api,
        "_lock_repository",
        lambda _session, value, **_kwargs: _async_value(value),
    )
    monkeypatch.setattr(migration_api, "_owned_job", lambda *args: _async_value(job))
    monkeypatch.setattr(migration_api, "_service", lambda _request: service)
    session = _FakeSession(seam)

    result = await create_migration_pr(
        repo_id,
        job_id,
        MigrationPRRequest(job_id=job_id),
        _request(FastAPI()),
        repository,  # type: ignore[arg-type]
        SimpleNamespace(id=user_id),  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
    )

    assert result.pr_number == 7


@pytest.mark.asyncio
async def test_cors_allows_last_event_id_reconnect_header() -> None:
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        response = await client.options(
            "/api/v1/repos/00000000-0000-0000-0000-000000000000/migrations/stream",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,last-event-id",
            },
        )

    assert response.status_code == 200
    assert "last-event-id" in response.headers["access-control-allow-headers"].lower()
