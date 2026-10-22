"""Portable validation for credential-safe maintenance command construction."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.maintenance_common import (
    parse_neo4j_database,
    parse_postgres_url,
    postgres_connection_args,
    postgres_environment,
    validate_restore_confirmation,
)
from scripts.neo4j_backup import build_command as build_neo4j_backup_command
from scripts.neo4j_restore import build_command as build_neo4j_restore_command
from scripts.postgres_backup import build_command as build_postgres_backup_command
from scripts.postgres_restore import build_command as build_postgres_restore_command


def test_postgres_command_uses_passfile_instead_of_password_argv(
    tmp_path: Path,
) -> None:
    target = parse_postgres_url(
        "postgresql+asyncpg://core_user:p%40ssword@localhost:5432/core_db"
    )
    backup = tmp_path / "core.dump"

    command = build_postgres_backup_command(target, backup)
    restore_command = build_postgres_restore_command(target, backup)

    assert "p@ssword" not in command
    assert "p@ssword" not in restore_command
    assert "postgresql" not in command
    with postgres_environment(target) as environment:
        passfile = Path(environment["PGPASSFILE"])
        assert passfile.exists()
        assert passfile.stat().st_mode & 0o777 == 0o600
        assert passfile.read_text(encoding="utf-8").endswith("p@ssword\n")
    assert not passfile.exists()


def test_postgres_restore_requires_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="destructive"):
        validate_restore_confirmation(False)
    validate_restore_confirmation(True)


def test_neo4j_commands_have_no_authentication_arguments(tmp_path: Path) -> None:
    database = parse_neo4j_database("core_graph")
    backup = build_neo4j_backup_command("neo4j-admin", database, tmp_path)
    restore = build_neo4j_restore_command("neo4j-admin", database, tmp_path)

    assert backup == [
        "neo4j-admin",
        "database",
        "dump",
        "core_graph",
        f"--to-path={tmp_path}",
    ]
    assert "password" not in " ".join(restore).lower()
    with pytest.raises(ValueError):
        parse_neo4j_database("../neo4j")


def test_postgres_connection_args_exclude_password() -> None:
    target = parse_postgres_url("postgresql://user:secret@db:5432/core")
    assert postgres_connection_args(target) == [
        "--host",
        "db",
        "--port",
        "5432",
        "--username",
        "user",
        "--dbname",
        "core",
    ]
