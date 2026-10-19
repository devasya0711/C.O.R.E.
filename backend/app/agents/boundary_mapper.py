"""Deterministic Louvain seams, with database ownership evaluated separately.

The caller verifies repository ownership and owns persistence/concurrency. GDS
uses an ephemeral, tenant-scoped Cypher projection; only missing GDS procedures
permit the real NetworkX fallback. Neither clustering path writes source nodes.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Literal
from uuid import UUID, uuid4

import networkx as nx
from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError

from app.core.errors import ApiError
from app.schemas.analysis import BoundaryResult, Community
from app.schemas.graph import CodeGraph
from app.services.neo4j_service import Neo4jService

_CODE_RELATIONSHIPS = frozenset({"CALLS", "INHERITS", "CONTAINS", "ROUTES_TO"})
_MAX_CODE_NODES = 10_000
_MAX_RELATIONSHIPS = 100_000
_GDS_NODE_QUERY = """
MATCH (n:CodeNode {repo_id: $repo_id})
WHERE NOT n:DBTable
RETURN id(n) AS id ORDER BY n.qualified_name
"""
# An undirected MATCH produces both orientations. Each orientation aggregates
# all parallel and reciprocal relations, giving GDS a symmetric weighted graph
# equivalent to the single undirected edge used by NetworkX and our metrics.
_GDS_RELATIONSHIP_QUERY = """
MATCH (a:CodeNode {repo_id: $repo_id})
      -[r:CALLS|INHERITS|CONTAINS|ROUTES_TO]-
      (b:CodeNode {repo_id: $repo_id})
WHERE NOT a:DBTable AND NOT b:DBTable AND a <> b
WITH a, b, count(r) AS weight
RETURN id(a) AS source, id(b) AS target, weight
ORDER BY a.qualified_name, b.qualified_name
"""
_GDS_PROJECT = """
CALL gds.graph.project.cypher(
    $graph_name, $node_query, $relationship_query,
    {parameters: {repo_id: $repo_id}, validateRelationships: true, readConcurrency: 1}
)
YIELD graphName RETURN graphName
"""
_GDS_LOUVAIN = """
CALL gds.louvain.stream($graph_name, {
    relationshipWeightProperty: 'weight', concurrency: 1
})
YIELD nodeId, communityId
MATCH (n:CodeNode {repo_id: $repo_id})
WHERE id(n) = nodeId AND NOT n:DBTable
RETURN n.qualified_name AS qualified_name, communityId AS community_id
ORDER BY qualified_name
"""
_GDS_DROP = "CALL gds.graph.drop($graph_name, false) YIELD graphName RETURN graphName"


class _GDSUnavailable(Exception):
    """A required GDS procedure is absent, rather than failing to execute."""


def code_projection(graph: CodeGraph) -> nx.Graph[str]:
    """Sorted undirected code graph; edge weight is relation multiplicity.

    DBTable access never affects modularity or cohesion, and recursive calls
    (self-loops) are ignored identically in the GDS projection and metrics.
    """
    names = sorted(
        node.qualified_name for node in graph.nodes if node.label != "DBTable"
    )
    code_names = set(names)
    weights: Counter[tuple[str, str]] = Counter()
    for edge in graph.edges:
        if (
            edge.relationship not in _CODE_RELATIONSHIPS
            or edge.source == edge.target
            or edge.source not in code_names
            or edge.target not in code_names
        ):
            continue
        source, target = sorted((edge.source, edge.target))
        weights[source, target] += 1
    projection: nx.Graph[str] = nx.Graph()
    projection.add_nodes_from(names)
    projection.add_weighted_edges_from(
        (source, target, weight) for (source, target), weight in sorted(weights.items())
    )
    return projection


def boundary_result(
    graph: CodeGraph,
    partition: Iterable[Iterable[str]],
    *,
    method: Literal["gds", "networkx"],
) -> BoundaryResult:
    """Canonicalize a complete code partition and calculate seam metadata.

    Internal weighted edges are counted once, boundary edges once for each
    incident community. Cohesion = internal / (internal + boundary); coupling
    is its complement. An isolated community has cohesion 1 and coupling 0.
    """
    projection = code_projection(graph)
    memberships = sorted(tuple(sorted(members)) for members in partition)
    flattened = [name for members in memberships for name in members]
    if (
        any(not members for members in memberships)
        or len(flattened) != len(set(flattened))
        or set(flattened) != set(projection.nodes)
    ):
        raise ValueError("Louvain must return a complete, disjoint code partition")
    assignments = {
        name: community_id
        for community_id, members in enumerate(memberships)
        for name in members
    }
    internal: Counter[int] = Counter()
    boundary: Counter[int] = Counter()
    for source, target, weight in projection.edges(data="weight", default=0):
        first, second = assignments[source], assignments[target]
        if first == second:
            internal[first] += weight
        else:
            boundary[first] += weight
            boundary[second] += weight

    table_names = {
        node.qualified_name: node.name
        for node in graph.nodes
        if node.label == "DBTable"
    }
    accessors: dict[str, set[int]] = defaultdict(set)
    for edge in graph.edges:
        if edge.relationship in {"READS_FROM", "WRITES_TO"}:
            accessors[edge.target].add(assignments[edge.source])
    conflicts: dict[int, set[str]] = defaultdict(set)
    for table, owners in sorted(accessors.items()):
        if len(owners) == 1:
            assignments[table] = next(iter(owners))
        else:
            # DATABASE.md includes all cross-community sharing in this field.
            # Preserve access edge types to distinguish read-only coupling
            # from a write conflict, rather than hiding read-only dependencies.
            for owner in sorted(owners):
                conflicts[owner].add(table_names[table])

    communities: list[Community] = []
    for community_id, members in enumerate(memberships):
        total = internal[community_id] + boundary[community_id]
        cohesion = internal[community_id] / total if total else 1.0
        communities.append(
            Community(
                community_id=community_id,
                node_ids=list(members),
                cohesion_score=cohesion,
                coupling_score=1.0 - cohesion,
                shared_db_tables=sorted(conflicts[community_id]),
            )
        )
    return BoundaryResult(
        communities=communities,
        assignments=dict(sorted(assignments.items())),
        method=method,
    )


def cluster_networkx(graph: CodeGraph) -> BoundaryResult:
    """Run real seeded NetworkX Louvain, handling edgeless graphs explicitly."""
    projection = code_projection(graph)
    partition = (
        nx.community.louvain_communities(projection, weight="weight", seed=42)
        if projection.number_of_edges()
        else [{name} for name in projection.nodes]
    )
    return boundary_result(graph, partition, method="networkx")


class BoundaryMapperAgent:
    """Analyze a validated repository snapshot without persisting assignments."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    async def analyze(self, repo_id: UUID) -> BoundaryResult:
        graph = await Neo4jService(self.driver).load_graph(repo_id)
        if (
            sum(node.label != "DBTable" for node in graph.nodes) > _MAX_CODE_NODES
            or len(graph.edges) > _MAX_RELATIONSHIPS
        ):
            raise ApiError(
                422,
                "BOUNDARY_LIMIT_EXCEEDED",
                "Repository exceeds bounded seam analysis limits.",
            )
        # There is no clustering operation for an empty code graph. Avoid GDS's
        # empty-projection error and return the exact vacuous local result.
        if not any(node.label != "DBTable" for node in graph.nodes):
            return cluster_networkx(graph)
        try:
            partition = await self._gds_partition(repo_id)
        except _GDSUnavailable:
            # Keep trusted, bounded CPU work off the API event loop.
            return await asyncio.to_thread(cluster_networkx, graph)
        return boundary_result(graph, partition, method="gds")

    async def _gds_partition(self, repo_id: UUID) -> list[list[str]]:
        graph_name = f"boundary_{uuid4().hex}"
        projected = False
        try:
            async with self.driver.session() as session:
                try:
                    result = await session.run(
                        _GDS_PROJECT,
                        graph_name=graph_name,
                        repo_id=str(repo_id),
                        node_query=_GDS_NODE_QUERY,
                        relationship_query=_GDS_RELATIONSHIP_QUERY,
                    )
                    # The procedure may have created its catalog graph before
                    # the result is fully consumed (or consumption fails).
                    projected = True
                    await result.consume()
                    result = await session.run(
                        _GDS_LOUVAIN, graph_name=graph_name, repo_id=str(repo_id)
                    )
                    communities: dict[int, list[str]] = defaultdict(list)
                    async for record in result:
                        name, community_id = (
                            record["qualified_name"],
                            record["community_id"],
                        )
                        if not isinstance(name, str) or type(community_id) is not int:
                            raise ValueError("GDS returned invalid Louvain assignments")
                        communities[community_id].append(name)
                    return list(communities.values())
                except Neo4jError as error:
                    if error.code == "Neo.ClientError.Procedure.ProcedureNotFound":
                        raise _GDSUnavailable from error
                    raise
        finally:
            if projected:
                # Catalog projections outlive transactions, so explicitly drop
                # in a fresh session even when streaming/validation fails.
                # Cleanup errors propagate; they must never trigger fallback.
                async with self.driver.session() as session:
                    result = await session.run(_GDS_DROP, graph_name=graph_name)
                    await result.consume()
