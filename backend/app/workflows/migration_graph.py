"""LangGraph orchestration for execution-verified seam migrations.

The workflow owns orchestration only.  Repository access, CRAG evaluation,
code generation, sandbox execution, GitHub publication, persistence, and event
delivery are all dependency-injected so the same graph can run with the real
services or deterministic test fakes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, cast
from uuid import UUID

from app.agents.crag_evaluator import CRAGEvaluationResult
from app.agents.refactor_coder import RefactorCoderRequest
from app.core.redaction import SecretRedactor
from app.db.models import MigrationJob, Repository, SeamCluster
from app.schemas.graph import CodeEdge, CodeGraph, CodeNode
from app.schemas.migrations import MigrationEvent, MigrationEventType

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

try:  # LangGraph is optional for importing the application in minimal tooling.
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.state import CompiledStateGraph
except ImportError:  # pragma: no cover - exercised only without the optional SDK.
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment,misc]
    CompiledStateGraph = object  # type: ignore[misc,assignment]


CRAGDecision = Literal["context_sufficient", "context_incomplete"]
SandboxRoute = Literal[
    "tests_passed",
    "tests_passed_no_pr",
    "tests_failed_retry",
    "max_retries_exceeded",
    "workflow_failed",
]
WorkflowValue = object | Awaitable[object]
SessionFactory = Callable[[], object]
EventPublisher = object


class MigrationState(TypedDict, total=False):
    """State carried between LangGraph migration nodes.

    The input fields are the first group.  The remaining fields are populated
    by nodes and intentionally contain only bounded, non-secret workflow data.
    Provider credentials stay inside the injected service/request factory.
    """

    job_id: UUID
    repo_id: UUID
    seam_id: UUID
    create_github_pr: bool
    expected_snapshot_version: str
    expected_snapshot: str
    expected_ingestion_snapshot: str
    expected_ingestion_version: str
    expected_commit_sha: str
    source_commit_sha: str
    ingestion_snapshot: str
    graph_context: CodeGraph | Mapping[str, object]
    context: CodeGraph | Mapping[str, object]
    repo_url: str
    base_branch: str
    last_commit_sha: str
    dependency_diagram: str
    blast_radius_summary: str
    repository: object
    seam: object
    seam_name: str
    seam_node_ids: list[str]
    seam_nodes: list[CodeNode]
    seam_edges: list[CodeEdge]
    warnings: list[str]
    source_files: dict[str, str]
    sandbox_source_files: dict[str, str]
    crag_result: CRAGEvaluationResult | Mapping[str, object]
    crag_decision: CRAGDecision
    crag_status: Literal["PENDING", "SUFFICIENT", "AUGMENTED"]
    crag_reasons: list[str]
    missing_files: list[str]
    augmented_sources: dict[str, str]
    retry_count: int
    sandbox_stderr: str
    sandbox_stdout: str
    sandbox_logs: str
    sandbox_passed: bool
    sandbox_timed_out: bool
    sandbox_isolation: str
    sandbox_isolation_note: str
    generated_artifacts: dict[str, str]
    artifacts: dict[str, str]
    pr_url: str
    pr_number: int
    branch: str
    status: str
    human_review: bool
    workflow_error: bool
    error_code: str
    error_stage: str
    error_type: str
    migration_job: object
    github_request: object


class GraphService(Protocol):
    """Minimum graph boundary used by the migration workflow."""

    def snapshot_version(self, repo_id: UUID) -> WorkflowValue: ...


class CRAGEvaluator(Protocol):
    """CRAG evaluator boundary; concrete implementations may be sync/async."""

    def evaluate(self, context: object) -> WorkflowValue: ...


class RefactorCoder(Protocol):
    """Structured artifact generator boundary."""

    def generate(self, request: object) -> WorkflowValue: ...


class Sandbox(Protocol):
    """Pytest sandbox boundary."""

    def run(self, artifacts: Mapping[str, str]) -> WorkflowValue: ...


class GitHubService(Protocol):
    """Optional pull-request publication boundary."""

    def create_pull_request(self, request: object) -> WorkflowValue: ...


@dataclass
class MigrationDependencies:
    """Injectable boundaries required by the compiled migration graph.

    The ``*_service`` aliases make the container convenient for callers that
    use the application service naming convention while the shorter names keep
    test fixtures readable.  Exactly one spelling is required for each
    non-optional service.
    """

    graph: object | None = None
    source_reader: object | None = None
    crag_evaluator: object | None = None
    coder: object | None = None
    sandbox: object | None = None
    github_service: object | None = None
    session_factory: SessionFactory | None = None
    event_publisher: EventPublisher | None = None
    graph_service: object | None = None
    github: object | None = None
    github_request_factory: Callable[[MigrationState], object] | None = None
    secret_redactor: SecretRedactor | None = None

    def resolved_graph(self) -> object:
        value = self.graph_service or self.graph
        if value is None:
            raise RuntimeError("Migration graph service is not configured.")
        return value

    def resolved_crag(self) -> object:
        if self.crag_evaluator is None:
            raise RuntimeError("Migration CRAG evaluator is not configured.")
        return self.crag_evaluator

    def resolved_coder(self) -> object:
        if self.coder is None:
            raise RuntimeError("Migration refactor coder is not configured.")
        return self.coder

    def resolved_sandbox(self) -> object:
        if self.sandbox is None:
            raise RuntimeError("Migration sandbox is not configured.")
        return self.sandbox

    def resolved_github(self) -> object | None:
        return self.github_service or self.github


MigrationWorkflowDependencies = MigrationDependencies


class LangGraphUnavailable(RuntimeError):
    """Raised when production attempts to build without LangGraph installed."""


class MigrationSnapshotChanged(RuntimeError):
    """Raised when the graph no longer describes the expected ingestion."""


class MigrationWorkflowError(RuntimeError):
    """Credential-free internal workflow failure used for safe terminal state."""


def _is_control_flow_error(error: BaseException) -> bool:
    return isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))


def _is_awaitable(value: object) -> bool:
    return inspect.isawaitable(value)


async def _resolve(value: WorkflowValue) -> object:
    if _is_awaitable(value):
        return await cast(Awaitable[object], value)
    return value


def _uuid(state: MigrationState, key: str) -> UUID:
    value = state.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    raise MigrationWorkflowError(f"Missing migration identifier: {key}.")


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _redact_text(dependencies: MigrationDependencies, value: str) -> str:
    if dependencies.secret_redactor is None:
        return value
    return dependencies.secret_redactor.redact(value)


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _method(target: object, names: Sequence[str]) -> Callable[..., object] | None:
    for name in names:
        candidate = getattr(target, name, None)
        if callable(candidate):
            return cast(Callable[..., object], candidate)
    if callable(target):
        return cast(Callable[..., object], target)
    return None


async def _call(
    target: object,
    names: Sequence[str],
    *args: object,
    **kwargs: object,
) -> object:
    candidate = _method(target, names)
    if candidate is None:
        names_text = ", ".join(names)
        raise MigrationWorkflowError(f"Injected service lacks {names_text}.")
    try:
        result = candidate(*args, **kwargs)
    except TypeError as first_error:
        # Small fakes often expose keyword-only boundaries.  Retrying only the
        # call shape keeps the graph independent of a concrete provider class.
        if not kwargs:
            raise
        try:
            result = candidate(*args)
        except TypeError:
            raise first_error from None
    return await _resolve(result)


def _event(
    state: MigrationState,
    event_type: MigrationEventType,
    payload: Mapping[str, object] | None = None,
) -> MigrationEvent:
    return MigrationEvent(
        job_id=_uuid(state, "job_id"),
        repo_id=_uuid(state, "repo_id"),
        event_type=event_type,
        payload=dict(payload or {}),
    )


async def _publish(dependencies: MigrationDependencies, event: MigrationEvent) -> None:
    publisher = dependencies.event_publisher
    if publisher is None:
        return
    publish_event = getattr(publisher, "publish_event", None)
    publish = getattr(publisher, "publish", None)
    emit = getattr(publisher, "emit", None)
    if callable(publish_event):
        await _resolve(publish_event(event))
        return
    if callable(publish):
        await _resolve(publish(event))
        return
    if callable(emit):
        await _resolve(
            emit(event.job_id, event.repo_id, event.event_type, event.payload)
        )
        return
    if callable(publisher):
        await _resolve(publisher(event))
        return
    raise MigrationWorkflowError("Injected migration event publisher is invalid.")


async def _observed_agent(
    agent: str,
    state: MigrationState,
    operation: Callable[[], Awaitable[MigrationState]],
) -> MigrationState:
    """Run one workflow node and emit structured latency/retry telemetry."""

    started = asyncio.get_running_loop().time()
    retry_count = state.get("retry_count", 0)
    retry_value = retry_count if type(retry_count) is int else 0
    try:
        result = await operation()
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        _LOGGER.exception(
            "agent_execution_failed",
            extra={
                "event": "agent_execution",
                "agent": agent,
                "latency_ms": round(
                    (asyncio.get_running_loop().time() - started) * 1000, 2
                ),
                "retry_count": retry_value,
                "crag_retry_count": retry_value,
                "status": "failed",
            },
        )
        raise
    result_retry_count = result.get("retry_count", retry_value)
    if type(result_retry_count) is not int:
        result_retry_count = retry_value
    _LOGGER.info(
        "agent_execution",
        extra={
            "event": "agent_execution",
            "agent": agent,
            "latency_ms": round(
                (asyncio.get_running_loop().time() - started) * 1000, 2
            ),
            "retry_count": result_retry_count,
            "crag_retry_count": result_retry_count,
            "status": "failed" if result.get("workflow_error", False) else "completed",
        },
    )
    return result


async def _session_get(
    session: object, model: type[object], key: UUID
) -> object | None:
    getter = getattr(session, "get", None)
    if not callable(getter):
        return None
    return await _resolve(getter(model, key))


async def _read_metadata(
    dependencies: MigrationDependencies, state: MigrationState
) -> dict[str, object]:
    factory = dependencies.session_factory
    if factory is None:
        return {}
    raw_context = factory()
    context = await _resolve(raw_context)
    if not hasattr(context, "__aenter__"):
        raise MigrationWorkflowError(
            "Migration session factory must return an async context."
        )
    async with cast(AbstractAsyncContextManager[object], context) as session:
        job = await _session_get(session, MigrationJob, _uuid(state, "job_id"))
        if job is None:
            return {}
        repository = await _session_get(session, Repository, _uuid(state, "repo_id"))
        seam = await _session_get(session, SeamCluster, _uuid(state, "seam_id"))
        metadata: dict[str, object] = {
            "migration_job": job,
            "repository": repository,
            "seam": seam,
            "retry_count": int(getattr(job, "retry_count", 0)),
            "sandbox_passed": bool(getattr(job, "sandbox_passed", False)),
            "last_commit_sha": getattr(repository, "last_commit_sha", None),
            "repo_url": getattr(repository, "repo_url", None),
            "base_branch": getattr(repository, "default_branch", None),
            "seam_name": getattr(seam, "suggested_name", None),
            "seam_node_ids": list(getattr(seam, "node_ids", []) or []),
        }
        create_pr = getattr(job, "create_github_pr", None)
        snapshot = getattr(job, "snapshot_version", None)
        if isinstance(create_pr, bool):
            metadata["create_github_pr"] = create_pr
        if isinstance(snapshot, str) and snapshot:
            metadata["expected_snapshot_version"] = snapshot
        return metadata


async def _apply_job(
    session: object, state: MigrationState, updates: Mapping[str, object]
) -> None:
    job = await _session_get(session, MigrationJob, _uuid(state, "job_id"))
    if job is None:
        job = state.get("migration_job")
    if job is None:
        return
    expected_repo = _uuid(state, "repo_id")
    actual_repo = getattr(job, "repo_id", expected_repo)
    if actual_repo != expected_repo and str(actual_repo) != str(expected_repo):
        raise MigrationWorkflowError("Migration job belongs to another repository.")
    for key, value in updates.items():
        if isinstance(job, Mapping):
            cast(dict[str, object], job)[key] = value
        else:
            setattr(job, key, value)


async def _persist(
    dependencies: MigrationDependencies,
    state: MigrationState,
    updates: Mapping[str, object],
) -> None:
    state_job = state.get("migration_job")
    if state_job is not None:
        for key, value in updates.items():
            if isinstance(state_job, Mapping):
                cast(dict[str, object], state_job)[key] = value
            else:
                setattr(state_job, key, value)

    factory = dependencies.session_factory
    if factory is None:
        return
    raw_context = factory()
    context = await _resolve(raw_context)
    if not hasattr(context, "__aenter__"):
        raise MigrationWorkflowError(
            "Migration session factory must return an async context."
        )
    async with cast(AbstractAsyncContextManager[object], context) as session:
        begin = getattr(session, "begin", None)
        if callable(begin):
            transaction = begin()
            async with cast(AbstractAsyncContextManager[object], transaction):
                await _apply_job(session, state, updates)
        else:
            await _apply_job(session, state, updates)
            commit = getattr(session, "commit", None)
            if callable(commit):
                await _resolve(commit())


async def _persist_and_publish(
    dependencies: MigrationDependencies,
    state: MigrationState,
    updates: Mapping[str, object],
    event_type: MigrationEventType,
    payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    await _persist(dependencies, state, updates)
    event = _event(state, event_type, payload)
    try:
        await _publish(dependencies, event)
    except Exception:
        # Durable state is authoritative. Redis event delivery is best effort;
        # a transient stream outage must not turn a successful PR into FAILED.
        _LOGGER.warning(
            "migration_event_publish_failed event_type=%s", event_type.value
        )
    return {**updates, "status": updates.get("status", state.get("status", ""))}


def _current_expected_snapshot(state: MigrationState) -> str | None:
    for key in (
        "expected_snapshot_version",
        "expected_snapshot",
        "expected_ingestion_snapshot",
        "expected_ingestion_version",
        "expected_commit_sha",
        "ingestion_snapshot",
    ):
        value = state.get(key)
        if isinstance(value, str):
            return value
    return None


async def _snapshot(
    dependencies: MigrationDependencies,
    state: MigrationState,
    fallback: str | None = None,
) -> str | None:
    graph = dependencies.resolved_graph()
    candidate = _method(
        graph, ("snapshot_version", "get_snapshot_version", "ingestion_snapshot")
    )
    if candidate is not None:
        value = await _resolve(candidate(_uuid(state, "repo_id")))
        if isinstance(value, Mapping):
            value = value.get("version", value.get("snapshot_version"))
        if value is None:
            return fallback
        if not isinstance(value, str):
            raise MigrationWorkflowError("Graph snapshot version is invalid.")
        return value
    return fallback


async def _verify_snapshot(
    dependencies: MigrationDependencies, state: MigrationState
) -> str | None:
    lease_method = _method(dependencies.resolved_graph(), ("lease_valid",))
    if lease_method is not None and not await _resolve(
        lease_method(_uuid(state, "repo_id"))
    ):
        raise MigrationSnapshotChanged("Repository migration lease was lost.")
    expected = _current_expected_snapshot(state)
    current = await _snapshot(dependencies, state)
    if expected is not None and current != expected:
        raise MigrationSnapshotChanged("Repository ingestion snapshot changed.")
    expected_commit = state.get("source_commit_sha")
    source_method = _method(
        dependencies.resolved_graph(), ("source_commit_sha", "get_source_commit_sha")
    )
    if (
        isinstance(expected_commit, str)
        and expected_commit
        and source_method is not None
    ):
        current_commit = await _resolve(source_method(_uuid(state, "repo_id")))
        if current_commit != expected_commit:
            raise MigrationSnapshotChanged("Repository source commit changed.")
    return current


def _normalise_graph(value: object) -> CodeGraph | Mapping[str, object]:
    if isinstance(value, CodeGraph):
        return value
    payload = _mapping(value)
    if payload is not None:
        nested = payload.get("graph", payload.get("context"))
        if nested is not None:
            return _normalise_graph(nested)
        if "nodes" in payload and "edges" in payload:
            return CodeGraph.model_validate(payload)
    if hasattr(value, "nodes") and hasattr(value, "edges"):
        return CodeGraph.model_validate(
            {
                "nodes": value.nodes,
                "edges": value.edges,
                "warnings": getattr(value, "warnings", []),
            }
        )
    raise MigrationWorkflowError("Graph retrieval did not return a code context.")


def _context_parts(
    context: CodeGraph | Mapping[str, object], state: MigrationState
) -> tuple[list[CodeNode], list[CodeEdge], list[str], dict[str, str]]:
    graph = _normalise_graph(context)
    if isinstance(graph, CodeGraph):
        nodes, edges, warnings = graph.nodes, graph.edges, graph.warnings
    else:
        raw_nodes = graph.get("nodes", [])
        raw_edges = graph.get("edges", [])
        nodes = [
            node if isinstance(node, CodeNode) else CodeNode.model_validate(node)
            for node in cast(Sequence[object], raw_nodes)
        ]
        edges = [
            edge if isinstance(edge, CodeEdge) else CodeEdge.model_validate(edge)
            for edge in cast(Sequence[object], raw_edges)
        ]
        warnings = [
            item
            for item in cast(Sequence[object], graph.get("warnings", []))
            if isinstance(item, str)
        ]
    selected = state.get("seam_node_ids")
    if selected:
        selected_names = set(selected)
        nodes = [node for node in nodes if node.qualified_name in selected_names]
        edges = [
            edge
            for edge in edges
            if edge.source in selected_names and edge.target in selected_names
        ]
    source_files: dict[str, str] = {}
    for node in nodes:
        if node.source_code:
            source_files.setdefault(node.file_path, node.source_code)
    return nodes, edges, warnings, source_files


async def _retrieve_seam_context(
    dependencies: MigrationDependencies, state: MigrationState
) -> object:
    graph = dependencies.resolved_graph()
    method = _method(
        graph,
        ("retrieve_seam_context", "get_seam_context"),
    )
    if method is not None:
        try:
            return await _resolve(
                method(_uuid(state, "repo_id"), _uuid(state, "seam_id"))
            )
        except TypeError as first_error:
            try:
                return await _resolve(
                    method(
                        repo_id=_uuid(state, "repo_id"),
                        seam_id=_uuid(state, "seam_id"),
                    )
                )
            except TypeError:
                raise first_error from None
    load_graph = _method(graph, ("load_graph", "graph"))
    if load_graph is None:
        existing = state.get("graph_context", state.get("context"))
        if existing is None:
            raise MigrationWorkflowError(
                "Injected graph service cannot retrieve context."
            )
        return existing
    return await _resolve(load_graph(_uuid(state, "repo_id")))


def _crag_result(
    value: object,
) -> tuple[CRAGDecision, list[str], list[str], dict[str, str]]:
    if isinstance(value, CRAGEvaluationResult):
        return (
            value.decision,
            value.missing_files,
            value.reasons,
            value.augmented_sources,
        )
    payload = _mapping(value)
    if payload is None:
        decision = getattr(value, "decision", getattr(value, "context_decision", None))
        missing = getattr(value, "missing_files", [])
        reasons = getattr(value, "reasons", [])
        augmented = getattr(value, "augmented_sources", {})
    else:
        decision = payload.get("decision", payload.get("context_decision"))
        missing = payload.get("missing_files", [])
        reasons = payload.get("reasons", [])
        augmented = payload.get(
            "augmented_sources", payload.get("full_file_augmentation", {})
        )
    if decision not in {"context_sufficient", "context_incomplete"}:
        raise MigrationWorkflowError("CRAG evaluator returned an invalid decision.")
    return (
        cast(CRAGDecision, decision),
        [item for item in cast(Sequence[object], missing) if isinstance(item, str)],
        [item for item in cast(Sequence[object], reasons) if isinstance(item, str)],
        {
            key: item
            for key, item in cast(Mapping[object, object], augmented).items()
            if isinstance(key, str) and isinstance(item, str)
        },
    )


def _crag_event_reasons(reasons: Sequence[str]) -> list[str]:
    """Keep diagnostic previews within the bounded migration event contract."""
    return [reason[:512] for reason in reasons[:16]]


def _nodes_edges(state: MigrationState) -> tuple[list[CodeNode], list[CodeEdge]]:
    nodes = state.get("seam_nodes", [])
    edges = state.get("seam_edges", [])
    return list(nodes), list(edges)


def _coder_request(
    dependencies: MigrationDependencies, state: MigrationState
) -> RefactorCoderRequest:
    nodes, edges = _nodes_edges(state)
    seam_name = _text(state.get("seam_name"), "migration")
    source_files = dict(state.get("source_files", {}))
    retry_count = state.get("retry_count", 0)
    if type(retry_count) is not int:
        raise MigrationWorkflowError("Migration retry count is invalid.")
    stderr = _redact_text(dependencies, _text(state.get("sandbox_stderr")))
    if retry_count > 0 and not stderr:
        stderr = "Sandbox failed without stderr output."
    return RefactorCoderRequest(
        seam_name=seam_name,
        seam_nodes=nodes,
        seam_edges=edges,
        source_files=source_files,
        warnings=list(state.get("warnings", [])),
        retry_count=retry_count,
        prior_sandbox_stderr=stderr,
    )


def _artifacts(value: object) -> dict[str, str]:
    raw: object
    if isinstance(value, Mapping):
        raw = value.get("artifacts", value)
    else:
        raw = getattr(value, "artifacts", getattr(value, "artifact_map", None))
    if not isinstance(raw, Mapping) or not raw:
        raise MigrationWorkflowError("Refactor coder returned no artifacts.")
    artifacts: dict[str, str] = {}
    for path, content in raw.items():
        if not isinstance(path, str) or not isinstance(content, str):
            raise MigrationWorkflowError("Refactor coder returned invalid artifacts.")
        artifacts[path] = content
    return artifacts


def _sandbox_result(value: object) -> tuple[bool, str, str, bool, str, str]:
    if isinstance(value, Mapping):
        passed = value.get("passed")
        stdout = value.get("stdout", "")
        stderr = value.get("stderr", "")
        timed_out = value.get("timed_out", False)
        isolation = value.get("isolation", "unknown")
        isolation_note = value.get("isolation_note", "")
    else:
        passed = getattr(value, "passed", None)
        stdout = getattr(value, "stdout", "")
        stderr = getattr(value, "stderr", "")
        timed_out = getattr(value, "timed_out", False)
        isolation = getattr(value, "isolation", "unknown")
        isolation_note = getattr(value, "isolation_note", "")
    if (
        type(passed) is not bool
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or type(timed_out) is not bool
        or not isinstance(isolation, str)
        or not isinstance(isolation_note, str)
    ):
        raise MigrationWorkflowError("Sandbox returned an invalid result.")
    return passed, stdout, stderr, timed_out, isolation, isolation_note


def _github_url(value: object) -> tuple[str, int | None, str | None]:
    if isinstance(value, str):
        return value, None, None
    payload = _mapping(value)
    if payload is not None:
        url = payload.get("pr_url", payload.get("url", payload.get("pull_request_url")))
        number = payload.get("pr_number", payload.get("number"))
        branch = payload.get("branch", payload.get("branch_name"))
    else:
        url = getattr(value, "pr_url", getattr(value, "pull_request_url", None))
        number = getattr(
            value, "pr_number", getattr(value, "pull_request_number", None)
        )
        branch = getattr(value, "branch", getattr(value, "branch_name", None))
    if not isinstance(url, str) or not url or len(url) > 512:
        raise MigrationWorkflowError(
            "GitHub service returned no valid pull request URL."
        )
    return (
        url,
        number if type(number) is int else None,
        branch if isinstance(branch, str) else None,
    )


def _failure_update(
    state: MigrationState, stage: str, error: BaseException
) -> MigrationState:
    code = getattr(error, "code", None)
    if not isinstance(code, str) or not code.isidentifier() or len(code) > 128:
        code = f"MIGRATION_{stage}_FAILED"
    return {
        "workflow_error": True,
        "error_code": code,
        "error_stage": stage,
        "error_type": (
            type(error).__name__
            if type(error).__name__.isidentifier()
            else "MigrationError"
        ),
    }


async def _retrieve_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        metadata = await _read_metadata(dependencies, state)
        context = await _retrieve_seam_context(dependencies, state)
        normalized = _normalise_graph(context)
        current = await _snapshot(
            dependencies,
            state,
            _text(metadata.get("last_commit_sha")) or None,
        )
        expected = _current_expected_snapshot(state) or current
        if expected is not None and current != expected:
            raise MigrationSnapshotChanged("Repository ingestion snapshot changed.")
        nodes, edges, warnings, source_files = _context_parts(normalized, state)
        scoped_context = CodeGraph(nodes=nodes, edges=edges, warnings=warnings)
        output = cast(
            MigrationState,
            {
                **metadata,
                "graph_context": scoped_context,
                "context": scoped_context,
                "seam_nodes": nodes,
                "seam_edges": edges,
                "warnings": warnings,
                "source_files": source_files,
                "retry_count": state.get("retry_count", 0),
                "status": "CRAG_EVAL",
                "workflow_error": False,
            },
        )
        if expected is not None:
            output["expected_snapshot_version"] = expected
        updates = {"status": "CRAG_EVAL"}
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            updates,
            MigrationEventType.AGENT_START,
            {"agent": "migration_context_retrieval"},
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        return _failure_update(state, "RETRIEVE_CONTEXT", error)


async def _crag_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        evaluator = dependencies.resolved_crag()
        result = await _call(
            evaluator,
            ("evaluate_context", "evaluate"),
            state["graph_context"],
        )
        decision, missing, reasons, augmented = _crag_result(result)
        source_files = dict(state.get("source_files", {}))
        source_files.update(augmented)
        output: MigrationState = {
            "crag_result": (
                result if isinstance(result, (CRAGEvaluationResult, Mapping)) else {}
            ),
            "crag_decision": decision,
            "crag_status": (
                "SUFFICIENT" if decision == "context_sufficient" else "PENDING"
            ),
            "crag_reasons": reasons,
            "missing_files": missing,
            "augmented_sources": augmented,
            "source_files": source_files,
            "status": "CRAG_EVAL",
            "workflow_error": decision == "context_incomplete" and not missing,
        }
        if output["workflow_error"]:
            output.update(
                {
                    "error_code": "CRAG_CONTEXT_INCOMPLETE",
                    "error_stage": "CRAG_EVALUATION",
                }
            )
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            {"status": "CRAG_EVAL", "crag_status": output["crag_status"]},
            MigrationEventType.CRAG_DECISION,
            {
                "decision": decision,
                "missing_file_count": len(missing),
                "reason_count": len(reasons),
                "reasons": _crag_event_reasons(reasons),
                "retry_count": state.get("retry_count", 0),
                "crag_retry_count": state.get("retry_count", 0),
            },
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        return _failure_update(state, "CRAG_EVALUATION", error)


async def _augment_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        source_files = dict(state.get("source_files", {}))
        augmented = dict(state.get("augmented_sources", {}))
        reader = dependencies.source_reader
        if reader is not None:
            for file_path in state.get("missing_files", []):
                value = await _call_source_reader(reader, file_path)
                if value is not None:
                    source_files[file_path] = value
                    augmented[file_path] = value
        missing = [
            file_path
            for file_path in state.get("missing_files", [])
            if file_path not in source_files
        ]
        if missing:
            raise MigrationWorkflowError(
                "Required source context could not be retrieved."
            )
        output: MigrationState = {
            "source_files": source_files,
            "augmented_sources": augmented,
            "crag_status": "AUGMENTED",
            "status": "CRAG_EVAL",
            "workflow_error": False,
        }
        await _persist(
            dependencies,
            {**state, **output},
            {"status": "CRAG_EVAL", "crag_status": "AUGMENTED"},
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        return _failure_update(state, "AST_AUGMENTATION", error)


async def _call_source_reader(reader: object, file_path: str) -> str | None:
    if isinstance(reader, Mapping):
        value = reader.get(file_path)
        return value if isinstance(value, str) else None
    method = _method(reader, ("read", "read_source"))
    if method is None:
        raise MigrationWorkflowError("Injected source reader is invalid.")
    value = await _resolve(method(file_path))
    if value is not None and not isinstance(value, str):
        raise MigrationWorkflowError("Source reader returned non-text content.")
    return value


async def _coder_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        await _verify_snapshot(dependencies, state)
        request = _coder_request(dependencies, state)
        result = await _call(
            dependencies.resolved_coder(),
            ("generate_refactor", "generate", "code"),
            request,
        )
        artifacts = _artifacts(result)
        output: MigrationState = {
            "generated_artifacts": artifacts,
            "artifacts": artifacts,
            "status": "CODING",
            "workflow_error": False,
        }
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            {"status": "CODING", "generated_artifacts": artifacts},
            MigrationEventType.ARTIFACTS_READY,
            {"artifact_count": len(artifacts), "attempt": state.get("retry_count", 0)},
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        return _failure_update(state, "REFACTOR_CODING", error)


async def _sandbox_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        await _verify_snapshot(dependencies, state)
        result = await _call(
            dependencies.resolved_sandbox(),
            ("run", "execute"),
            {
                **dict(state.get("sandbox_source_files", {})),
                **dict(state.get("generated_artifacts", state.get("artifacts", {}))),
            },
        )
        passed, stdout, stderr, timed_out, isolation, isolation_note = _sandbox_result(
            result
        )
        stdout = _redact_text(dependencies, stdout)
        stderr = _redact_text(dependencies, stderr)
        isolation_note = _redact_text(dependencies, isolation_note)
        retry_count = state.get("retry_count", 0)
        if not passed:
            retry_count = min(3, retry_count + 1)
        safe_stderr = stderr or (
            "Sandbox failed without stderr output." if not passed else ""
        )
        logs = (
            f"isolation: {isolation}\n"
            f"isolation_note: {isolation_note}\n"
            f"stdout:\n{stdout}\nstderr:\n{safe_stderr}"
        )
        output: MigrationState = {
            "sandbox_passed": passed,
            "sandbox_timed_out": timed_out,
            "sandbox_isolation": isolation,
            "sandbox_isolation_note": isolation_note,
            "sandbox_stdout": stdout,
            "sandbox_stderr": safe_stderr,
            "sandbox_logs": logs,
            "retry_count": retry_count,
            "status": "TESTING",
            "workflow_error": False,
        }
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            {
                "status": "TESTING",
                "retry_count": retry_count,
                "sandbox_logs": logs,
                "sandbox_passed": passed,
                "sandbox_isolation": isolation,
                "sandbox_isolation_note": isolation_note,
            },
            MigrationEventType.SANDBOX_ITERATION,
            {
                "attempt": retry_count,
                "passed": passed,
                "timed_out": timed_out,
                "isolation": isolation,
            },
        )
        if timed_out:
            output["error_code"] = "SANDBOX_TIMEOUT"
            output["error_stage"] = "SANDBOX_TESTING"
        if (
            passed
            and state.get("create_github_pr", False)
            and isolation == "subprocess"
        ):
            output.update(
                {
                    "workflow_error": True,
                    "error_code": "SANDBOX_ISOLATION_UNAVAILABLE",
                    "error_stage": "SANDBOX_TESTING",
                }
            )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        return _failure_update(state, "SANDBOX_TESTING", error)


def _github_payload(state: MigrationState) -> Mapping[str, object]:
    return {
        "job_id": _uuid(state, "job_id"),
        "repo_id": _uuid(state, "repo_id"),
        "repo_url": _text(state.get("repo_url")),
        "base_branch": _text(state.get("base_branch"), "main"),
        "seam_name": _text(state.get("seam_name"), "migration"),
        "artifacts": dict(state.get("generated_artifacts", state.get("artifacts", {}))),
        "dependency_diagram": _text(
            state.get("dependency_diagram"), "graph TD\n  Migration --> Sandbox"
        ),
        "blast_radius_summary": _text(
            state.get("blast_radius_summary"), "Verified by C.O.R.E. sandbox."
        ),
    }


async def _github_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        await _verify_snapshot(dependencies, state)
        service = dependencies.resolved_github()
        if service is None:
            raise MigrationWorkflowError("GitHub PR service is not configured.")
        request = (
            dependencies.github_request_factory(state)
            if dependencies.github_request_factory is not None
            else state.get("github_request", _github_payload(state))
        )
        result = await _call(service, ("create_pull_request", "publish"), request)
        url, number, branch = _github_url(result)
        output: MigrationState = {
            "pr_url": url,
            "status": "COMPLETED",
            "workflow_error": False,
        }
        if number is not None:
            output["pr_number"] = number
        if branch is not None:
            output["branch"] = branch
        updates: dict[str, object] = {"status": "COMPLETED", "pr_url": url}
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            updates,
            MigrationEventType.PR_CREATED,
            {
                key: value
                for key, value in {"pr_number": number, "branch": branch}.items()
                if value is not None
            },
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        return _failure_update(state, "GITHUB_PR", error)


async def _complete_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        await _verify_snapshot(dependencies, state)
        output: MigrationState = {"status": "COMPLETED", "workflow_error": False}
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            {"status": "COMPLETED"},
            MigrationEventType.COMPLETED,
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        return _failure_update(state, "COMPLETION", error)


async def _mark_failed_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        code = _text(state.get("error_code"), "MIGRATION_FAILED")
        output: MigrationState = {"status": "FAILED", "workflow_error": True}
        failure_payload: dict[str, object] = {
            "error_code": code,
            "stage": _text(state.get("error_stage"), "MIGRATION"),
            "error_type": _text(state.get("error_type"), "MigrationError"),
        }
        reasons = state.get("crag_reasons", [])
        if reasons:
            failure_payload["reasons"] = _crag_event_reasons(reasons)
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            {
                "status": "FAILED",
                "sandbox_logs": state.get("sandbox_logs"),
            },
            MigrationEventType.MIGRATION_FAILED,
            failure_payload,
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        raise MigrationWorkflowError("Could not persist migration failure.") from error


async def _human_review_node(
    dependencies: MigrationDependencies, state: MigrationState
) -> MigrationState:
    try:
        output: MigrationState = {
            "status": "FAILED",
            "human_review": True,
            "workflow_error": True,
        }
        await _persist_and_publish(
            dependencies,
            {**state, **output},
            {
                "status": "FAILED",
                "retry_count": state.get("retry_count", 3),
                "sandbox_logs": state.get("sandbox_logs"),
            },
            MigrationEventType.HUMAN_REVIEW,
            {
                "error_code": "MAX_SANDBOX_RETRIES",
                "retry_count": state.get("retry_count", 3),
                "status": "FAILED",
            },
        )
        return output
    except BaseException as error:
        if _is_control_flow_error(error):
            raise
        raise MigrationWorkflowError(
            "Could not persist human-review migration."
        ) from error


def _has_error(state: MigrationState) -> Literal["workflow_failed", "continue"]:
    return "workflow_failed" if state.get("workflow_error", False) else "continue"


def _crag_route(
    state: MigrationState,
) -> Literal["context_sufficient", "context_incomplete", "workflow_failed"]:
    if state.get("workflow_error", False):
        return "workflow_failed"
    return state.get("crag_decision", "context_incomplete")


def _sandbox_route(state: MigrationState) -> SandboxRoute:
    if state.get("workflow_error", False):
        return "workflow_failed"
    if state.get("sandbox_passed", False):
        return (
            "tests_passed"
            if state.get("create_github_pr", False)
            else "tests_passed_no_pr"
        )
    if state.get("retry_count", 0) < 3:
        return "tests_failed_retry"
    return "max_retries_exceeded"


def _build_graph(
    dependencies: MigrationDependencies,
) -> CompiledStateGraph[MigrationState, None, MigrationState, MigrationState]:
    if StateGraph is None:
        raise LangGraphUnavailable(
            "LangGraph is required to build the migration workflow."
        )
    # Resolve mandatory services during construction so a misconfigured worker
    # fails before accepting a migration job, while GitHub remains optional.
    dependencies.resolved_graph()
    dependencies.resolved_crag()
    dependencies.resolved_coder()
    dependencies.resolved_sandbox()

    async def retrieve_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "migration_context_retrieval",
            state,
            lambda: _retrieve_node(dependencies, state),
        )

    async def crag_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "crag_evaluator",
            state,
            lambda: _crag_node(dependencies, state),
        )

    async def augment_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "crag_source_augmentation",
            state,
            lambda: _augment_node(dependencies, state),
        )

    async def coder_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "refactor_coder",
            state,
            lambda: _coder_node(dependencies, state),
        )

    async def sandbox_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "pytest_sandbox",
            state,
            lambda: _sandbox_node(dependencies, state),
        )

    async def github_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "github_pr_publisher",
            state,
            lambda: _github_node(dependencies, state),
        )

    async def complete_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "migration_completion",
            state,
            lambda: _complete_node(dependencies, state),
        )

    async def human_review_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "migration_human_review",
            state,
            lambda: _human_review_node(dependencies, state),
        )

    async def failed_node(state: MigrationState) -> MigrationState:
        return await _observed_agent(
            "migration_failure",
            state,
            lambda: _mark_failed_node(dependencies, state),
        )

    graph = StateGraph(MigrationState)
    graph.add_node("retrieve_seam_context", retrieve_node)
    graph.add_node("crag_evaluate_context", crag_node)
    graph.add_node("augment_ast_source", augment_node)
    graph.add_node("generate_refactor", coder_node)
    graph.add_node("execute_sandbox_tests", sandbox_node)
    graph.add_node("create_github_pr", github_node)
    graph.add_node("complete", complete_node)
    graph.add_node("mark_for_human_review", human_review_node)
    graph.add_node("mark_failed", failed_node)

    graph.add_edge(START, "retrieve_seam_context")
    graph.add_conditional_edges(
        "retrieve_seam_context",
        _has_error,
        {"continue": "crag_evaluate_context", "workflow_failed": "mark_failed"},
    )
    graph.add_conditional_edges(
        "crag_evaluate_context",
        _crag_route,
        {
            "context_sufficient": "generate_refactor",
            "context_incomplete": "augment_ast_source",
            "workflow_failed": "mark_failed",
        },
    )
    graph.add_conditional_edges(
        "augment_ast_source",
        _has_error,
        {"continue": "generate_refactor", "workflow_failed": "mark_failed"},
    )
    graph.add_conditional_edges(
        "generate_refactor",
        _has_error,
        {"continue": "execute_sandbox_tests", "workflow_failed": "mark_failed"},
    )
    graph.add_conditional_edges(
        "execute_sandbox_tests",
        _sandbox_route,
        {
            "tests_passed": "create_github_pr",
            "tests_passed_no_pr": "complete",
            "tests_failed_retry": "generate_refactor",
            "max_retries_exceeded": "mark_for_human_review",
            "workflow_failed": "mark_failed",
        },
    )
    graph.add_conditional_edges(
        "create_github_pr",
        _has_error,
        {"continue": END, "workflow_failed": "mark_failed"},
    )
    graph.add_conditional_edges(
        "complete",
        _has_error,
        {"continue": END, "workflow_failed": "mark_failed"},
    )
    graph.add_edge("mark_for_human_review", END)
    graph.add_edge("mark_failed", END)
    return graph.compile()


def build_migration_graph(
    dependencies: MigrationDependencies,
) -> CompiledStateGraph[MigrationState, None, MigrationState, MigrationState]:
    """Compile the real LangGraph migration StateGraph."""
    return _build_graph(dependencies)


def create_migration_graph(
    dependencies: MigrationDependencies,
) -> CompiledStateGraph[MigrationState, None, MigrationState, MigrationState]:
    """Compatibility factory for callers that name graph creation explicitly."""
    return build_migration_graph(dependencies)


compile_migration_graph = build_migration_graph


__all__ = [
    "CompiledStateGraph",
    "CRAGDecision",
    "GraphService",
    "LangGraphUnavailable",
    "MigrationDependencies",
    "MigrationSnapshotChanged",
    "MigrationState",
    "MigrationWorkflowDependencies",
    "SandboxRoute",
    "build_migration_graph",
    "compile_migration_graph",
    "create_migration_graph",
]
