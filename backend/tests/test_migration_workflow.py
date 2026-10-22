"""Focused execution-path tests for the Phase 5 LangGraph workflow."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.redaction import SecretRedactor
from app.schemas.graph import CodeGraph, CodeNode
from app.schemas.migrations import MigrationEvent, MigrationEventType
from app.workflows.migration_graph import (
    MigrationDependencies,
    MigrationState,
    build_migration_graph,
)
from pydantic import SecretStr

pytest.importorskip("langgraph")


def _graph(*, source_code: str = "def pay() -> None:\n    return None\n") -> CodeGraph:
    return CodeGraph(
        nodes=[
            CodeNode(
                label="Function",
                qualified_name="payments.pay",
                name="pay",
                file_path="payments.py",
                start_line=1,
                end_line=2,
                source_code=source_code,
            )
        ],
        edges=[],
    )


def _graph_with_unrelated_node() -> CodeGraph:
    graph = _graph()
    graph.nodes.append(
        CodeNode(
            label="Function",
            qualified_name="orders.place",
            name="place",
            file_path="orders.py",
            start_line=1,
            end_line=2,
            source_code="def place() -> None:\n    return None\n",
        )
    )
    return graph


class FakeGraph:
    def __init__(
        self, context: CodeGraph, versions: list[str], *, lease_valid: bool = True
    ) -> None:
        self.context = context
        self.versions = versions
        self.lease_is_valid = lease_valid
        self.snapshot_calls = 0

    async def retrieve_seam_context(self, repo_id: UUID, seam_id: UUID) -> CodeGraph:
        del repo_id, seam_id
        return self.context

    async def snapshot_version(self, repo_id: UUID) -> str:
        del repo_id
        index = min(self.snapshot_calls, len(self.versions) - 1)
        self.snapshot_calls += 1
        return self.versions[index]

    async def lease_valid(self, repo_id: UUID) -> bool:
        del repo_id
        return self.lease_is_valid


class FakeCRAG:
    def __init__(
        self, *, sufficient: bool, missing_files: list[str] | None = None
    ) -> None:
        self.sufficient = sufficient
        self.missing_files = missing_files or []
        self.contexts: list[object] = []

    async def evaluate(self, context: object) -> Mapping[str, object]:
        self.contexts.append(context)
        return {
            "decision": (
                "context_sufficient" if self.sufficient else "context_incomplete"
            ),
            "missing_files": self.missing_files,
            "reasons": [] if self.sufficient else ["missing source context"],
        }


class FakeCoder:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.requests: list[object] = []
        self.error = error

    async def generate(self, request: object) -> Mapping[str, object]:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return {"artifacts": {"service.py": "generated service\n"}}


class FakeSandbox:
    def __init__(self, results: list[Mapping[str, object]]) -> None:
        self.results = list(results)
        self.artifacts: list[Mapping[str, str]] = []

    async def run(self, artifacts: Mapping[str, str]) -> Mapping[str, object]:
        self.artifacts.append(artifacts)
        return self.results.pop(0)


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def create_pull_request(self, request: object) -> Mapping[str, object]:
        self.requests.append(request)
        return {
            "pr_url": "https://github.com/example/core/pull/42",
            "pr_number": 42,
            "branch": "refactor/extract-payments",
        }


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[MigrationEvent] = []

    async def publish_event(self, event: MigrationEvent) -> None:
        self.events.append(event)


class FailingPublisher(FakePublisher):
    async def publish_event(self, event: MigrationEvent) -> None:
        del event
        raise RuntimeError("redis unavailable")


class OrderedPublisher(FakePublisher):
    def __init__(self, job: object) -> None:
        super().__init__()
        self.job = job
        self.statuses_at_publish: list[str] = []

    async def publish_event(self, event: MigrationEvent) -> None:
        self.statuses_at_publish.append(str(getattr(self.job, "status", "")))
        await super().publish_event(event)


class FakeTransaction:
    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSession:
    def __init__(self, job: object, repository: object, seam: object) -> None:
        self.job = job
        self.repository = repository
        self.seam = seam

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, model: type[object], key: UUID) -> object | None:
        del key
        name = model.__name__
        return {
            "MigrationJob": self.job,
            "Repository": self.repository,
            "SeamCluster": self.seam,
        }.get(name)

    def begin(self) -> FakeTransaction:
        return FakeTransaction()


class FakeJob:
    def __init__(self, repo_id: UUID, seam_id: UUID) -> None:
        self.repo_id = repo_id
        self.seam_id = seam_id
        self.status = "QUEUED"
        self.crag_status = "PENDING"
        self.retry_count = 0
        self.generated_artifacts: dict[str, str] = {}
        self.sandbox_logs: str | None = None
        self.pr_url: str | None = None


class FakeRepository:
    last_commit_sha = "commit-1"
    repo_url = "https://github.com/example/core.git"
    default_branch = "main"


class FakeSeam:
    suggested_name = "payments"
    node_ids: list[str] = []


def _state(*, create_github_pr: bool = False) -> MigrationState:
    return {
        "job_id": uuid4(),
        "repo_id": uuid4(),
        "seam_id": uuid4(),
        "seam_name": "payments",
        "create_github_pr": create_github_pr,
        "expected_snapshot_version": "snapshot-1",
    }


def _dependencies(
    *,
    context: CodeGraph | None = None,
    crag: FakeCRAG | None = None,
    coder: FakeCoder | None = None,
    sandbox: FakeSandbox | None = None,
    github: FakeGitHub | None = None,
    source_reader: Mapping[str, str] | None = None,
    publisher: FakePublisher | None = None,
    versions: list[str] | None = None,
    lease_valid: bool = True,
    secret_redactor: SecretRedactor | None = None,
) -> tuple[
    MigrationDependencies,
    FakeGraph,
    FakeCRAG,
    FakeCoder,
    FakeSandbox,
    FakePublisher,
]:
    graph = FakeGraph(
        context or _graph(), versions or ["snapshot-1"], lease_valid=lease_valid
    )
    evaluator = crag or FakeCRAG(sufficient=True)
    refactor_coder = coder or FakeCoder()
    runner = sandbox or FakeSandbox([{"passed": True, "stdout": "ok", "stderr": ""}])
    events = publisher or FakePublisher()
    return (
        MigrationDependencies(
            graph=graph,
            source_reader=source_reader,
            crag_evaluator=evaluator,
            coder=refactor_coder,
            sandbox=runner,
            github_service=github,
            event_publisher=events,
            secret_redactor=secret_redactor,
        ),
        graph,
        evaluator,
        refactor_coder,
        runner,
        events,
    )


@pytest.mark.asyncio
async def test_sufficient_context_passes_without_pr_and_orders_events() -> None:
    dependencies, _graph_service, _crag, coder, sandbox, publisher = _dependencies()

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "COMPLETED"
    assert result["sandbox_passed"] is True
    assert len(coder.requests) == 1
    assert len(sandbox.artifacts) == 1
    assert [event.event_type for event in publisher.events] == [
        MigrationEventType.AGENT_START,
        MigrationEventType.CRAG_DECISION,
        MigrationEventType.ARTIFACTS_READY,
        MigrationEventType.SANDBOX_ITERATION,
        MigrationEventType.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_crag_receives_only_the_selected_seam_context() -> None:
    crag = FakeCRAG(sufficient=True)
    dependencies, _graph_service, evaluator, _coder, _sandbox, _publisher = (
        _dependencies(context=_graph_with_unrelated_node(), crag=crag)
    )
    state = _state()
    state["seam_node_ids"] = ["payments.pay"]

    result = await build_migration_graph(dependencies).ainvoke(state)

    assert result["status"] == "COMPLETED"
    context = evaluator.contexts[0]
    assert isinstance(context, CodeGraph)
    assert [node.qualified_name for node in context.nodes] == ["payments.pay"]


@pytest.mark.asyncio
async def test_create_github_pr_uses_the_injected_service() -> None:
    initial = _state(create_github_pr=True)
    github = FakeGitHub()
    dependencies, _graph_service, _crag, _coder, _sandbox, publisher = _dependencies(
        github=github
    )
    job = FakeJob(initial["repo_id"], initial["seam_id"])

    def session_factory() -> FakeSession:
        return FakeSession(job, FakeRepository(), FakeSeam())

    dependencies.session_factory = session_factory

    result = await build_migration_graph(dependencies).ainvoke(initial)

    assert result["status"] == "COMPLETED"
    assert result["pr_url"] == "https://github.com/example/core/pull/42"
    assert len(github.requests) == 1
    assert job.pr_url == "https://github.com/example/core/pull/42"
    assert publisher.events[-1].event_type is MigrationEventType.PR_CREATED


@pytest.mark.asyncio
async def test_job_updates_use_fresh_sessions_before_events() -> None:
    initial = _state()
    job = FakeJob(initial["repo_id"], initial["seam_id"])
    sessions: list[FakeSession] = []

    def session_factory() -> FakeSession:
        session = FakeSession(job, FakeRepository(), FakeSeam())
        sessions.append(session)
        return session

    publisher = OrderedPublisher(job)
    dependencies, _graph_service, _crag, _coder, _sandbox, _publisher = _dependencies(
        publisher=publisher
    )
    dependencies.session_factory = session_factory

    result = await build_migration_graph(dependencies).ainvoke(initial)

    assert result["status"] == "COMPLETED"
    assert len(sessions) >= 5
    assert job.status == "COMPLETED"
    assert job.generated_artifacts == {"service.py": "generated service\n"}
    assert job.sandbox_logs is not None
    assert publisher.statuses_at_publish == [
        "CRAG_EVAL",
        "CRAG_EVAL",
        "CODING",
        "TESTING",
        "COMPLETED",
    ]


@pytest.mark.asyncio
async def test_incomplete_context_augments_source_before_coding() -> None:
    crag = FakeCRAG(sufficient=False, missing_files=["payments/types.py"])
    coder = FakeCoder()
    dependencies, _graph_service, _crag, _coder, _sandbox, _publisher = _dependencies(
        context=_graph(source_code=""),
        crag=crag,
        coder=coder,
        source_reader={"payments/types.py": "PaymentStatus = str\n"},
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "COMPLETED"
    request = coder.requests[0]
    assert request.source_files["payments/types.py"] == "PaymentStatus = str\n"
    assert result["crag_status"] == "AUGMENTED"


@pytest.mark.asyncio
async def test_sandbox_retry_passes_stderr_back_to_the_coder() -> None:
    coder = FakeCoder()
    sandbox = FakeSandbox(
        [
            {"passed": False, "stdout": "", "stderr": "assertion from attempt one"},
            {"passed": True, "stdout": "ok", "stderr": ""},
        ]
    )
    dependencies, _graph_service, _crag, _coder, _sandbox, publisher = _dependencies(
        coder=coder, sandbox=sandbox
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "COMPLETED"
    assert result["retry_count"] == 1
    assert len(coder.requests) == 2
    assert coder.requests[1].retry_count == 1
    assert coder.requests[1].prior_sandbox_stderr == "assertion from attempt one"
    assert [event.event_type for event in publisher.events].count(
        MigrationEventType.SANDBOX_ITERATION
    ) == 2


@pytest.mark.asyncio
async def test_sandbox_logs_redact_secrets_before_retry_and_persistence() -> None:
    secret = "configured-sandbox-secret"
    sandbox = FakeSandbox(
        [
            {
                "passed": False,
                "stdout": f"stdout leaked {secret}",
                "stderr": f"stderr leaked {secret}",
            },
            {
                "passed": False,
                "stdout": f"stdout leaked again {secret}",
                "stderr": f"stderr leaked again {secret}",
            },
            {
                "passed": False,
                "stdout": f"stdout leaked final {secret}",
                "stderr": f"stderr leaked final {secret}",
            },
        ]
    )
    coder = FakeCoder()
    initial = _state()
    job = FakeJob(initial["repo_id"], initial["seam_id"])
    dependencies, _graph_service, _crag, _coder, _sandbox, _publisher = _dependencies(
        coder=coder,
        sandbox=sandbox,
        secret_redactor=SecretRedactor.from_settings(
            Settings(_env_file=None, openai_api_key=SecretStr(secret))
        ),
    )
    dependencies.session_factory = lambda: FakeSession(
        job, FakeRepository(), FakeSeam()
    )

    result = await build_migration_graph(dependencies).ainvoke(initial)

    assert result["status"] == "FAILED"
    assert secret not in str(job.sandbox_logs)
    assert secret not in result["sandbox_logs"]
    assert all(secret not in request.prior_sandbox_stderr for request in coder.requests)
    assert "[REDACTED]" in str(job.sandbox_logs)


@pytest.mark.asyncio
async def test_three_sandbox_failures_terminally_require_human_review() -> None:
    sandbox = FakeSandbox(
        [
            {"passed": False, "stdout": "", "stderr": "failure one"},
            {"passed": False, "stdout": "", "stderr": "failure two"},
            {"passed": False, "stdout": "", "stderr": "failure three"},
        ]
    )
    dependencies, _graph_service, _crag, coder, _sandbox, publisher = _dependencies(
        sandbox=sandbox
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "FAILED"
    assert result["human_review"] is True
    assert result["retry_count"] == 3
    assert len(coder.requests) == 3
    assert publisher.events[-1].event_type is MigrationEventType.HUMAN_REVIEW
    assert publisher.events[-1].payload["status"] == "FAILED"


@pytest.mark.asyncio
async def test_stale_snapshot_is_checked_before_coding() -> None:
    dependencies, graph, _crag, coder, _sandbox, publisher = _dependencies(
        versions=["snapshot-1", "snapshot-2"]
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert graph.snapshot_calls == 2
    assert result["status"] == "FAILED"
    assert coder.requests == []
    assert publisher.events[-1].event_type is MigrationEventType.MIGRATION_FAILED
    assert publisher.events[-1].payload["error_code"] == (
        "MIGRATION_REFACTOR_CODING_FAILED"
    )


@pytest.mark.asyncio
async def test_lost_repository_lease_blocks_coding_and_sandbox() -> None:
    dependencies, graph, _crag, coder, sandbox, publisher = _dependencies(
        lease_valid=False
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "FAILED"
    assert result["error_stage"] == "REFACTOR_CODING"
    assert graph.snapshot_calls == 1
    assert coder.requests == []
    assert sandbox.artifacts == []
    assert publisher.events[-1].event_type is MigrationEventType.MIGRATION_FAILED


@pytest.mark.asyncio
async def test_provider_failure_is_persisted_as_a_safe_failed_event() -> None:
    coder = FakeCoder(error=RuntimeError("provider response contains a secret"))
    dependencies, _graph_service, _crag, _coder, _sandbox, publisher = _dependencies(
        coder=coder
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "FAILED"
    assert publisher.events[-1].event_type is MigrationEventType.MIGRATION_FAILED
    serialized = publisher.events[-1].model_dump_json()
    assert "provider response contains a secret" not in serialized


@pytest.mark.asyncio
async def test_event_delivery_failure_does_not_fail_durable_completion() -> None:
    dependencies, _graph_service, _crag, _coder, _sandbox, _publisher = _dependencies(
        publisher=FailingPublisher()
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_final_snapshot_change_blocks_completion_without_pr() -> None:
    dependencies, _graph_service, _crag, _coder, _sandbox, publisher = _dependencies(
        versions=["snapshot-1", "snapshot-1", "snapshot-2"]
    )

    result = await build_migration_graph(dependencies).ainvoke(_state())

    assert result["status"] == "FAILED"
    assert publisher.events[-1].event_type is MigrationEventType.MIGRATION_FAILED


@pytest.mark.asyncio
async def test_reduced_isolation_blocks_automatic_pr_creation() -> None:
    github = FakeGitHub()
    sandbox = FakeSandbox(
        [
            {
                "passed": True,
                "stdout": "ok",
                "stderr": "",
                "isolation": "subprocess",
                "isolation_note": "shared host network",
            }
        ]
    )
    dependencies, _graph_service, _crag, _coder, _sandbox, publisher = _dependencies(
        github=github, sandbox=sandbox
    )

    result = await build_migration_graph(dependencies).ainvoke(
        _state(create_github_pr=True)
    )

    assert result["status"] == "FAILED"
    assert result["error_code"] == "SANDBOX_ISOLATION_UNAVAILABLE"
    assert github.requests == []
    assert publisher.events[-1].event_type is MigrationEventType.MIGRATION_FAILED
