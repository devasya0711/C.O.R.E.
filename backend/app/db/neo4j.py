"""Async Neo4j lifecycle and idempotent database-wide schema initialization."""

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.config import Settings

SCHEMA_QUERIES: tuple[str, ...] = (
    "CREATE CONSTRAINT code_node_id_unique IF NOT EXISTS "
    "FOR (n:CodeNode) REQUIRE n.id IS UNIQUE",
    "CREATE INDEX code_node_repo_idx IF NOT EXISTS FOR (n:CodeNode) ON (n.repo_id)",
    "CREATE INDEX code_node_qname_idx IF NOT EXISTS "
    "FOR (n:CodeNode) ON (n.repo_id, n.qualified_name)",
    "CREATE VECTOR INDEX function_embedding_idx IF NOT EXISTS "
    "FOR (f:Function) ON (f.embedding) OPTIONS {indexConfig: "
    "{`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}",
)


def create_neo4j_driver(settings: Settings) -> AsyncDriver | None:
    """Create no driver unless all connection settings are explicitly supplied."""
    if not settings.neo4j_uri or not settings.neo4j_user or not settings.neo4j_password:
        return None
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        connection_timeout=10,
        max_transaction_retry_time=10,
    )


async def initialize_schema(driver: AsyncDriver) -> None:
    """Apply literal DDL only; no tenant data is read by schema maintenance."""
    await driver.verify_connectivity()
    async with driver.session() as session:
        for query in SCHEMA_QUERIES:
            result = await session.run(query)
            await result.consume()
