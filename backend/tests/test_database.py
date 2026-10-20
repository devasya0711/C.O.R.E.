"""Portable SQL tests and explicitly gated, migrated PostgreSQL integration tests.

Set TEST_DATABASE_URL to a disposable PostgreSQL database where the test role can
create schemas. PostgreSQL cases use real Alembic migrations and isolated schemas;
SQLite cases only verify portable ORM/transaction behavior, not PostgreSQL types.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db import (
    Base,
    IngestionJob,
    MigrationJob,
    Repository,
    SeamCluster,
    User,
    create_database,
    get_session,
)
from fastapi import FastAPI, Request
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    delete,
    event,
    func,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import AdaptedConnection, Connection, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

BACKEND = Path(__file__).resolve().parents[1]
BASE_REVISION = "0001_postgresql"
HEAD_REVISION = "0002_migration_workflow"


@dataclass
class Database:
    """One isolated database/schema and its real session factory."""

    factory: async_sessionmaker[AsyncSession]
    connection: AsyncConnection
    schema: str | None


@dataclass
class Rows:
    """A complete persisted workspace used by database behavior tests."""

    user: User
    repository: Repository
    seam: SeamCluster
    migration: MigrationJob
    ingestion: IngestionJob


def _alembic_config(output: StringIO | None = None) -> Config:
    return Config(str(BACKEND / "alembic.ini"), output_buffer=output)


def _migrate(connection: Connection, target: str) -> None:
    config = _alembic_config()
    config.attributes["connection"] = connection
    if target == "base":
        command.downgrade(config, target)
    else:
        command.upgrade(config, target)


async def _configure_sqlite(driver: aiosqlite.Connection) -> None:
    # Test-only stand-in for PostgreSQL's built-in UUID function, not production.
    await driver.create_function("gen_random_uuid", 0, lambda: uuid4().hex)
    await driver.execute("PRAGMA foreign_keys=ON")


@pytest_asyncio.fixture(params=["sqlite", "postgresql"], ids=["sqlite", "postgresql"])
async def database(request: pytest.FixtureRequest) -> AsyncIterator[Database]:
    """Never silently substitute SQLite for real PostgreSQL integration cases."""

    postgres = request.param == "postgresql"
    database_url = os.environ.get("TEST_DATABASE_URL") if postgres else None
    if postgres and not database_url:
        pytest.skip("Real PostgreSQL integration requires TEST_DATABASE_URL.")
    if database_url and make_url(database_url).get_backend_name() not in {
        "postgresql",
        "postgres",
    }:
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL.")

    engine, _ = create_database(database_url or "sqlite+aiosqlite:///:memory:")
    if not postgres:

        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(
            connection: AdaptedConnection, _record: ConnectionPoolEntry
        ) -> None:
            connection.run_async(_configure_sqlite)

    schema = f"test_core_{uuid4().hex}" if postgres else None
    try:
        async with engine.connect() as connection:
            try:
                if schema:
                    # Identifiers are generated here, never taken from user input.
                    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                    await connection.execute(text(f'SET search_path TO "{schema}"'))
                    await connection.commit()
                    await connection.run_sync(_migrate, "head")
                else:
                    await connection.run_sync(Base.metadata.create_all)
                await connection.commit()
                yield Database(
                    async_sessionmaker(connection, expire_on_commit=False),
                    connection,
                    schema,
                )
            finally:
                await connection.rollback()
                if schema:
                    await connection.execute(text("SET search_path TO public"))
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                    await connection.commit()
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> Rows:
    user = User(clerk_id=f"clerk_{uuid4().hex}", email=f"{uuid4().hex}@example.test")
    repository = Repository(
        user=user, name="sample_monolith", repo_url="local://sample_monolith"
    )
    seam = SeamCluster(
        repository=repository,
        community_id=1,
        suggested_name="payment-service",
        cohesion_score=0.87,
    )
    migration = MigrationJob(repository=repository, seam=seam)
    ingestion = IngestionJob(repository=repository)
    session.add_all([user, repository, seam, migration, ingestion])
    await session.flush()
    return Rows(user, repository, seam, migration, ingestion)


def _request(factory: async_sessionmaker[AsyncSession]) -> Request:
    application = FastAPI()
    application.state.session_factory = factory
    return Request({"type": "http", "app": application})


@pytest.mark.asyncio
async def test_factory_is_lazy_and_uses_asyncpg() -> None:
    engine, factory = create_database("postgresql://localhost/core_test")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "asyncpg"
        assert engine.sync_engine.hide_parameters
        assert not engine.echo
        async with factory() as session:
            assert not session.in_transaction()
            assert session.bind is engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_defaults(database: Database) -> None:
    async with database.factory.begin() as session:
        rows = await _seed(session)
        identifiers = {
            rows.user.id,
            rows.repository.id,
            rows.seam.id,
            rows.migration.id,
            rows.ingestion.id,
        }
        assert len(identifiers) == 5
        assert all(isinstance(identifier, UUID) for identifier in identifiers)
        assert rows.repository.default_branch == "main"
        assert rows.repository.status == "PENDING"
        assert rows.repository.node_count == rows.repository.edge_count == 0
        assert rows.repository.last_commit_sha is None
        assert rows.user.encrypted_github_pat is None
        assert rows.seam.node_ids == rows.seam.shared_db_tables == []
        assert rows.migration.status == "QUEUED"
        assert rows.migration.crag_status == "PENDING"
        assert rows.migration.retry_count == 0
        assert rows.migration.generated_artifacts == {}
        assert rows.migration.sandbox_logs is rows.migration.pr_url is None
        assert rows.ingestion.status == rows.ingestion.stage == "QUEUED"
        assert rows.ingestion.progress == 0
        assert rows.ingestion.error is None
        assert rows.ingestion.started_at is rows.ingestion.completed_at is None
        for timestamp in (
            rows.user.created_at,
            rows.repository.created_at,
            rows.migration.created_at,
            rows.ingestion.created_at,
            rows.ingestion.updated_at,
        ):
            assert isinstance(timestamp, datetime)
            if database.schema:
                assert timestamp.utcoffset() is not None


@pytest.mark.asyncio
async def test_raw_insert_uses_server_defaults(database: Database) -> None:
    async with database.factory.begin() as session:
        # Raw SQL bypasses every ORM-side default.
        row = (
            await session.execute(
                text(
                    "INSERT INTO users (clerk_id, email) "
                    "VALUES (:clerk_id, :email) RETURNING id, created_at"
                ),
                {"clerk_id": "raw-defaults", "email": "raw@example.test"},
            )
        ).one()
        assert UUID(str(row.id)).version == 4
        assert row.created_at is not None


@pytest.mark.asyncio
async def test_crud_and_json_mutations_persist(database: Database) -> None:
    async with database.factory.begin() as session:
        rows = await _seed(session)
        user_id, repo_id, seam_id = rows.user.id, rows.repository.id, rows.seam.id
        migration_id, ingestion_id = rows.migration.id, rows.ingestion.id
        rows.user.encrypted_github_pat = "encrypted-ciphertext"
        rows.repository.status = "READY"
        rows.repository.node_count = 42
        rows.repository.edge_count = 73
        rows.seam.shared_db_tables.append("ledger_entries")
        rows.seam.node_ids.append("payments.processor.PaymentProcessor.execute")
        rows.migration.generated_artifacts["service/main.py"] = "print('verified')\n"
        rows.migration.status = "COMPLETED"
        rows.migration.crag_status = "AUGMENTED"
        rows.migration.retry_count = 3
        rows.migration.sandbox_logs = "3 passed"
        rows.migration.pr_url = "https://github.com/example/project/pull/1"
        rows.ingestion.status = "RUNNING"
        rows.ingestion.stage = "AST_PARSING"
        rows.ingestion.progress = 50
        rows.ingestion.started_at = datetime.now(UTC)

    async with database.factory.begin() as session:
        user = await session.get(User, user_id)
        repository = await session.get(Repository, repo_id)
        seam = await session.get(SeamCluster, seam_id)
        migration = await session.get(MigrationJob, migration_id)
        ingestion = await session.get(IngestionJob, ingestion_id)
        assert user is not None and user.encrypted_github_pat == "encrypted-ciphertext"
        assert repository is not None and repository.status == "READY"
        assert repository.node_count == 42 and repository.edge_count == 73
        assert seam is not None and seam.shared_db_tables == ["ledger_entries"]
        assert seam.node_ids == ["payments.processor.PaymentProcessor.execute"]
        assert migration is not None and migration.generated_artifacts == {
            "service/main.py": "print('verified')\n"
        }
        assert migration.status == "COMPLETED" and migration.retry_count == 3
        assert migration.crag_status == "AUGMENTED"
        assert migration.sandbox_logs == "3 passed"
        assert migration.pr_url == "https://github.com/example/project/pull/1"
        assert ingestion is not None and ingestion.progress == 50
        assert ingestion.stage == "AST_PARSING" and ingestion.started_at is not None
        assert ingestion.updated_at >= ingestion.created_at
        ingestion.status = "FAILED"
        ingestion.stage = "FAILED"
        ingestion.error = "AST_PARSE_ERROR"
        ingestion.completed_at = datetime.now(UTC)
        await session.delete(migration)

    async with database.factory() as session:
        assert await session.get(MigrationJob, migration_id) is None
        ingestion = await session.get(IngestionJob, ingestion_id)
        assert ingestion is not None and ingestion.error == "AST_PARSE_ERROR"
        assert ingestion.status == "FAILED" and ingestion.completed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", ["user", "repository", "seam"])
async def test_database_fk_cascades(database: Database, parent: str) -> None:
    async with database.factory.begin() as session:
        rows = await _seed(session)
        user_id, repo_id, seam_id = rows.user.id, rows.repository.id, rows.seam.id

    async with database.factory.begin() as session:
        # Bulk SQL ensures the database, rather than ORM cascade logic, deletes.
        if parent == "user":
            await session.execute(delete(User).where(User.id == user_id))
        elif parent == "repository":
            await session.execute(delete(Repository).where(Repository.id == repo_id))
        else:
            await session.execute(delete(SeamCluster).where(SeamCluster.id == seam_id))

    async with database.factory() as session:
        assert await session.scalar(select(func.count()).select_from(MigrationJob)) == 0
        assert await session.scalar(select(func.count()).select_from(SeamCluster)) == 0
        expected_repo = 1 if parent == "seam" else 0
        assert await session.scalar(select(func.count()).select_from(Repository)) == (
            expected_repo
        )
        assert await session.scalar(select(func.count()).select_from(IngestionJob)) == (
            expected_repo
        )
        expected_user = 0 if parent == "user" else 1
        assert (
            await session.scalar(select(func.count()).select_from(User))
            == expected_user
        )


INVALID_VALUES: list[tuple[type[Base], str, object]] = [
    (Repository, "status", "UNKNOWN"),
    (Repository, "node_count", -1),
    (Repository, "edge_count", -1),
    (SeamCluster, "cohesion_score", -0.01),
    (SeamCluster, "cohesion_score", 1.01),
    (MigrationJob, "status", "UNKNOWN"),
    (MigrationJob, "crag_status", "UNKNOWN"),
    (MigrationJob, "retry_count", -1),
    (MigrationJob, "retry_count", 4),
    (IngestionJob, "status", "UNKNOWN"),
    (IngestionJob, "stage", "UNKNOWN"),
    (IngestionJob, "progress", -1),
    (IngestionJob, "progress", 101),
    (User, "clerk_id", None),
    (Repository, "user_id", None),
    (SeamCluster, "repo_id", None),
    (MigrationJob, "repo_id", None),
    (MigrationJob, "seam_id", None),
    (IngestionJob, "repo_id", None),
    (SeamCluster, "node_ids", None),
    (SeamCluster, "shared_db_tables", None),
    (MigrationJob, "generated_artifacts", None),
    (Repository, "user_id", uuid4()),
    (SeamCluster, "repo_id", uuid4()),
    (MigrationJob, "repo_id", uuid4()),
    (MigrationJob, "seam_id", uuid4()),
    (IngestionJob, "repo_id", uuid4()),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "column", "value"), INVALID_VALUES)
async def test_database_rejects_invalid_values(
    database: Database, model: type[Base], column: str, value: object
) -> None:
    async with database.factory.begin() as session:
        await _seed(session)
    with pytest.raises(IntegrityError):
        async with database.factory.begin() as session:
            await session.execute(update(model).values({column: value}))


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["clerk_id", "email"])
async def test_user_identity_is_unique(database: Database, column: str) -> None:
    async with database.factory.begin() as session:
        rows = await _seed(session)
        duplicate = getattr(rows.user, column)
    values = {"clerk_id": "another-clerk", "email": "another@example.test"}
    values[column] = duplicate
    with pytest.raises(IntegrityError):
        async with database.factory.begin() as session:
            await session.execute(insert(User).values(**values))


@pytest.mark.asyncio
async def test_session_dependency_commits(database: Database) -> None:
    dependency = get_session(_request(database.factory))
    session = await anext(dependency)
    user = User(clerk_id="committed", email="committed@example.test")
    session.add(user)
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)
    async with database.factory() as verification:
        assert await verification.get(User, user.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_session_dependency_rolls_back(
    database: Database, failure: type[BaseException]
) -> None:
    dependency = get_session(_request(database.factory))
    session = await anext(dependency)
    session.add(User(clerk_id="rolled-back", email="rollback@example.test"))
    await session.flush()
    with pytest.raises(failure):
        await dependency.athrow(failure("request failed"))
    async with database.factory() as verification:
        assert await verification.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.asyncio
async def test_session_dependency_close_rolls_back(database: Database) -> None:
    dependency = get_session(_request(database.factory))
    session = await anext(dependency)
    session.add(User(clerk_id="closed", email="closed@example.test"))
    await session.flush()
    await dependency.aclose()
    async with database.factory() as verification:
        assert await verification.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.asyncio
async def test_session_dependency_commit_failure_rolls_back(database: Database) -> None:
    async with database.factory.begin() as session:
        session.add(User(clerk_id="duplicate", email="existing@example.test"))
    dependency = get_session(_request(database.factory))
    session = await anext(dependency)
    session.add(User(clerk_id="duplicate", email="new@example.test"))
    with pytest.raises(IntegrityError):
        await anext(dependency)
    async with database.factory.begin() as verification:
        assert await verification.scalar(select(func.count()).select_from(User)) == 1
        verification.add(User(clerk_id="recovered", email="new@example.test"))


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_none", [False, True])
async def test_unconfigured_dependency_raises_standard_503(explicit_none: bool) -> None:
    errors = pytest.importorskip(
        "app.core.errors", reason="Shared ApiError is supplied by API integration."
    )
    application = FastAPI()
    if explicit_none:
        application.state.session_factory = None
    request = Request({"type": "http", "app": application})
    with pytest.raises(errors.ApiError) as caught:
        await anext(get_session(request))
    assert caught.value.status_code == 503
    assert caught.value.code == "DATABASE_UNAVAILABLE"
    assert caught.value.message == "Database is not configured."


def test_alembic_postgresql_offline_upgrade_and_downgrade() -> None:
    upgrade = StringIO()
    command.upgrade(_alembic_config(upgrade), "head", sql=True)
    sql = upgrade.getvalue()
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table} (" in sql
    assert sql.count("DEFAULT gen_random_uuid()") == 5
    assert sql.count("JSONB") == 3
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "ON DELETE CASCADE" in sql
    assert "ck_ingestion_jobs_progress CHECK" in sql
    assert "ck_repositories_ck_" not in sql

    downgrade = StringIO()
    command.downgrade(_alembic_config(downgrade), f"{BASE_REVISION}:base", sql=True)
    sql = downgrade.getvalue()
    for table in Base.metadata.tables:
        assert f"DROP TABLE {table};" in sql
    assert sql.index("DROP TABLE migration_jobs;") < sql.index(
        "DROP TABLE seam_clusters;"
    )
    assert sql.index("DROP TABLE repositories;") < sql.index("DROP TABLE users;")


@pytest.mark.asyncio
async def test_postgresql_native_types_and_migration_roundtrip(
    database: Database,
) -> None:
    if not database.schema:
        pytest.skip("PostgreSQL native types and Alembic require real PostgreSQL.")

    def verify_schema(connection: Connection) -> None:
        inspector = inspect(connection)
        for table in Base.metadata.sorted_tables:
            actual = {
                column["name"]: column
                for column in inspector.get_columns(table.name, schema=database.schema)
            }
            assert set(actual) == set(table.columns.keys())
            assert str(actual["id"]["type"]) == "UUID"
            uuid_default = actual["id"]["default"]
            assert uuid_default is not None and "gen_random_uuid()" in uuid_default
            for column in table.columns:
                if column.name.endswith("_at"):
                    timestamp_type = actual[column.name]["type"]
                    assert isinstance(timestamp_type, DateTime)
                    assert timestamp_type.timezone
            expected_checks = {
                str(constraint.name)
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }
            assert {
                check["name"]
                for check in inspector.get_check_constraints(
                    table.name, schema=database.schema
                )
            } == expected_checks
        for table_name, column_name in (
            ("seam_clusters", "node_ids"),
            ("seam_clusters", "shared_db_tables"),
            ("migration_jobs", "generated_artifacts"),
        ):
            columns = inspector.get_columns(table_name, schema=database.schema)
            actual_column = next(
                item for item in columns if item["name"] == column_name
            )
            actual_type = actual_column["type"]
            assert str(actual_type) == "JSONB"
        migration_context = MigrationContext.configure(connection)
        assert compare_metadata(migration_context, Base.metadata) == []

    await database.connection.run_sync(verify_schema)
    await database.connection.commit()
    await database.connection.run_sync(_migrate, "base")

    def remaining_tables(connection: Connection) -> list[str]:
        return inspect(connection).get_table_names(schema=database.schema)

    assert await database.connection.run_sync(remaining_tables) == ["alembic_version"]
    await database.connection.commit()
    await database.connection.run_sync(_migrate, "head")
    await database.connection.commit()
    await database.connection.run_sync(verify_schema)


@pytest.mark.asyncio
async def test_postgresql_enforces_varchar_length(database: Database) -> None:
    if not database.schema:
        pytest.skip("SQLite does not enforce PostgreSQL VARCHAR length limits.")
    with pytest.raises(DBAPIError):
        async with database.factory.begin() as session:
            session.add(User(clerk_id="x" * 129, email="length@example.test"))


def test_postgresql_alembic_cli_uses_real_async_engine() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Real async Alembic CLI integration requires TEST_DATABASE_URL.")
    # The CLI owns a real async engine against a fresh database. Most tests only
    # need CREATE SCHEMA; this extra case requires the test role's CREATEDB bit.
    database_name = f"test_core_cli_{uuid4().hex}"

    async def prepare_database(drop: bool = False) -> None:
        engine, _ = create_database(database_url)
        try:
            async with engine.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                can_create = await connection.scalar(
                    text(
                        "SELECT rolcreatedb OR rolsuper FROM pg_roles "
                        "WHERE rolname = current_user"
                    )
                )
                if not can_create:
                    pytest.skip("Async Alembic CLI test requires a CREATEDB test role.")
                statement = (
                    f'DROP DATABASE "{database_name}" WITH (FORCE)'
                    if drop
                    else f'CREATE DATABASE "{database_name}"'
                )
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(prepare_database())
    environment = os.environ.copy()
    url = make_url(database_url).set(database=database_name)
    # Credentials stay in the child environment, never command arguments/logs.
    environment["DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        for arguments in (["upgrade", "head"], ["current"], ["downgrade", "base"]):
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, "Async Alembic CLI migration failed."
            if arguments == ["current"]:
                assert HEAD_REVISION in result.stdout
    finally:
        asyncio.run(prepare_database(drop=True))
