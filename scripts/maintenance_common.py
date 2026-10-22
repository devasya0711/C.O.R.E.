"""Shared, credential-safe helpers for database maintenance commands."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True, slots=True)
class PostgresTarget:
    """Connection fields passed to libpq without embedding a password."""

    host: str | None
    port: int | None
    username: str | None
    database: str
    password: str | None


def parse_postgres_url(value: str) -> PostgresTarget:
    """Parse a PostgreSQL URL supplied through the environment."""

    normalized = value.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("database URL must use the postgres or postgresql scheme")
    if parsed.query or parsed.fragment:
        raise ValueError("database URL query parameters and fragments are unsupported")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("database URL contains an invalid host or port") from error
    database = unquote(parsed.path.lstrip("/"))
    if not database or "/" in database:
        raise ValueError("database URL must include one database name")
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    return PostgresTarget(host, port, username, database, password)


def postgres_connection_args(target: PostgresTarget) -> list[str]:
    """Build libpq arguments without ever placing the password in argv."""

    args: list[str] = []
    if target.host:
        args.extend(("--host", target.host))
    if target.port:
        args.extend(("--port", str(target.port)))
    if target.username:
        args.extend(("--username", target.username))
    args.extend(("--dbname", target.database))
    return args


@contextmanager
def postgres_environment(target: PostgresTarget) -> Iterator[dict[str, str]]:
    """Provide a private temporary passfile instead of a password argv value."""

    environment = os.environ.copy()
    if not target.password:
        yield environment
        return

    with NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="core-pgpass-", delete=False
    ) as passfile:
        passfile.write(
            ":".join(
                (
                    target.host or "*",
                    str(target.port) if target.port else "*",
                    target.database,
                    target.username or "*",
                    _escape_pgpass(target.password),
                )
            )
            + "\n"
        )
        passfile_path = Path(passfile.name)
    passfile_path.chmod(0o600)
    environment["PGPASSFILE"] = str(passfile_path)
    try:
        yield environment
    finally:
        passfile_path.unlink(missing_ok=True)


def run_command(
    command: Sequence[str], environment: dict[str, str], label: str
) -> None:
    """Run a maintenance binary without logging its command or output."""

    try:
        subprocess.run(
            list(command),
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} executable is unavailable") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"{label} failed with exit code {error.returncode}"
        ) from error


def ensure_directory(path: Path) -> None:
    """Create a backup directory with restrictive permissions when needed."""

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ValueError(f"backup path is not a directory: {path}")
    if path.stat().st_mode & 0o077:
        raise ValueError(f"backup directory must not be group/world accessible: {path}")


def validate_restore_confirmation(confirmed: bool) -> None:
    """Require an explicit flag before a destructive restore operation."""

    if not confirmed:
        raise ValueError(
            "restore is destructive; pass --yes-i-really-mean-it to continue"
        )


def parse_neo4j_database(value: str) -> str:
    """Accept only a database identifier, never a path or shell fragment."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", value):
        raise ValueError("Neo4j database name contains unsupported characters")
    return value


def _escape_pgpass(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


__all__ = [
    "PostgresTarget",
    "ensure_directory",
    "parse_neo4j_database",
    "parse_postgres_url",
    "postgres_connection_args",
    "postgres_environment",
    "run_command",
    "validate_restore_confirmation",
]
