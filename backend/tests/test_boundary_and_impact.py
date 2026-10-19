"""Real-Neo4j impact recall, isolation, limits and a sparse 10k-node benchmark.

Run with TEST_NEO4J_URI set; no mocked graph traversal or global cleanup.
BoundaryMapper/API/cache coverage belongs to their respective owners.
"""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from app.agents import impact_predictor
from app.agents.impact_predictor import ImpactPredictorAgent
from app.agents.static_analyzer import StaticAnalyzerAgent
from app.core.errors import ApiError
from app.schemas.analysis import ImpactRequest, ImpactResponse
from app.schemas.graph import CodeEdge, CodeGraph, CodeNode, NodeLabel, Relationship
from app.services.neo4j_service import Neo4jService
from neo4j import AsyncDriver
from pydantic import BaseModel, Field, ValidationError

SAMPLE = Path(__file__).resolve().parents[2] / "sample_monolith"
PAYMENT = "payments.PaymentProcessor.execute"
CHECKOUT_ROUTE = "api:POST:routes.checkout_endpoint:/checkout"


class _Profile(BaseModel):
    """Minimal typed projection of Neo4j's real PROFILE execution evidence."""

    operator: str = Field(alias="operatorType")
    rows: int
    db_hits: int = Field(alias="dbHits")
    arguments: dict[str, object] = Field(alias="args")
    children: list["_Profile"] = Field(default_factory=list)


def assert_bounded_point_plan(profile: object, context: str) -> None:
    pending = [_Profile.model_validate(profile)]
    found_seek = False
    while pending:
        operator = pending.pop()
        diagnostic = (
            f"{context}: {operator.operator}, "
            f"rows={operator.rows}, db_hits={operator.db_hits}, "
            f"details={operator.arguments.get('Details')}, "
            f"children={[(c.operator, c.rows) for c in operator.children]}"
        )
        assert "Scan" not in operator.operator, diagnostic
        assert operator.rows <= 5, diagnostic
        if operator.operator.startswith("NodeIndexSeek"):
            found_seek = True
            assert operator.rows <= 1, diagnostic
        pending.extend(operator.children)
    assert found_seek, f"{context}: no indexed point seek"


def node(
    name: str, label: NodeLabel = "Function", *, file_path: str = "fixture.py"
) -> CodeNode:
    return CodeNode(
        label=label,
        qualified_name=name,
        name=name.rsplit(".", 1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=1,
    )


def edge(
    source: str,
    target: str,
    relationship: Relationship = "CALLS",
    *,
    line: int | None = None,
) -> CodeEdge:
    return CodeEdge(source=source, target=target, relationship=relationship, line=line)


async def ingest(
    driver: AsyncDriver, repo_id: UUID, nodes: list[CodeNode], edges: list[CodeEdge]
) -> None:
    await Neo4jService(driver).upsert_graph(
        repo_id, CodeGraph(nodes=nodes, edges=edges)
    )


async def analyze(
    driver: AsyncDriver,
    repo_id: UUID,
    target: str,
    hops: int = 3,
    summary: str = "Add a required parameter",
) -> ImpactResponse:
    return await ImpactPredictorAgent(driver).analyze(
        repo_id,
        ImpactRequest(
            target_qualified_name=target,
            max_hops=hops,
            proposed_change_summary=summary,
        ),
    )


def links(response: ImpactResponse) -> dict[tuple[str, str], int]:
    return {(e.source, e.target): e.hops for e in response.affected_edges}


@pytest_asyncio.fixture
async def impact_repo(neo4j_driver: AsyncDriver) -> AsyncIterator[UUID]:
    repo_id = uuid4()
    try:
        yield repo_id
    finally:
        # Include deliberately malformed non-CodeNode fixtures, scoped by UUID.
        await neo4j_driver.execute_query(
            "MATCH (n {repo_id: $repo_id}) DETACH DELETE n", repo_id=str(repo_id)
        )


@pytest_asyncio.fixture
async def sample_repo(neo4j_driver: AsyncDriver, impact_repo: UUID) -> UUID:
    await Neo4jService(neo4j_driver).upsert_graph(
        impact_repo, StaticAnalyzerAgent().analyze(SAMPLE)
    )
    return impact_repo


@pytest.mark.asyncio
async def test_sample_payment_recalls_transitive_checkout_and_ledger(
    neo4j_driver: AsyncDriver, sample_repo: UUID
) -> None:
    result = await analyze(neo4j_driver, sample_repo, PAYMENT)
    assert result.target == PAYMENT
    assert {
        "orders.place_order",
        "routes.checkout_endpoint",
        CHECKOUT_ROUTE,
        "models.record_payment",
        "models.InMemoryDatabase.save_payment",
        "db:ledger_entries",
    } <= set(result.affected_nodes)
    expected = {
        ("orders.place_order", PAYMENT): 1,
        ("routes.checkout_endpoint", "orders.place_order"): 2,
        (CHECKOUT_ROUTE, "routes.checkout_endpoint"): 2,
        (PAYMENT, "models.record_payment"): 1,
        ("models.record_payment", "models.InMemoryDatabase.save_payment"): 2,
        ("models.InMemoryDatabase.save_payment", "db:ledger_entries"): 2,
    }
    assert expected.items() <= links(result).items()
    assert result.affected_db_tables == ["ledger_entries", "orders", "users"]
    # Two actual callers in two files, one route, three tables, deepest caller=2.
    assert result.risk_score == 69
    assert result.impact_summary.startswith("High structural risk:")
    assert "5*2 upstream functions" in result.impact_summary
    assert "15*1 affected API endpoints" in result.impact_summary
    assert "10*3 affected DB tables" in result.impact_summary
    assert "5*2 upstream modules" in result.impact_summary
    assert "2*2 deepest caller hop" in result.impact_summary
    assert result.affected_nodes == sorted(set(result.affected_nodes))
    assert list(links(result)) == sorted(links(result))
    assert len(result.affected_edges) == len(links(result))
    visible = set(result.affected_nodes) | {PAYMENT}
    assert all(s in visible and t in visible for s, t in links(result))
    # The graph cannot determine what the free-text change actually breaks.
    assert result == await analyze(
        neo4j_driver, sample_repo, PAYMENT, summary="No signature change at all"
    )


@pytest.mark.asyncio
async def test_sample_depth_expands_monotonically_and_route_costs_no_call_hop(
    neo4j_driver: AsyncDriver, sample_repo: UUID
) -> None:
    results = [
        await analyze(neo4j_driver, sample_repo, PAYMENT, depth)
        for depth in range(1, 6)
    ]
    assert "orders.place_order" in results[0].affected_nodes
    assert CHECKOUT_ROUTE not in results[0].affected_nodes
    assert CHECKOUT_ROUTE in results[1].affected_nodes
    assert "models.InMemoryDatabase.save_payment" not in results[0].affected_nodes
    assert "models.InMemoryDatabase.save_payment" in results[1].affected_nodes
    assert (
        "models.InMemoryDatabase.save_payment",
        "db:ledger_entries",
    ) not in links(results[0])
    assert (
        links(results[1])[("models.InMemoryDatabase.save_payment", "db:ledger_entries")]
        == 2
    )
    for previous, current in zip(results, results[1:], strict=False):
        assert set(previous.affected_nodes) <= set(current.affected_nodes)
        assert set(previous.affected_db_tables) <= set(current.affected_db_tables)
        assert set(links(previous)) <= set(links(current))
        assert previous.risk_score <= current.risk_score
    for depth, result in enumerate(results, start=1):
        assert all(0 <= e.hops <= depth for e in result.affected_edges)


@pytest.mark.asyncio
async def test_all_five_depths_are_exact_with_boundary_route_attachments(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    nodes = [node("target")]
    edges: list[CodeEdge] = []
    for depth in range(1, 7):
        nodes.extend(
            [
                node(f"up{depth}", file_path=f"up{depth}.py"),
                node(f"down{depth}"),
                node(f"route{depth}", "APIEndpoint"),
            ]
        )
        edges.extend(
            [
                edge(f"up{depth}", f"up{depth - 1}" if depth > 1 else "target"),
                edge(f"down{depth - 1}" if depth > 1 else "target", f"down{depth}"),
                edge(f"route{depth}", f"up{depth}", "ROUTES_TO"),
            ]
        )
    nodes.extend([node("seed_route", "APIEndpoint"), node("down_route", "APIEndpoint")])
    edges.extend(
        [
            edge("seed_route", "target", "ROUTES_TO"),
            edge("down_route", "down1", "ROUTES_TO"),
        ]
    )
    await ingest(neo4j_driver, impact_repo, nodes, edges)
    previous_score = 0
    for depth in range(1, 6):
        result = await analyze(neo4j_driver, impact_repo, "target", depth)
        assert set(result.affected_nodes) == {"seed_route"} | {
            f"{kind}{index}"
            for index in range(1, depth + 1)
            for kind in ("up", "down", "route")
        }
        expected_links = {("seed_route", "target"): 1}
        for index in range(1, depth + 1):
            expected_links.update(
                {
                    (f"up{index}", f"up{index - 1}" if index > 1 else "target"): index,
                    (
                        f"down{index - 1}" if index > 1 else "target",
                        f"down{index}",
                    ): index,
                    (f"route{index}", f"up{index}"): index,
                }
            )
        assert links(result) == expected_links
        assert result.risk_score == min(100, 5 * depth + 15 * (depth + 1) + 7 * depth)
        assert previous_score <= result.risk_score
        previous_score = result.risk_score


@pytest.mark.asyncio
async def test_class_seeds_declared_methods_and_direct_inheritance_context(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    await ingest(
        neo4j_driver,
        impact_repo,
        [
            node("Base", "Class"),
            node("Base.execute"),
            node("Selected", "Class"),
            node("Selected.validate"),
            node("Child", "Class"),
            node("Child.execute"),
            node("caller"),
            node("base_caller"),
            node("child_caller"),
            node("route", "APIEndpoint"),
            node("Unrelated", "Class"),
            node("Unrelated.execute"),
        ],
        [
            edge("Selected", "Base", "INHERITS"),
            edge("Child", "Selected", "INHERITS"),
            edge("Base", "Base.execute", "CONTAINS"),
            edge("Selected", "Selected.validate", "CONTAINS"),
            edge("Child", "Child.execute", "CONTAINS"),
            edge("Unrelated", "Unrelated.execute", "CONTAINS"),
            edge("caller", "Selected.validate"),
            edge("base_caller", "Base.execute"),
            edge("child_caller", "Child.execute"),
            edge("route", "caller", "ROUTES_TO"),
        ],
    )
    result = await analyze(neo4j_driver, impact_repo, "Selected", 1)
    assert set(result.affected_nodes) == {
        "Base",
        "Base.execute",
        "Selected.validate",
        "Child",
        "Child.execute",
        "caller",
        "base_caller",
        "child_caller",
        "route",
    }
    assert links(result) == {
        ("Selected", "Base"): 0,
        ("Child", "Selected"): 0,
        ("Base", "Base.execute"): 0,
        ("Selected", "Selected.validate"): 0,
        ("Child", "Child.execute"): 0,
        ("caller", "Selected.validate"): 1,
        ("base_caller", "Base.execute"): 1,
        ("child_caller", "Child.execute"): 1,
        ("route", "caller"): 1,
    }
    assert result.risk_score == 37  # 3 callers, 1 endpoint, 1 module, depth 1.
    assert "without MRO resolution" in result.impact_summary
    for depth in range(2, 6):
        expanded = await analyze(neo4j_driver, impact_repo, "Selected", depth)
        assert expanded.risk_score == result.risk_score
        assert expanded.affected_nodes == result.affected_nodes
        assert expanded.affected_edges == result.affected_edges
    # Exercise actual inherited/contained-method expansion, in addition to the
    # benchmark's no-class/missing-target plans. Neighbor scans are forbidden.
    _, summary, _ = await neo4j_driver.execute_query(
        "PROFILE " + impact_predictor._CLASS_QUERY,
        repo_id=str(impact_repo),
        target="Selected",
        row_limit=10_001,
    )
    assert_bounded_point_plan(summary.profile, "class (Selected)")


@pytest.mark.asyncio
async def test_sample_class_selection_includes_payment_method_callers(
    neo4j_driver: AsyncDriver, sample_repo: UUID
) -> None:
    result = await analyze(neo4j_driver, sample_repo, "payments.PaymentProcessor", 2)
    assert PAYMENT in result.affected_nodes
    assert CHECKOUT_ROUTE in result.affected_nodes
    assert "orders.place_order" in result.affected_nodes
    assert "ledger_entries" in result.affected_db_tables
    assert links(result)[("payments.PaymentProcessor", PAYMENT)] == 0


@pytest.mark.asyncio
async def test_cycles_parallel_calls_and_alternate_paths_have_shortest_depths(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    await ingest(
        neo4j_driver,
        impact_repo,
        [
            node("target"),
            node("a", file_path="shared.py"),
            node("b", file_path="shared.py"),
            node("deep", file_path="other.py"),
            node("route", "APIEndpoint"),
        ],
        [
            edge("a", "target", line=1),
            edge("a", "target", line=2),
            edge("b", "a"),
            edge("a", "b"),
            edge("b", "target"),
            edge("deep", "b"),
            edge("target", "a"),
            edge("target", "target"),
            edge("route", "a", "ROUTES_TO"),
        ],
    )
    result = await analyze(neo4j_driver, impact_repo, "target", 5)
    assert result.affected_nodes == ["a", "b", "deep", "route"]
    assert links(result) == {
        ("a", "target"): 1,
        ("b", "a"): 1,
        ("a", "b"): 1,
        ("b", "target"): 1,
        ("deep", "b"): 2,
        ("target", "a"): 1,
        ("target", "target"): 1,
        ("route", "a"): 1,
    }
    assert len(result.affected_edges) == 8
    assert result.risk_score == 44
    for _ in range(3):
        assert await analyze(neo4j_driver, impact_repo, "target", 5) == result


@pytest.mark.asyncio
async def test_db_neighbors_are_deduplicated_and_downstream_is_target_bounded(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    await ingest(
        neo4j_driver,
        impact_repo,
        [node(name) for name in ("target", "caller", "down1", "down2", "down3", "side")]
        + [
            node(name, "DBTable")
            for name in (
                "seed_db",
                "caller_db",
                "first_db",
                "ledger_entries",
                "outside_db",
            )
        ],
        [
            edge("caller", "target"),
            edge("caller", "side"),
            edge("target", "down1"),
            edge("down1", "down2"),
            edge("down2", "down3"),
            edge("target", "seed_db", "READS_FROM"),
            edge("target", "seed_db", "WRITES_TO"),
            edge("caller", "caller_db", "READS_FROM"),
            edge("down1", "first_db", "WRITES_TO"),
            edge("down2", "ledger_entries", "WRITES_TO"),
            edge("down3", "outside_db", "READS_FROM"),
            edge("side", "outside_db", "WRITES_TO"),
        ],
    )
    result = await analyze(neo4j_driver, impact_repo, "target", 2)
    assert result.affected_db_tables == [
        "caller_db",
        "first_db",
        "ledger_entries",
        "seed_db",
    ]
    assert not {"side", "down3", "outside_db"} & set(result.affected_nodes)
    assert links(result)[("target", "seed_db")] == 0
    assert links(result)[("caller", "caller_db")] == 1
    assert links(result)[("down1", "first_db")] == 1
    assert links(result)[("down2", "ledger_entries")] == 2
    assert result.risk_score == 52  # Only caller counts, not down1/down2.


@pytest.mark.parametrize(
    ("caller_count", "endpoint_count", "table_count", "score", "level"),
    [
        (0, 0, 0, 0, "Low"),
        (1, 0, 0, 12, "Low"),
        (0, 2, 0, 30, "Medium"),
        (1, 1, 1, 37, "Medium"),
        (0, 2, 3, 60, "High"),
        (20, 1, 1, 100, "High"),
    ],
)
@pytest.mark.asyncio
async def test_transparent_risk_formula_thresholds_and_cap(
    neo4j_driver: AsyncDriver,
    impact_repo: UUID,
    caller_count: int,
    endpoint_count: int,
    table_count: int,
    score: int,
    level: str,
) -> None:
    callers = [f"caller{i}" for i in range(caller_count)]
    endpoints = [f"endpoint{i}" for i in range(endpoint_count)]
    tables = [f"table{i}" for i in range(table_count)]
    await ingest(
        neo4j_driver,
        impact_repo,
        [node("target")]
        + [node(name) for name in callers]
        + [node(name, "APIEndpoint") for name in endpoints]
        + [node(name, "DBTable") for name in tables],
        [edge(name, "target") for name in callers]
        + [edge(name, "target", "ROUTES_TO") for name in endpoints]
        + [edge("target", name, "READS_FROM") for name in tables],
    )
    result = await analyze(neo4j_driver, impact_repo, "target")
    assert result.risk_score == score
    assert result.impact_summary.startswith(f"{level} structural risk:")
    assert f"5*{caller_count} upstream functions" in result.impact_summary
    assert f"15*{endpoint_count} affected API endpoints" in result.impact_summary
    assert f"10*{table_count} affected DB tables" in result.impact_summary
    assert f"5*{int(bool(callers))} upstream modules" in result.impact_summary
    assert f"2*{int(bool(callers))} deepest caller hop" in result.impact_summary
    assert f"= {score}/100" in result.impact_summary
    if not callers and not endpoints and not tables:
        assert result.affected_nodes == []
        assert result.affected_edges == []


@pytest.mark.asyncio
async def test_empty_class_has_no_invented_dependencies(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    await ingest(neo4j_driver, impact_repo, [node("Empty", "Class")], [])
    result = await analyze(neo4j_driver, impact_repo, "Empty")
    assert result.risk_score == 0
    assert result.affected_nodes == []
    assert result.affected_edges == []
    assert result.affected_db_tables == []


@pytest.mark.parametrize("label", ["File", "DBTable", "APIEndpoint"])
@pytest.mark.asyncio
async def test_non_callable_target_is_a_safe_422(
    neo4j_driver: AsyncDriver, impact_repo: UUID, label: NodeLabel
) -> None:
    await ingest(neo4j_driver, impact_repo, [node("unsupported", label)], [])
    with pytest.raises(ApiError) as caught:
        await analyze(neo4j_driver, impact_repo, "unsupported")
    assert caught.value.status_code == 422
    assert caught.value.code == "IMPACT_UNSUPPORTED_TARGET"


@pytest.mark.asyncio
async def test_missing_and_injection_like_targets_are_bound_parameters(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    injected = "x'}) DETACH DELETE n // MATCH (n) RETURN n"
    await ingest(neo4j_driver, impact_repo, [node(injected), node("untouched")], [])
    result = await analyze(neo4j_driver, impact_repo, injected)
    assert result.target == injected
    assert result.risk_score == 0
    for missing in ("missing", injected + " -- secret-value"):
        with pytest.raises(ApiError) as caught:
            await analyze(neo4j_driver, impact_repo, missing)
        assert caught.value.status_code == 404
        assert caught.value.code == "RESOURCE_NOT_FOUND"
        assert missing not in caught.value.message
        assert caught.value.details == {}
    records, _, _ = await neo4j_driver.execute_query(
        "MATCH (n:CodeNode {repo_id: $repo_id}) RETURN count(n) AS count",
        repo_id=str(impact_repo),
    )
    assert records[0]["count"] == 2


@pytest.mark.parametrize("bad_depth", [0, 6, True, False, "3", 3.0, None])
def test_request_rejects_non_strict_or_out_of_range_hops(bad_depth: object) -> None:
    with pytest.raises(ValidationError):
        ImpactRequest.model_validate(
            {"target_qualified_name": "target", "max_hops": bad_depth}
        )


@pytest.mark.asyncio
async def test_duplicate_symbols_cross_tenant_edges_and_non_code_paths_are_isolated(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    foreign_repo = uuid4()
    try:
        await ingest(
            neo4j_driver,
            impact_repo,
            [
                node("shared.execute"),
                node("local.caller"),
                node("local.reentry"),
                node("local.downstream_reentry"),
                node("local.noncode_reentry"),
                node("local.Class", "Class"),
                node("local.Class.execute"),
            ],
            [
                edge("local.caller", "shared.execute"),
                edge("local.Class", "local.Class.execute", "CONTAINS"),
            ],
        )
        await ingest(
            neo4j_driver,
            foreign_repo,
            [
                node("shared.execute"),
                node("private.caller"),
                node("private.bridge"),
                node("private.route", "APIEndpoint"),
                node("private.table", "DBTable"),
                node("private.Class", "Class"),
                node("private.Class.execute"),
            ],
            [
                edge("private.caller", "shared.execute"),
                edge("private.Class", "private.Class.execute", "CONTAINS"),
            ],
        )
        # Simulate a corrupt/malicious writer bypassing Neo4jService. In
        # particular, both ends of a two-hop CALLS path can be local while its
        # intermediate node belongs to another repo or lacks CodeNode.
        statements = (
            "MATCH (t:CodeNode {repo_id: $local, qualified_name: 'shared.execute'}), "
            "(f:CodeNode {repo_id: $foreign, qualified_name: 'private.bridge'}), "
            "(r:CodeNode {repo_id: $local, qualified_name: 'local.reentry'}), "
            "(d:CodeNode {repo_id: $local, "
            "qualified_name: 'local.downstream_reentry'}) "
            "CREATE (r)-[:CALLS]->(f), (f)-[:CALLS]->(t), "
            "(t)-[:CALLS]->(f), (f)-[:CALLS]->(d)",
            "MATCH (t:CodeNode {repo_id: $local, qualified_name: 'shared.execute'}), "
            "(r:CodeNode {repo_id: $foreign, qualified_name: 'private.route'}), "
            "(d:CodeNode {repo_id: $foreign, qualified_name: 'private.table'}) "
            "CREATE (r)-[:ROUTES_TO]->(t), (t)-[:WRITES_TO]->(d)",
            "MATCH (t:CodeNode {repo_id: $local, qualified_name: 'shared.execute'}), "
            "(c:CodeNode {repo_id: $local, qualified_name: 'local.noncode_reentry'}) "
            "CREATE (r:Function {repo_id: $local, qualified_name: 'rogue'}) "
            "CREATE (c)-[:CALLS]->(r), (r)-[:CALLS]->(t)",
            "MATCH (c:CodeNode {repo_id: $local, qualified_name: 'local.Class'}), "
            "(f:CodeNode {repo_id: $foreign, qualified_name: 'private.Class'}), "
            "(m:CodeNode {repo_id: $foreign, qualified_name: 'private.Class.execute'}) "
            "CREATE (c)-[:INHERITS]->(f), (c)-[:CONTAINS]->(m)",
        )
        for statement in statements:
            await neo4j_driver.execute_query(
                statement, local=str(impact_repo), foreign=str(foreign_repo)
            )
        result = await analyze(neo4j_driver, impact_repo, "shared.execute", 5)
        assert result.affected_nodes == ["local.caller"]
        assert links(result) == {("local.caller", "shared.execute"): 1}
        assert result.affected_db_tables == []
        assert result.risk_score == 12
        class_result = await analyze(neo4j_driver, impact_repo, "local.Class", 5)
        assert class_result.affected_nodes == ["local.Class.execute"]
        assert class_result.risk_score == 0
        foreign = await analyze(neo4j_driver, foreign_repo, "shared.execute", 5)
        assert foreign.affected_nodes == ["private.caller"]
        for missing in ("private.caller", "rogue"):
            with pytest.raises(ApiError) as caught:
                await analyze(neo4j_driver, impact_repo, missing)
            assert caught.value.status_code == 404
    finally:
        await neo4j_driver.execute_query(
            "MATCH (n {repo_id: $repo_id}) DETACH DELETE n", repo_id=str(foreign_repo)
        )


@pytest.mark.parametrize("limit", ["_MAX_QUERY_ROWS", "_MAX_NODES", "_MAX_EDGES"])
@pytest.mark.asyncio
async def test_oversized_radius_fails_explicitly_instead_of_silent_truncation(
    neo4j_driver: AsyncDriver,
    impact_repo: UUID,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    await ingest(
        neo4j_driver,
        impact_repo,
        [node("target")] + [node(f"caller{i}") for i in range(8)],
        [edge(f"caller{i}", "target") for i in range(8)],
    )
    monkeypatch.setattr(impact_predictor, limit, 3)
    with pytest.raises(ApiError) as caught:
        await analyze(neo4j_driver, impact_repo, "target", 5)
    assert caught.value.status_code == 422
    assert caught.value.code == "IMPACT_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_dense_cycles_exceed_the_production_path_limit_explicitly(
    neo4j_driver: AsyncDriver, impact_repo: UUID
) -> None:
    names = [f"f{i}" for i in range(7)]
    await ingest(
        neo4j_driver,
        impact_repo,
        [node(name) for name in names],
        [edge(source, target) for source in names for target in names],
    )
    # Only seven unique nodes: the guard must also bound path multiplicity,
    # rather than relying solely on a final deduplicated node/edge limit.
    shallow = await analyze(neo4j_driver, impact_repo, "f0", 1)
    assert shallow.risk_score == 37
    with pytest.raises(ApiError) as caught:
        await analyze(neo4j_driver, impact_repo, "f0", 5)
    assert caught.value.status_code == 422
    assert caught.value.code == "IMPACT_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_client_deadline_returns_a_safe_error(
    neo4j_driver: AsyncDriver, impact_repo: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ingest(neo4j_driver, impact_repo, [node("target")], [])
    monkeypatch.setattr(impact_predictor, "_ANALYSIS_TIMEOUT_SECONDS", 0.0)
    with pytest.raises(ApiError) as caught:
        await analyze(neo4j_driver, impact_repo, "target")
    assert caught.value.status_code == 503
    assert caught.value.code == "IMPACT_TIMEOUT"
    assert caught.value.details == {}


@pytest.mark.asyncio
async def test_10000_node_chain_benchmark(
    neo4j_driver: AsyncDriver,
    impact_repo: UUID,
    capsys: pytest.CaptureFixture[str],
    record_property: Callable[[str, object], None],
) -> None:
    """Measure five-hop analysis, not ingestion; no universal latency assertion.

    The sparse chain has five reachable callers and five reachable callees.
    Dense fanout/path explosion, concurrency and deployed-network latency are
    intentionally not represented by this microbenchmark.
    """
    await neo4j_driver.execute_query(
        "UNWIND range(0, 9999) AS i "
        "CREATE (:CodeNode:Function {id: $repo_id + ':chain.f' + toString(i), "
        "repo_id: $repo_id, qualified_name: 'chain.f' + toString(i), "
        "name: 'f' + toString(i), file_path: 'chain.py'})",
        repo_id=str(impact_repo),
    )
    await neo4j_driver.execute_query(
        "UNWIND range(0, 9998) AS i "
        "MATCH (a:CodeNode {id: $repo_id + ':chain.f' + toString(i)}) "
        "MATCH (b:CodeNode {id: $repo_id + ':chain.f' + toString(i + 1)}) "
        "CREATE (a)-[:CALLS {repo_id: $repo_id}]->(b)",
        repo_id=str(impact_repo),
    )
    started = perf_counter()
    result = await analyze(neo4j_driver, impact_repo, "chain.f5000", 5)
    first_ms = (perf_counter() - started) * 1000
    assert result.affected_nodes == sorted(
        f"chain.f{i}" for i in range(4995, 5006) if i != 5000
    )
    assert len(result.affected_edges) == 10
    assert result.risk_score == 40  # 5*5 callers + 5*1 file + 2*5 hops.
    timings: list[float] = []
    for _ in range(5):
        started = perf_counter()
        repeated = await analyze(neo4j_driver, impact_repo, "chain.f5000", 5)
        timings.append((perf_counter() - started) * 1000)
        assert repeated == result
    # Equality filters alone previously let Neo4j enumerate the entire repo
    # from the caller side. Check actual plans, not just the number returned.
    # Warmed integration statistics also let plain USING INDEX choose a full
    # composite-index scan for route/DB UNWIND lookups, followed by equality
    # filters. Check every point lookup, for present and absent symbols.
    for query_name, query in (
        ("target", impact_predictor._TARGET_QUERY),
        ("class", impact_predictor._CLASS_QUERY),
        ("upstream", impact_predictor._UPSTREAM_QUERIES[5]),
        ("downstream", impact_predictor._DOWNSTREAM_QUERIES[5]),
        ("routes", impact_predictor._ROUTES_QUERY),
        ("db", impact_predictor._DB_QUERY),
    ):
        for target in ("chain.f5000", "chain.missing"):
            _, summary, _ = await neo4j_driver.execute_query(
                "PROFILE " + query,
                repo_id=str(impact_repo),
                target=target,
                names=[target],
                row_limit=10_001,
            )
            assert_bounded_point_plan(summary.profile, f"{query_name} ({target})")
    record_property("chain_nodes", 10_000)
    record_property("first_analysis_ms", first_ms)
    record_property("warm_analysis_median_ms", median(timings))
    with capsys.disabled():
        print(
            "\n10,000-node sparse chain, depth=5, full analysis round-trip: "
            f"first={first_ms:.2f}ms; warm n=5 "
            f"min/median/max={min(timings):.2f}/{median(timings):.2f}/"
            f"{max(timings):.2f}ms (not a general SLA)"
        )
