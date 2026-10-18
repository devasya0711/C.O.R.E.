"""Real Neo4j snapshot, namespace, semantic retrieval, and rollback tests."""

from pathlib import Path
from uuid import uuid4

import pytest
from app.agents.static_analyzer import StaticAnalyzerAgent
from app.schemas.graph import CodeEdge, CodeGraph, CodeNode
from app.services.neo4j_service import Neo4jService
from app.services.parse_source import analyze_source
from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError

SAMPLE = Path(__file__).resolve().parents[2] / "sample_monolith"


def small_graph(name: str = "payments.execute") -> CodeGraph:
    return CodeGraph(
        nodes=[
            CodeNode(
                label="Function",
                qualified_name=name,
                name="execute",
                file_path="payments.py",
                start_line=1,
                end_line=2,
                source_code="def execute():\n    return 1",
                embedding=[1.0] + [0.0] * 1535,
            )
        ],
        edges=[],
    )


@pytest.mark.asyncio
async def test_sample_snapshot_is_idempotent_and_removes_stale_symbols(
    neo4j_driver: AsyncDriver,
) -> None:
    service = Neo4jService(neo4j_driver)
    repo = uuid4()
    graph = StaticAnalyzerAgent().analyze(SAMPLE)
    try:
        await service.upsert_graph(repo, graph)
        await service.upsert_graph(repo, graph)
        restored = await service.load_graph(repo)
        assert len(restored.nodes) == 73
        assert len(restored.edges) == 132
        assert restored == graph.model_copy(update={"warnings": []})
        flow = await service.flow_graph(repo)
        ids = {node.id for node in flow.nodes}
        assert all(edge.source in ids and edge.target in ids for edge in flow.edges)
        assert all(node.id.startswith(f"{repo}:") for node in flow.nodes)
        await service.upsert_graph(repo, small_graph())
        restored = await service.load_graph(repo, embeddings=True)
        assert restored == small_graph()
    finally:
        await neo4j_driver.execute_query(
            "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n", repo_id=str(repo)
        )


@pytest.mark.asyncio
async def test_namespace_and_semantic_context_are_tenant_scoped(
    neo4j_driver: AsyncDriver,
) -> None:
    service = Neo4jService(neo4j_driver)
    first, second = uuid4(), uuid4()
    try:
        await service.upsert_graph(first, small_graph("same.name' RETURN 42"))
        await service.upsert_graph(second, small_graph("private.secret"))
        context = await service.retrieve_context(first, [1.0] + [0.0] * 1535, limit=1)
        assert [node.qualified_name for node in context.nodes] == [
            "same.name' RETURN 42"
        ]
        assert context.nodes[0].embedding is None
        await service.upsert_graph(first, CodeGraph(nodes=[], edges=[]))
        assert not (await service.load_graph(first)).nodes
        assert len((await service.load_graph(second)).nodes) == 1
    finally:
        for repo in (first, second):
            await neo4j_driver.execute_query(
                "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n",
                repo_id=str(repo),
            )


@pytest.mark.asyncio
async def test_invalid_write_rolls_back_previous_snapshot(
    neo4j_driver: AsyncDriver,
) -> None:
    repo = uuid4()
    service = Neo4jService(neo4j_driver)
    graph = small_graph()
    graph.nodes.append(
        graph.nodes[0].model_copy(update={"qualified_name": "orders.place"})
    )
    graph.edges.append(
        CodeEdge(
            source="orders.place",
            target="payments.execute",
            relationship="CALLS",
            line=2,
        )
    )
    try:
        await service.upsert_graph(repo, graph)
        bad = graph.model_copy(deep=True)
        # Deliberately corrupt an internal model to provoke a real Neo4j property
        # error after edge deletion, proving the entire snapshot write rolls back.
        object.__setattr__(bad.nodes[0], "source_code", {"not": "a Neo4j property"})
        with pytest.warns(UserWarning, match="serializer"), pytest.raises(Neo4jError):
            await service.upsert_graph(repo, bad)
        restored = await service.load_graph(repo, embeddings=True)
        assert restored == CodeGraph(
            nodes=sorted(graph.nodes, key=lambda node: node.qualified_name),
            edges=graph.edges,
        )
    finally:
        await neo4j_driver.execute_query(
            "MATCH (n:CodeNode {repo_id: $repo_id}) DETACH DELETE n", repo_id=str(repo)
        )


@pytest.mark.asyncio
async def test_out_of_process_parser_matches_in_process_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-must-not-reach-child")
    marker = tmp_path / "executed"
    (tmp_path / "malicious.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        "def inspect():\n    return 1\n"
    )
    graph = await analyze_source(tmp_path)
    assert len(graph.nodes) == 2
    assert not marker.exists()
    assert "test-secret-must-not-reach-child" not in graph.model_dump_json()
    assert (await analyze_source(SAMPLE)) == StaticAnalyzerAgent().analyze(SAMPLE)
