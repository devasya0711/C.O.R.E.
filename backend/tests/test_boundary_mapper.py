"""Real NetworkX/Neo4j coverage; GDS boundary doubles are explicitly labelled.

Only ``test_real_gds_louvain_when_installed`` exercises the external Louvain
procedure. Procedure-boundary fixtures verify lifecycle/error handling, not the
mathematical correctness or availability of an installed GDS plugin.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from itertools import combinations
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.agents.boundary_mapper import (
    _GDS_DROP,
    _GDS_LOUVAIN,
    _GDS_NODE_QUERY,
    _GDS_PROJECT,
    _GDS_RELATIONSHIP_QUERY,
    BoundaryMapperAgent,
    boundary_result,
    cluster_networkx,
    code_projection,
)
from app.agents.static_analyzer import StaticAnalyzerAgent
from app.core.errors import ApiError
from app.schemas.graph import CodeEdge, CodeGraph, CodeNode, NodeLabel
from app.services.neo4j_service import Neo4jService
from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError, ServiceUnavailable


def node(
    qualified_name: str, label: NodeLabel = "Function", *, name: str | None = None
) -> CodeNode:
    return CodeNode(
        label=label,
        qualified_name=qualified_name,
        name=qualified_name if name is None else name,
        file_path="fixture.py",
        start_line=1,
        end_line=1,
    )


def seam_graph() -> CodeGraph:
    """Two dense code seams with one bridge and a shared data hub."""
    names = [f"{seam}.{index}" for seam in ("billing", "orders") for index in range(4)]
    edges = [
        CodeEdge(source=source, target=target, relationship="CALLS", line=1)
        for seam in (names[:4], names[4:])
        for source, target in combinations(seam, 2)
    ]
    edges.append(CodeEdge(source=names[0], target=names[4], relationship="CALLS"))
    edges.extend(
        CodeEdge(source=name, target="db:ledger_entries", relationship="READS_FROM")
        for name in names
    )
    edges.append(
        CodeEdge(source=names[0], target="db:ledger_entries", relationship="WRITES_TO")
    )
    return CodeGraph(
        nodes=[
            *(node(name) for name in names),
            node("db:ledger_entries", "DBTable", name="ledger_entries"),
        ],
        edges=edges,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["_MAX_CODE_NODES", "_MAX_RELATIONSHIPS"])
async def test_analysis_rejects_oversized_graphs_without_partial_clusters(
    limit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(f"app.agents.boundary_mapper.{limit}", 0)
    monkeypatch.setattr(
        Neo4jService, "load_graph", AsyncMock(return_value=seam_graph())
    )
    with pytest.raises(ApiError) as error:
        await BoundaryMapperAgent(cast(AsyncDriver, object())).analyze(uuid4())
    assert error.value.code == "BOUNDARY_LIMIT_EXCEEDED"


def test_real_networkx_louvain_is_deterministic_and_canonical() -> None:
    graph = seam_graph()
    expected = cluster_networkx(graph)
    assert expected.method == "networkx"
    assert [community.node_ids for community in expected.communities] == [
        [f"billing.{index}" for index in range(4)],
        [f"orders.{index}" for index in range(4)],
    ]
    assert [community.community_id for community in expected.communities] == [0, 1]
    assert "db:ledger_entries" not in expected.assignments
    for community in expected.communities:
        assert community.cohesion_score == pytest.approx(6 / 7)
        assert community.coupling_score == pytest.approx(1 / 7)
        assert community.shared_db_tables == ["ledger_entries"]
    for _ in range(3):
        assert cluster_networkx(graph) == expected
    reversed_graph = CodeGraph(
        nodes=list(reversed(graph.nodes)), edges=list(reversed(graph.edges))
    )
    assert cluster_networkx(reversed_graph) == expected
    # Labels/iteration order supplied by an external algorithm do not leak into
    # stable community IDs; memberships, rather than raw Louvain IDs, define them.
    canonical = boundary_result(
        graph,
        [reversed(community.node_ids) for community in reversed(expected.communities)],
        method="gds",
    )
    assert canonical.communities == expected.communities


@pytest.mark.parametrize("access", ["read_read", "read_write", "write_write"])
def test_shared_tables_include_read_only_and_write_coupling(access: str) -> None:
    graph = seam_graph()
    graph.edges = [
        edge
        for edge in graph.edges
        if edge.relationship not in {"READS_FROM", "WRITES_TO"}
    ]
    graph.nodes.extend(
        [node("db.owned", "DBTable", name="owned"), node("db.unused", "DBTable")]
    )
    first, second = access.split("_")
    graph.edges.extend(
        [
            CodeEdge(
                source="billing.0",
                target="db:ledger_entries",
                relationship="WRITES_TO" if first == "write" else "READS_FROM",
            ),
            CodeEdge(
                source="orders.0",
                target="db:ledger_entries",
                relationship="WRITES_TO" if second == "write" else "READS_FROM",
            ),
            CodeEdge(source="billing.0", target="db.owned", relationship="WRITES_TO"),
            CodeEdge(source="billing.1", target="db.owned", relationship="READS_FROM"),
        ]
    )
    result = cluster_networkx(graph)
    expected = ["ledger_entries"]
    assert [community.shared_db_tables for community in result.communities] == [
        expected,
        expected,
    ]
    assert result.assignments["db.owned"] == result.assignments["billing.0"]
    assert "owned" not in result.assignments
    assert "db:ledger_entries" not in result.assignments
    assert "db.unused" not in result.assignments
    assert all(
        not name.startswith(("db.", "db:"))
        for community in result.communities
        for name in community.node_ids
    )


def test_conflict_metadata_uses_sorted_table_names_not_qualified_keys() -> None:
    graph = seam_graph()
    graph.nodes.append(node("db:z_audit", "DBTable", name="audit_log"))
    graph.edges.extend(
        [
            CodeEdge(
                source="billing.0", target="db:z_audit", relationship="READS_FROM"
            ),
            CodeEdge(source="orders.0", target="db:z_audit", relationship="WRITES_TO"),
        ]
    )
    result = cluster_networkx(graph)
    assert all(
        community.shared_db_tables == ["audit_log", "ledger_entries"]
        for community in result.communities
    )
    assert result.assignments == {
        f"{seam}.{index}": community_id
        for community_id, seam in enumerate(("billing", "orders"))
        for index in range(4)
    }
    assert [community.node_ids for community in result.communities] == [
        [f"billing.{index}" for index in range(4)],
        [f"orders.{index}" for index in range(4)],
    ]


def test_isolated_empty_and_table_only_graphs() -> None:
    for graph in (
        CodeGraph(nodes=[], edges=[]),
        CodeGraph(nodes=[node("db", "DBTable")], edges=[]),
    ):
        result = cluster_networkx(graph)
        assert result.communities == []
        assert result.assignments == {}
    graph = CodeGraph(
        nodes=[node("z"), node("a"), node("db", "DBTable")],
        edges=[
            CodeEdge(source="z", target="z", relationship="CALLS"),
            CodeEdge(source="a", target="db", relationship="READS_FROM"),
        ],
    )
    result = cluster_networkx(graph)
    assert result.assignments == {"a": 0, "db": 0, "z": 1}
    assert [community.node_ids for community in result.communities] == [["a"], ["z"]]
    assert all(community.cohesion_score == 1 for community in result.communities)
    assert all(community.coupling_score == 0 for community in result.communities)


def weighted_graph() -> CodeGraph:
    return CodeGraph(
        nodes=[
            node("z"),
            node("b"),
            node("a"),
            node("file", "File"),
            node("base", "Class"),
            node("child", "Class"),
            node("api", "APIEndpoint"),
            node("db", "DBTable"),
        ],
        edges=[
            CodeEdge(source="a", target="b", relationship="CALLS", line=1),
            CodeEdge(source="a", target="b", relationship="CALLS", line=2),
            CodeEdge(source="b", target="a", relationship="CALLS", line=3),
            CodeEdge(source="b", target="z", relationship="CALLS"),
            CodeEdge(source="a", target="a", relationship="CALLS"),
            CodeEdge(source="file", target="a", relationship="CONTAINS"),
            CodeEdge(source="child", target="base", relationship="INHERITS"),
            CodeEdge(source="api", target="a", relationship="ROUTES_TO"),
            CodeEdge(source="a", target="db", relationship="WRITES_TO"),
        ],
    )


def test_directed_multiplicity_all_code_relations_and_weighted_metrics() -> None:
    graph = weighted_graph()
    projection = code_projection(graph)
    assert list(projection.nodes) == ["a", "api", "b", "base", "child", "file", "z"]
    assert projection["a"]["b"]["weight"] == 3
    assert sorted(projection.edges(data="weight")) == [
        ("a", "api", 1),
        ("a", "b", 3),
        ("a", "file", 1),
        ("b", "z", 1),
        ("base", "child", 1),
    ]
    result = boundary_result(
        graph, [["z"], ["a", "b", "api", "file"], ["child", "base"]], method="gds"
    )
    first, second, third = result.communities
    assert first.cohesion_score == pytest.approx(5 / 6)
    assert first.coupling_score == pytest.approx(1 / 6)
    assert (second.cohesion_score, second.coupling_score) == (1, 0)
    assert (third.cohesion_score, third.coupling_score) == (0, 1)


@pytest.mark.parametrize(
    "partition",
    [[["a"]], [["a", "b"], ["a"]], [["a", "b"], []], [["a", "b", "foreign"]]],
)
def test_incomplete_or_overlapping_external_partitions_fail(
    partition: list[list[str]],
) -> None:
    graph = CodeGraph(nodes=[node("a"), node("b")], edges=[])
    with pytest.raises(ValueError, match="complete, disjoint"):
        boundary_result(graph, partition, method="gds")


def test_sample_monolith_uses_real_networkx_and_preserves_all_code_symbols() -> None:
    sample = Path(__file__).resolve().parents[2] / "sample_monolith"
    graph = StaticAnalyzerAgent().analyze(sample)
    result = cluster_networkx(graph)
    code_names = {
        node.qualified_name for node in graph.nodes if node.label != "DBTable"
    }
    assert len(result.communities) > 1
    assert {
        name for community in result.communities for name in community.node_ids
    } == code_names
    assert cluster_networkx(graph) == result


class GDSProcedureBoundary:
    """External procedure double; this does NOT execute real GDS Louvain."""

    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_query: str | None = None
        self.error: Exception | None = None
        self.stream_error: Exception | None = None
        self.rows: list[dict[str, object]] = [
            {"qualified_name": "orders.0", "community_id": 99},
            {"qualified_name": "billing.0", "community_id": 7},
        ]

    def session(self) -> GDSProcedureBoundary:
        return self

    async def __aenter__(self) -> GDSProcedureBoundary:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def run(self, query: str, **parameters: object) -> GDSProcedureBoundary:
        self.calls.append((query, parameters))
        if query == self.fail_query and self.error is not None:
            raise self.error
        return self

    async def consume(self) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[dict[str, object]]:
        for row in self.rows:
            yield row
        if self.stream_error is not None:
            raise self.stream_error


class ProcedureBoundaryError(Neo4jError):
    """Public exception contract without relying on driver hydration internals."""

    def __init__(self, code: str) -> None:
        super().__init__("explicit external procedure boundary")
        self.boundary_code = code

    @property
    def code(self) -> str:
        return self.boundary_code

    @code.setter
    def code(self, value: str) -> None:
        self.boundary_code = value


def neo4j_error(code: str) -> Neo4jError:
    return ProcedureBoundaryError(code)


@pytest.fixture
def gds_boundary(monkeypatch: pytest.MonkeyPatch) -> GDSProcedureBoundary:
    graph = CodeGraph(nodes=[node("orders.0"), node("billing.0")], edges=[])
    boundary = GDSProcedureBoundary(graph)
    monkeypatch.setattr(Neo4jService, "load_graph", AsyncMock(return_value=graph))
    return boundary


@pytest.mark.asyncio
async def test_gds_procedure_boundary_canonicalizes_and_cleans_up(
    gds_boundary: GDSProcedureBoundary,
) -> None:
    agent = BoundaryMapperAgent(cast(AsyncDriver, gds_boundary))
    repo = uuid4()
    result = await agent.analyze(repo)
    assert result.method == "gds"
    assert result.assignments == {"billing.0": 0, "orders.0": 1}
    assert [query for query, _ in gds_boundary.calls] == [
        _GDS_PROJECT,
        _GDS_LOUVAIN,
        _GDS_DROP,
    ]
    first_name = str(gds_boundary.calls[0][1]["graph_name"])
    assert UUID(first_name.removeprefix("boundary_")).version == 4
    assert gds_boundary.calls[0][1]["repo_id"] == str(repo)
    assert gds_boundary.calls[1][1]["repo_id"] == str(repo)
    assert gds_boundary.calls[0][1]["node_query"] == _GDS_NODE_QUERY
    assert gds_boundary.calls[0][1]["relationship_query"] == _GDS_RELATIONSHIP_QUERY
    assert gds_boundary.calls[-1][1]["graph_name"] == first_name
    await agent.analyze(repo)
    assert gds_boundary.calls[3][1]["graph_name"] != first_name


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [_GDS_PROJECT, _GDS_LOUVAIN])
async def test_missing_gds_procedure_boundary_falls_back(
    gds_boundary: GDSProcedureBoundary, query: str
) -> None:
    gds_boundary.fail_query = query
    gds_boundary.error = neo4j_error("Neo.ClientError.Procedure.ProcedureNotFound")
    result = await BoundaryMapperAgent(cast(AsyncDriver, gds_boundary)).analyze(uuid4())
    assert result == cluster_networkx(gds_boundary.graph)
    assert (gds_boundary.calls[-1][0] == _GDS_DROP) == (query == _GDS_LOUVAIN)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [_GDS_PROJECT, _GDS_LOUVAIN, _GDS_DROP])
@pytest.mark.parametrize(
    "error",
    [
        neo4j_error("Neo.ClientError.Security.Unauthorized"),
        neo4j_error("Neo.ClientError.Security.Forbidden"),
        neo4j_error("Neo.ClientError.Procedure.ProcedureCallFailed"),
        neo4j_error("Neo.ClientError.Statement.SyntaxError"),
        cast(Callable[[str], ServiceUnavailable], ServiceUnavailable)(
            "connection lost"
        ),
    ],
)
async def test_gds_procedure_boundary_does_not_mask_real_errors(
    gds_boundary: GDSProcedureBoundary, query: str, error: Exception
) -> None:
    gds_boundary.fail_query, gds_boundary.error = query, error
    with pytest.raises(type(error)) as caught:
        await BoundaryMapperAgent(cast(AsyncDriver, gds_boundary)).analyze(uuid4())
    assert caught.value is error
    if query != _GDS_PROJECT:
        assert gds_boundary.calls[-1][0] == _GDS_DROP


@pytest.mark.asyncio
async def test_gds_procedure_boundary_rejects_bad_partition_after_cleanup(
    gds_boundary: GDSProcedureBoundary,
) -> None:
    gds_boundary.rows = [{"qualified_name": "foreign.secret", "community_id": 4}]
    with pytest.raises(ValueError, match="complete, disjoint"):
        await BoundaryMapperAgent(cast(AsyncDriver, gds_boundary)).analyze(uuid4())
    assert gds_boundary.calls[-1][0] == _GDS_DROP


@pytest.mark.asyncio
async def test_gds_procedure_boundary_stream_failure_still_cleans_up(
    gds_boundary: GDSProcedureBoundary,
) -> None:
    error = neo4j_error("Neo.ClientError.Procedure.ProcedureCallFailed")
    gds_boundary.stream_error = error
    with pytest.raises(Neo4jError) as caught:
        await BoundaryMapperAgent(cast(AsyncDriver, gds_boundary)).analyze(uuid4())
    assert caught.value is error
    assert gds_boundary.calls[-1][0] == _GDS_DROP


@pytest.mark.asyncio
async def test_missing_drop_procedure_is_not_a_clustering_fallback(
    gds_boundary: GDSProcedureBoundary,
) -> None:
    gds_boundary.fail_query = _GDS_DROP
    gds_boundary.error = neo4j_error("Neo.ClientError.Procedure.ProcedureNotFound")
    with pytest.raises(Neo4jError) as caught:
        await BoundaryMapperAgent(cast(AsyncDriver, gds_boundary)).analyze(uuid4())
    assert caught.value is gds_boundary.error


async def gds_procedures(driver: AsyncDriver) -> set[str]:
    records, _, _ = await driver.execute_query(
        "SHOW PROCEDURES YIELD name WHERE name STARTS WITH $prefix RETURN name",
        prefix="gds.",
    )
    procedures = {str(record["name"]) for record in records}
    expected = os.environ.get("TEST_GDS_EXPECTED")
    if expected is not None:
        assert expected in {"true", "false"}
        if expected == "true":
            assert {
                "gds.graph.project.cypher",
                "gds.louvain.stream",
                "gds.graph.drop",
            } <= procedures
        else:
            assert not procedures
    return procedures


@pytest.mark.asyncio
async def test_real_neo4j_without_gds_falls_back_and_is_tenant_isolated(
    neo4j_driver: AsyncDriver,
) -> None:
    if "gds.graph.project.cypher" in await gds_procedures(neo4j_driver):
        pytest.skip("Absent-GDS integration requires a server without GDS projection")
    service = Neo4jService(neo4j_driver)
    first, second = uuid4(), uuid4()
    graph = seam_graph()
    private = CodeGraph(nodes=[node("private.secret"), node("billing.0")], edges=[])
    try:
        await service.upsert_graph(first, graph)
        await service.upsert_graph(second, private)
        # Even deliberately corrupted cross-tenant CALLS links cannot enter
        # source loading or either GDS projection query.
        await neo4j_driver.execute_query(
            "MATCH (a:CodeNode {repo_id: $repo_id, qualified_name: $source}) "
            "MATCH (b:CodeNode {repo_id: $other_repo_id, qualified_name: $target}) "
            "CREATE (a)-[:CALLS]->(b), (b)-[:CALLS]->(a)",
            repo_id=str(first),
            other_repo_id=str(second),
            source="billing.0",
            target="private.secret",
        )
        result = await BoundaryMapperAgent(neo4j_driver).analyze(first)
        assert result == cluster_networkx(graph)
        private_result = await BoundaryMapperAgent(neo4j_driver).analyze(second)
        assert private_result == cluster_networkx(private)
        # Execute the exact projection source queries on real Neo4j as well;
        # absent GDS must not leave their tenant filtering unverified.
        nodes, _, _ = await neo4j_driver.execute_query(
            _GDS_NODE_QUERY, repo_id=str(first)
        )
        code_ids = {record["id"] for record in nodes}
        edges, _, _ = await neo4j_driver.execute_query(
            _GDS_RELATIONSHIP_QUERY, repo_id=str(first)
        )
        assert len(code_ids) == 8
        assert len(edges) == 26  # 2 * (two six-edge cliques + one bridge)
        assert all(
            record["source"] in code_ids and record["target"] in code_ids
            for record in edges
        )
        for repo in (first, second):
            restored = await service.load_graph(repo)
            assert all(node.community_id is None for node in restored.nodes)
        empty_result = await BoundaryMapperAgent(neo4j_driver).analyze(uuid4())
        assert empty_result == cluster_networkx(CodeGraph(nodes=[], edges=[]))
    finally:
        for repo in (first, second):
            await neo4j_driver.execute_query(
                "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n",
                repo_id=str(repo),
            )


@pytest.mark.asyncio
async def test_real_neo4j_projection_queries_match_networkx_weights(
    neo4j_driver: AsyncDriver,
) -> None:
    repo = uuid4()
    graph = weighted_graph()
    try:
        await Neo4jService(neo4j_driver).upsert_graph(repo, graph)
        records, _, _ = await neo4j_driver.execute_query(
            _GDS_NODE_QUERY, repo_id=str(repo)
        )
        ids = [record["id"] for record in records]
        projection = code_projection(graph)
        mapping = dict(zip(ids, projection.nodes, strict=True))
        records, _, _ = await neo4j_driver.execute_query(
            _GDS_RELATIONSHIP_QUERY, repo_id=str(repo)
        )
        actual = [
            (mapping[record["source"]], mapping[record["target"]], record["weight"])
            for record in records
        ]
        expected = sorted(
            (source, target, weight)
            for source, target, weight in projection.edges(data="weight", default=0)
            for source, target in ((source, target), (target, source))
        )
        assert actual == expected
    finally:
        await neo4j_driver.execute_query(
            "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n", repo_id=str(repo)
        )


@pytest.mark.asyncio
async def test_real_gds_louvain_when_installed(
    neo4j_driver: AsyncDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit real-GDS evidence; skips rather than substituting a fixture."""
    required = {"gds.graph.project.cypher", "gds.louvain.stream", "gds.graph.drop"}
    if not required <= await gds_procedures(neo4j_driver):
        pytest.skip("Real GDS Louvain plugin is not installed on TEST_NEO4J_URI")
    repo = uuid4()
    projection_ids = [uuid4(), uuid4()]
    monkeypatch.setattr(
        "app.agents.boundary_mapper.uuid4", iter(projection_ids).__next__
    )
    try:
        await Neo4jService(neo4j_driver).upsert_graph(repo, seam_graph())
        agent = BoundaryMapperAgent(neo4j_driver)
        result = await agent.analyze(repo)
        assert result.method == "gds"
        assert [community.node_ids for community in result.communities] == [
            [f"billing.{index}" for index in range(4)],
            [f"orders.{index}" for index in range(4)],
        ]
        assert await agent.analyze(repo) == result
        records, _, _ = await neo4j_driver.execute_query(
            "CALL gds.graph.list() YIELD graphName "
            "WHERE graphName IN $names RETURN graphName",
            names=[f"boundary_{projection_id.hex}" for projection_id in projection_ids],
        )
        assert not records
    finally:
        await neo4j_driver.execute_query(
            "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n", repo_id=str(repo)
        )
