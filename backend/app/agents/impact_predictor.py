"""Bounded, tenant-scoped structural impact analysis (no semantic prediction).

CALLS alone consumes the requested 1..5 hop budget. Class selection seeds its
declared methods and those of directly related base/subclasses, conservatively;
it does not attempt Python MRO or arbitrary-depth inheritance resolution.
CONTAINS/INHERITS edges have depth zero. Upstream CALLS edges use their source's
shortest distance to any seed; downstream-only CALLS edges use the destination's
shortest distance from a seed. ROUTES_TO uses max(1, handler distance), while DB
attachments use the function's distance without consuming another call hop.

Risk = min(100, 5*upstream functions + 15*endpoints + 10*DB tables
           + 5*distinct upstream file_paths + 2*deepest caller hop).
Seeds/downstream-only functions do not count as callers. No callers means zero
caller modules/depth, so an isolated leaf scores zero. Increasing CALLS depth
only adds dependencies and cannot lower this heuristic. Free-text change
descriptions are deliberately not interpreted.

Every query is anchored to a symbol, never a full-repository graph load. Limits
include a sentinel row: overflow fails explicitly rather than returning a
partial radius or score. Server transaction and client wall-clock deadlines
also bound high-fanout/cyclic path enumeration. Repository ownership is checked
by the caller (the API layer).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from neo4j import READ_ACCESS, AsyncDriver, AsyncTransaction, Record
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, ConfigDict

from app.core.errors import ApiError
from app.schemas.analysis import ImpactEdge, ImpactRequest, ImpactResponse

_TRANSACTION_TIMEOUT_SECONDS = 3.0
_ANALYSIS_TIMEOUT_SECONDS = 5.0
_MAX_QUERY_ROWS = 10_000
_MAX_NODES = 5_000
_MAX_EDGES = 10_000

_TARGET_QUERY = """
MATCH (n:CodeNode {repo_id: $repo_id, qualified_name: $target})
USING INDEX SEEK n:CodeNode(repo_id, qualified_name)
RETURN n{.qualified_name, .name, .file_path, labels: labels(n)} AS node
LIMIT 2
"""

# Keep class-neighbor predicates inside bounded paths as well. A plain OPTIONAL
# MATCH with an indexed method.repo_id can otherwise start with a repo-wide
# method scan instead of expanding from the already-bound owner.
_CLASS_QUERY = """
MATCH (c:CodeNode:Class {repo_id: $repo_id, qualified_name: $target})
USING INDEX SEEK c:CodeNode(repo_id, qualified_name)
CALL {
    WITH c
    RETURN c AS owner, null AS inherit_source, null AS inherit_target
    UNION ALL
    WITH c
    MATCH path=(c)-[:INHERITS*1..1]->(owner)
    WHERE all(n IN nodes(path)
              WHERE n:CodeNode AND n:Class AND n.repo_id = $repo_id)
    RETURN owner, c.qualified_name AS inherit_source,
           owner.qualified_name AS inherit_target
    UNION ALL
    WITH c
    MATCH path=(owner)-[:INHERITS*1..1]->(c)
    WHERE all(n IN nodes(path)
              WHERE n:CodeNode AND n:Class AND n.repo_id = $repo_id)
    RETURN owner, owner.qualified_name AS inherit_source,
           c.qualified_name AS inherit_target
}
OPTIONAL MATCH path=(owner)-[:CONTAINS*1..1]->(method)
WHERE all(n IN nodes(path) WHERE n:CodeNode AND n.repo_id = $repo_id)
      AND all(n IN tail(nodes(path)) WHERE n:Function)
RETURN owner{.qualified_name, .name, .file_path} AS owner,
       method{.qualified_name, .name, .file_path} AS method,
       inherit_source, inherit_target
LIMIT $row_limit
"""

# Cypher cannot parameterize a variable-length upper bound. These five literal,
# developer-owned patterns are the ONLY text used to construct traversal queries.
# Neither the target, change description, repo ID nor request depth is formatted
# into Cypher; depth only selects an already constructed query from this map.
_CALL_PATTERNS = {
    1: "[:CALLS*1..1]",
    2: "[:CALLS*1..2]",
    3: "[:CALLS*1..3]",
    4: "[:CALLS*1..4]",
    5: "[:CALLS*1..5]",
}
# Require point seeks on the existing startup composite index. USING INDEX
# alone also permits an index-wide existence scan followed by equality filters;
# warmed statistics/plans can choose that scan for UNWIND lookups. SEEK forbids
# that fallback (coercing name with toString alone does not reliably prevent it).
_SEED_QUERY = """
UNWIND $names AS name
MATCH (seed:CodeNode:Function {repo_id: $repo_id, qualified_name: name})
USING INDEX SEEK seed:CodeNode(repo_id, qualified_name)
"""
_PATH_RESULT = """
WHERE all(n IN nodes(path)
          WHERE n:CodeNode AND n:Function AND n.repo_id = $repo_id)
RETURN [n IN nodes(path) | n{.qualified_name, .name, .file_path}] AS nodes
LIMIT $row_limit
"""
_UPSTREAM_QUERIES = {
    depth: _SEED_QUERY
    + "MATCH path=(caller:CodeNode:Function)-"
    + pattern
    + "->(seed)\n"
    + _PATH_RESULT
    for depth, pattern in _CALL_PATTERNS.items()
}
_DOWNSTREAM_QUERIES = {
    depth: _SEED_QUERY
    + "MATCH path=(seed)-"
    + pattern
    + "->(callee:CodeNode:Function)\n"
    + _PATH_RESULT
    for depth, pattern in _CALL_PATTERNS.items()
}

_ROUTES_QUERY = """
UNWIND $names AS name
MATCH (handler:CodeNode:Function {repo_id: $repo_id, qualified_name: name})
USING INDEX SEEK handler:CodeNode(repo_id, qualified_name)
MATCH (endpoint:CodeNode:APIEndpoint {repo_id: $repo_id})-[:ROUTES_TO]->(handler)
RETURN handler.qualified_name AS handler,
       endpoint{.qualified_name, .name, .file_path} AS endpoint
LIMIT $row_limit
"""
_DB_QUERY = """
UNWIND $names AS name
MATCH (f:CodeNode:Function {repo_id: $repo_id, qualified_name: name})
USING INDEX SEEK f:CodeNode(repo_id, qualified_name)
MATCH (f)-[:READS_FROM|WRITES_TO]->(table:CodeNode:DBTable {repo_id: $repo_id})
RETURN f.qualified_name AS source,
       table{.qualified_name, .name, .file_path} AS table
LIMIT $row_limit
"""


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    qualified_name: str
    name: str
    file_path: str


class _Target(_Node):
    labels: list[str]


class _Path(BaseModel):
    nodes: list[_Node]


def _too_large() -> ApiError:
    return ApiError(
        422,
        "IMPACT_LIMIT_EXCEEDED",
        "Impact analysis exceeds the bounded result limit. Select a narrower target.",
    )


@dataclass
class _Radius:
    target: str
    nodes: dict[str, _Node] = field(default_factory=dict)
    seeds: set[str] = field(default_factory=set)
    upstream: dict[str, int] = field(default_factory=dict)
    downstream: dict[str, int] = field(default_factory=dict)
    edges: dict[tuple[str, str], int] = field(default_factory=dict)
    endpoints: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)

    def add_node(self, node: _Node) -> None:
        self.nodes[node.qualified_name] = node
        if len(self.nodes) > _MAX_NODES:
            raise _too_large()

    def add_edge(self, source: str, target: str, hops: int) -> None:
        key = (source, target)
        self.edges[key] = min(self.edges.get(key, hops), hops)
        if len(self.edges) > _MAX_EDGES:
            raise _too_large()

    def add_paths(self, rows: list[Record], *, upstream: bool) -> None:
        distances = self.upstream if upstream else self.downstream
        links: set[tuple[str, str]] = set()
        for row in rows:
            path = _Path.model_validate(dict(row)).nodes
            for index, node in enumerate(path):
                self.add_node(node)
                depth = len(path) - index - 1 if upstream else index
                name = node.qualified_name
                distances[name] = min(distances.get(name, depth), depth)
            links.update(
                (source.qualified_name, target.qualified_name)
                for source, target in zip(path, path[1:], strict=False)
            )
        for source, target in links:
            if upstream:
                self.add_edge(source, target, max(1, distances[source]))
            elif (source, target) not in self.edges:
                # A shared edge retains its shortest upstream caller depth.
                self.add_edge(source, target, max(1, distances[target]))

    def response(self, max_hops: int, *, class_target: bool) -> ImpactResponse:
        callers = set(self.upstream) - self.seeds
        modules = {self.nodes[name].file_path for name in callers}
        deepest = max((self.upstream[name] for name in callers), default=0)
        raw = (
            5 * len(callers)
            + 15 * len(self.endpoints)
            + 10 * len(self.tables)
            + 5 * len(modules)
            + 2 * deepest
        )
        score = min(100, raw)
        level = "Low" if score < 30 else "Medium" if score < 60 else "High"
        summary = (
            f"{level} structural risk: min(100, 5*{len(callers)} upstream functions "
            f"+ 15*{len(self.endpoints)} affected API endpoints "
            f"+ 10*{len(self.tables)} affected DB tables "
            f"+ 5*{len(modules)} upstream modules (distinct file_path) "
            f"+ 2*{deepest} deepest caller hop) = {score}/100 "
            f"(uncapped {raw}). CALLS depth <= {max_hops}; routes do not consume "
            "a call hop. DB dependencies include direct neighbors of seeds, "
            "callers and bounded downstream callees. Conservative structural "
            "reachability only; the proposed change text is not interpreted "
            "and no semantic change prediction is made."
        )
        if class_target:
            summary += (
                " Class seeds include declared methods and methods of directly "
                "related base/subclasses, without MRO resolution; "
                "CONTAINS/INHERITS edges have hop 0."
            )
        return ImpactResponse(
            target=self.target,
            risk_score=score,
            affected_nodes=sorted(set(self.nodes) - {self.target}),
            affected_edges=[
                ImpactEdge(source=source, target=target, hops=hops)
                for (source, target), hops in sorted(self.edges.items())
            ],
            affected_db_tables=sorted(self.tables),
            impact_summary=summary,
        )


class ImpactPredictorAgent:
    """Analyze one owned repository using real, bounded Cypher traversals."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    async def analyze(self, repo_id: UUID, request: ImpactRequest) -> ImpactResponse:
        """Return a complete bounded radius, or a safe error (never partial data).

        The selected target is identified by ``target`` and excluded from
        ``affected_nodes``. All other returned edge endpoints are included.
        Parallel relationship kinds/call sites collapse to one directed edge
        because ImpactEdge has no relationship-type/call-site field.
        """
        try:
            async with asyncio.timeout(_ANALYSIS_TIMEOUT_SECONDS):
                async with self.driver.session(
                    default_access_mode=READ_ACCESS
                ) as session:
                    async with await session.begin_transaction(
                        timeout=_TRANSACTION_TIMEOUT_SECONDS
                    ) as tx:
                        return await self._analyze(tx, repo_id, request)
        except TimeoutError as exc:
            raise ApiError(
                503, "IMPACT_TIMEOUT", "Impact analysis exceeded its time limit."
            ) from exc
        except (Neo4jError, DriverError) as exc:
            raise ApiError(
                503, "IMPACT_UNAVAILABLE", "Impact analysis is temporarily unavailable."
            ) from exc

    @staticmethod
    async def _rows(
        tx: AsyncTransaction, query: str, repo_id: UUID, **parameters: object
    ) -> list[Record]:
        result = await tx.run(
            query,
            {
                "repo_id": str(repo_id),
                "row_limit": _MAX_QUERY_ROWS + 1,
                **parameters,
            },
        )
        rows: list[Record] = []
        async for record in result:
            rows.append(record)
            if len(rows) > _MAX_QUERY_ROWS:
                raise _too_large()
        return rows

    async def _analyze(
        self, tx: AsyncTransaction, repo_id: UUID, request: ImpactRequest
    ) -> ImpactResponse:
        rows = await self._rows(
            tx, _TARGET_QUERY, repo_id, target=request.target_qualified_name
        )
        if not rows:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "Impact target not found.")
        if len(rows) != 1:
            raise ApiError(503, "IMPACT_INVALID_GRAPH", "Impact target is not unique.")
        target = _Target.model_validate(rows[0]["node"])
        class_target = "Class" in target.labels
        if not class_target and "Function" not in target.labels:
            raise ApiError(
                422,
                "IMPACT_UNSUPPORTED_TARGET",
                "Impact analysis requires a Function or Class target.",
            )
        radius = _Radius(target.qualified_name)
        radius.add_node(_Node.model_validate(target.model_dump(exclude={"labels"})))
        if class_target:
            rows = await self._rows(
                tx, _CLASS_QUERY, repo_id, target=target.qualified_name
            )
            for row in rows:
                owner = _Node.model_validate(row["owner"])
                radius.add_node(owner)
                if row["method"] is not None:
                    method = _Node.model_validate(row["method"])
                    radius.add_node(method)
                    radius.seeds.add(method.qualified_name)
                    radius.add_edge(owner.qualified_name, method.qualified_name, 0)
                if row["inherit_source"] is not None:
                    radius.add_edge(row["inherit_source"], row["inherit_target"], 0)
        else:
            radius.seeds.add(target.qualified_name)
        radius.upstream = dict.fromkeys(radius.seeds, 0)
        radius.downstream = dict.fromkeys(radius.seeds, 0)
        if radius.seeds:
            rows = await self._rows(
                tx,
                _UPSTREAM_QUERIES[request.max_hops],
                repo_id,
                names=sorted(radius.seeds),
            )
            radius.add_paths(rows, upstream=True)
            rows = await self._rows(
                tx,
                _DOWNSTREAM_QUERIES[request.max_hops],
                repo_id,
                names=sorted(radius.seeds),
            )
            radius.add_paths(rows, upstream=False)
            rows = await self._rows(
                tx, _ROUTES_QUERY, repo_id, names=sorted(radius.upstream)
            )
            for row in rows:
                endpoint = _Node.model_validate(row["endpoint"])
                radius.add_node(endpoint)
                radius.endpoints.add(endpoint.qualified_name)
                radius.add_edge(
                    endpoint.qualified_name,
                    row["handler"],
                    max(1, radius.upstream[row["handler"]]),
                )
            distances = radius.upstream.copy()
            for name, depth in radius.downstream.items():
                distances[name] = min(distances.get(name, depth), depth)
            rows = await self._rows(tx, _DB_QUERY, repo_id, names=sorted(distances))
            for row in rows:
                table = _Node.model_validate(row["table"])
                radius.add_node(table)
                radius.tables.add(table.name)
                radius.add_edge(
                    row["source"], table.qualified_name, distances[row["source"]]
                )
        return radius.response(request.max_hops, class_target=class_target)
