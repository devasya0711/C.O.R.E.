"""Tenant-partitioned graph snapshots and structural/semantic retrieval."""

from __future__ import annotations

import hashlib
from collections import Counter
from uuid import UUID, uuid4

from neo4j import AsyncDriver, AsyncManagedTransaction

from app.schemas.graph import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    FlowEdge,
    FlowNode,
    GraphResponse,
    NodeData,
    NodeLabel,
    Position,
    Relationship,
)

# Only fixed, developer-owned Cypher is used. AST/user strings are parameters.
_NODE_LABELS: tuple[NodeLabel, ...] = (
    "File",
    "Class",
    "Function",
    "APIEndpoint",
    "DBTable",
)
_NODE_PREFIX = "UNWIND $rows AS row MERGE (n:CodeNode {id: row.id, repo_id: $repo_id}) "
_NODE_QUERIES: dict[NodeLabel, str] = {
    "File": _NODE_PREFIX
    + "REMOVE n:Class:Function:APIEndpoint:DBTable SET n:File, n += row.props",
    "Class": _NODE_PREFIX
    + "REMOVE n:File:Function:APIEndpoint:DBTable SET n:Class, n += row.props",
    "Function": _NODE_PREFIX
    + "REMOVE n:File:Class:APIEndpoint:DBTable SET n:Function, n += row.props",
    "APIEndpoint": _NODE_PREFIX
    + "REMOVE n:File:Class:Function:DBTable SET n:APIEndpoint, n += row.props",
    "DBTable": _NODE_PREFIX
    + "REMOVE n:File:Class:Function:APIEndpoint SET n:DBTable, n += row.props",
}
_EDGE_PREFIX = (
    "UNWIND $rows AS row "
    "MATCH (a:CodeNode {id: row.source, repo_id: $repo_id}) "
    "MATCH (b:CodeNode {id: row.target, repo_id: $repo_id}) "
)
_EDGE_SUFFIX = " SET r.line = row.line, r.repo_id = $repo_id"
_EDGE_QUERIES: dict[Relationship, str] = {
    "CONTAINS": _EDGE_PREFIX
    + "MERGE (a)-[r:CONTAINS {id: row.id}]->(b)"
    + _EDGE_SUFFIX,
    "CALLS": _EDGE_PREFIX + "MERGE (a)-[r:CALLS {id: row.id}]->(b)" + _EDGE_SUFFIX,
    "INHERITS": _EDGE_PREFIX
    + "MERGE (a)-[r:INHERITS {id: row.id}]->(b)"
    + _EDGE_SUFFIX,
    "READS_FROM": _EDGE_PREFIX
    + "MERGE (a)-[r:READS_FROM {id: row.id}]->(b)"
    + _EDGE_SUFFIX,
    "WRITES_TO": _EDGE_PREFIX
    + "MERGE (a)-[r:WRITES_TO {id: row.id}]->(b)"
    + _EDGE_SUFFIX,
    "ROUTES_TO": _EDGE_PREFIX
    + "MERGE (a)-[r:ROUTES_TO {id: row.id}]->(b)"
    + _EDGE_SUFFIX,
}


def node_id(repo_id: UUID, qualified_name: str) -> str:
    return f"{repo_id}:{qualified_name}"


def edge_id(repo_id: UUID, edge: CodeEdge) -> str:
    return f"{repo_id}:{hashlib.sha256(edge.model_dump_json().encode()).hexdigest()}"


class Neo4jService:
    """The caller must verify repository ownership before supplying its UUID."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    async def upsert_graph(self, repo_id: UUID, graph: CodeGraph) -> None:
        """Replace one repository atomically; never leave stale edges or symbols."""
        async with self.driver.session() as session:
            await session.execute_write(self._write, repo_id, graph, str(uuid4()))

    @staticmethod
    async def _write(
        tx: AsyncManagedTransaction, repo_id: UUID, graph: CodeGraph, version: str
    ) -> None:
        result = await tx.run(
            "MATCH (n:CodeNode {repo_id: $repo_id})-[r]->"
            "(m:CodeNode {repo_id: $repo_id}) DELETE r",
            repo_id=str(repo_id),
        )
        await result.consume()
        for label in _NODE_LABELS:
            rows: list[dict[str, object]] = []
            for node in graph.nodes:
                if node.label != label:
                    continue
                props = node.model_dump(exclude={"label"})
                props.update(repo_id=str(repo_id), ingestion_version=version)
                rows.append(
                    {"id": node_id(repo_id, node.qualified_name), "props": props}
                )
            for offset in range(0, len(rows), 100):
                result = await tx.run(
                    _NODE_QUERIES[label],
                    rows=rows[offset : offset + 100],
                    repo_id=str(repo_id),
                )
                await result.consume()
        for relationship, query in _EDGE_QUERIES.items():
            edge_rows = [
                {
                    "id": edge_id(repo_id, edge),
                    "source": node_id(repo_id, edge.source),
                    "target": node_id(repo_id, edge.target),
                    "line": edge.line,
                }
                for edge in graph.edges
                if edge.relationship == relationship
            ]
            for offset in range(0, len(edge_rows), 200):
                result = await tx.run(
                    query, rows=edge_rows[offset : offset + 200], repo_id=str(repo_id)
                )
                await result.consume()
        result = await tx.run(
            "MATCH (n:CodeNode {repo_id: $repo_id}) "
            "WHERE coalesce(n.ingestion_version, '') <> $version DETACH DELETE n",
            repo_id=str(repo_id),
            version=version,
        )
        await result.consume()

    async def load_graph(self, repo_id: UUID, *, embeddings: bool = False) -> CodeGraph:
        """Read only this repository; vectors are excluded from the studio payload."""
        async with self.driver.session() as session:
            return await session.execute_read(self._read, repo_id, embeddings)

    @staticmethod
    async def _read(
        tx: AsyncManagedTransaction, repo_id: UUID, embeddings: bool
    ) -> CodeGraph:
        result = await tx.run(
            "MATCH (n:CodeNode {repo_id: $repo_id}) "
            "RETURN n{.*, embedding: CASE WHEN $embeddings THEN n.embedding "
            "ELSE null END} AS props, labels(n) AS labels ORDER BY n.qualified_name",
            repo_id=str(repo_id),
            embeddings=embeddings,
        )
        nodes: list[CodeNode] = []
        async for record in result:
            labels = [label for label in _NODE_LABELS if label in record["labels"]]
            if len(labels) != 1:
                raise ValueError("Graph node has an invalid source label")
            props = record["props"]
            nodes.append(
                CodeNode.model_validate(
                    {
                        "label": labels[0],
                        **{
                            name: props[name]
                            for name in CodeNode.model_fields
                            if name != "label" and name in props
                        },
                    }
                )
            )
        result = await tx.run(
            "MATCH (a:CodeNode {repo_id: $repo_id})-[r]->"
            "(b:CodeNode {repo_id: $repo_id}) "
            "WHERE type(r) IN $types "
            "RETURN a.qualified_name AS source, b.qualified_name AS target, "
            "type(r) AS relationship, r.line AS line "
            "ORDER BY source, target, relationship, coalesce(line, 0)",
            repo_id=str(repo_id),
            types=list(_EDGE_QUERIES),
        )
        edges = [CodeEdge.model_validate(dict(record)) async for record in result]
        return CodeGraph(nodes=nodes, edges=edges)

    async def flow_graph(
        self, repo_id: UUID, shared_tables: dict[int, list[str]] | None = None
    ) -> GraphResponse:
        graph = await self.load_graph(repo_id)
        incoming = Counter(edge.target for edge in graph.edges)
        outgoing = Counter(edge.source for edge in graph.edges)
        nodes = [
            FlowNode(
                id=node_id(repo_id, node.qualified_name),
                position=Position(
                    x=float((index % 8) * 280), y=float((index // 8) * 160)
                ),
                data=NodeData(
                    **node.model_dump(),
                    incoming_count=incoming[node.qualified_name],
                    outgoing_count=outgoing[node.qualified_name],
                    shared_db_tables=(shared_tables or {}).get(node.community_id, [])
                    if node.community_id is not None
                    else [],
                ),
            )
            for index, node in enumerate(graph.nodes)
        ]
        return GraphResponse(
            nodes=nodes,
            edges=[
                FlowEdge(
                    id=edge_id(repo_id, edge),
                    source=node_id(repo_id, edge.source),
                    target=node_id(repo_id, edge.target),
                    label=edge.relationship,
                    data=edge,
                )
                for edge in graph.edges
            ],
        )

    async def retrieve_context(
        self, repo_id: UUID, embedding: list[float], *, limit: int = 8
    ) -> CodeGraph:
        """Combine scoped semantic ranking with one-hop deterministic topology.

        Neo4j 5's global vector index cannot prefilter by tenant. Rank only the
        indexed repository namespace instead of retrieving another tenant's
        global top-k and discarding it after the fact.
        """
        CodeNode.valid_embedding(embedding)
        if not any(embedding) or not 1 <= limit <= 100:
            raise ValueError("Invalid semantic context request")
        records, _, _ = await self.driver.execute_query(
            "MATCH (f:Function:CodeNode {repo_id: $repo_id}) "
            "WHERE f.embedding IS NOT NULL "
            "RETURN f.qualified_name AS name, "
            "vector.similarity.cosine(f.embedding, $embedding) AS score "
            "ORDER BY score DESC, name LIMIT $limit",
            repo_id=str(repo_id),
            embedding=embedding,
            limit=limit,
        )
        seeds = {str(record["name"]) for record in records}
        graph = await self.load_graph(repo_id)
        selected = seeds.copy()
        for edge in graph.edges:
            if edge.source in seeds or edge.target in seeds:
                selected.update((edge.source, edge.target))
        return CodeGraph(
            nodes=[node for node in graph.nodes if node.qualified_name in selected],
            edges=[
                edge
                for edge in graph.edges
                if edge.source in selected and edge.target in selected
            ],
        )
