"""Shared, credential-safe helpers for database maintenance commands."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp, mkstemp
from urllib.parse import unquote, urlsplit

_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_CREDENTIALS_RE = re.compile(
    r"(?i)(\bpostgres(?:ql)?(?:\+[-\w.]+)?://[^/\s:@]+:)[^@\s]+(@)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(PGPASSWORD|PGPASSFILE|NEO4J_PASSWORD|NEO4J_AUTH|"
    r"DATABASE_URL|ENCRYPTION_SECRET_KEY|OPENAI_API_KEY|GITHUB_TOKEN|GH_TOKEN)"
    r"=\S+"
)
_MAX_DIAGNOSTIC_LENGTH = 512
_POSTGRES_CONNECTION_ENVIRONMENT_KEYS = frozenset(
    {
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "PGPASSFILE",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGSYSCONFDIR",
        "PGOPTIONS",
        "PGAPPNAME",
    }
)
_SENSITIVE_ENVIRONMENT_KEYS = frozenset(
    {
        "DATABASE_URL",
        "ENCRYPTION_SECRET_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "NEO4J_AUTH",
        "NEO4J_PASSWORD",
        "NEO4J_URI",
        "OPENAI_API_KEY",
        "PGPASSFILE",
        "PGPASSWORD",
    }
)


def _validate_text(value: str, field_name: str) -> None:
    if _CONTROL_CHARACTER_RE.search(value) or any(
        unicodedata.category(character) == "Cc" for character in value
    ):
        raise ValueError(f"{field_name} contains a newline, NUL, or control character")


def _validate_database_name(value: str) -> None:
    _validate_text(value, "PostgreSQL database name")
    if not value or "/" in value:
        raise ValueError("database URL must include one database name")
    if "=" in value or "://" in value:
        raise ValueError(
            "PostgreSQL database name must not be a connection string or URI"
        )


@dataclass(frozen=True, slots=True)
class PostgresTarget:
    """Connection fields passed to libpq without embedding a password."""

    host: str | None
    port: int | None
    username: str | None
    database: str
    password: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.host is not None:
            _validate_text(self.host, "PostgreSQL host")
        if self.username is not None:
            _validate_text(self.username, "PostgreSQL username")
        if self.password is not None:
            _validate_text(self.password, "PostgreSQL password")
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValueError("PostgreSQL port must be between 1 and 65535")
        _validate_database_name(self.database)


def parse_postgres_url(value: str) -> PostgresTarget:
    """Parse a PostgreSQL URL supplied through the environment."""

    if not isinstance(value, str):
        raise TypeError("database URL must be text")
    _validate_text(value, "database URL")
    normalized = value.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("database URL must use the postgres or postgresql scheme")
    if parsed.query or parsed.fragment:
        raise ValueError("database URL query parameters and fragments are unsupported")
    try:
        host = unquote(parsed.hostname) if parsed.hostname else None
        port = parsed.port
    except ValueError as error:
        raise ValueError("database URL contains an invalid host or port") from error
    database = unquote(parsed.path.lstrip("/"))
    username = unquote(parsed.username) if parsed.username is not None else None
    password = unquote(parsed.password) if parsed.password is not None else None
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
    environment.pop("DATABASE_URL", None)
    for key in _POSTGRES_CONNECTION_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    if target.password is None:
        yield environment
        return

    with NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="core-pgpass-", delete=False
    ) as passfile:
        passfile.write(
            ":".join(
                (
                    _escape_pgpass(target.host or "*"),
                    _escape_pgpass(str(target.port) if target.port else "*"),
                    _escape_pgpass(target.database),
                    _escape_pgpass(target.username or "*"),
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
    command: Sequence[str],
    environment: dict[str, str],
    label: str,
    sensitive_values: Sequence[str] = (),
) -> None:
    """Run a maintenance binary with bounded, redacted failure diagnostics."""

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
        diagnostic = _safe_diagnostic(error.stderr, sensitive_values)
        message = f"{label} failed with exit code {error.returncode}"
        if diagnostic:
            message = f"{message}: {diagnostic}"
        raise RuntimeError(message) from error


def maintenance_child_environment() -> dict[str, str]:
    """Return an environment without credentials unrelated to admin commands."""

    environment = os.environ.copy()
    for key in _SENSITIVE_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    return environment


def ensure_directory(path: Path) -> None:
    """Create a backup directory with restrictive permissions when needed."""

    if path.is_symlink():
        raise ValueError(f"backup directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError(f"backup directory must not be a symlink: {path}")
    if not path.is_dir():
        raise ValueError(f"backup path is not a directory: {path}")
    if path.stat().st_mode & 0o077:
        raise ValueError(f"backup directory must not be group/world accessible: {path}")


@contextmanager
def atomic_backup_file(path: Path, force: bool) -> Iterator[Path]:
    """Stage a backup file and publish it only after the child succeeds."""

    ensure_directory(path.parent)
    _validate_backup_file_destination(path, force)
    descriptor, temporary_name = mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    published = False
    try:
        yield temporary_path
        _validate_private_file(temporary_path, require_nonempty=True)
        _validate_backup_file_destination(path, force)
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        published = True
    finally:
        if not published:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def atomic_backup_directory(path: Path, force: bool) -> Iterator[Path]:
    """Stage a Neo4j backup directory and publish it only after success."""

    ensure_directory(path.parent)
    _validate_backup_directory_destination(path, force)
    temporary_path = Path(mkdtemp(prefix=f".{path.name}.", dir=path.parent))
    os.chmod(temporary_path, 0o700)
    published = False
    old_path: Path | None = None
    try:
        yield temporary_path
        _harden_private_tree(temporary_path)
        if not any(item.is_file() for item in temporary_path.rglob("*")):
            raise ValueError("backup command produced no files")
        _validate_backup_directory_destination(path, force)
        if path.exists():
            old_path = Path(mkdtemp(prefix=f".{path.name}.old-", dir=path.parent))
            old_path.rmdir()
            os.replace(path, old_path)
        try:
            os.replace(temporary_path, path)
        except BaseException:
            if old_path is not None and not path.exists():
                os.replace(old_path, path)
            raise
        published = True
        if old_path is not None:
            shutil.rmtree(old_path)
    finally:
        if not published and temporary_path.exists():
            shutil.rmtree(temporary_path)


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


def validate_postgres_restore_input(path: Path) -> None:
    """Reject missing, unsafe, or non-custom-format PostgreSQL input."""

    _validate_private_file(path, require_nonempty=True)
    with path.open("rb") as backup:
        if backup.read(5) != b"PGDMP":
            raise ValueError("restore input is not a PostgreSQL custom-format dump")


def validate_neo4j_restore_input(path: Path) -> None:
    """Reject missing, unsafe, or malformed Neo4j dump directories."""

    if path.is_symlink() or not path.is_dir():
        raise ValueError("restore input must be an existing backup directory")
    if path.stat().st_mode & 0o077:
        raise ValueError("restore input directory must not be group/world accessible")
    entries = list(path.iterdir())
    if not entries:
        raise ValueError("restore input directory must contain a Neo4j archive")
    archives = 0
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise ValueError("restore input directory contains an unsafe entry")
        if entry.stat().st_mode & 0o077:
            raise ValueError("restore input files must not be group/world accessible")
        if entry.suffix not in {".dump", ".backup"}:
            continue
        if entry.stat().st_size == 0:
            raise ValueError("restore input contains an empty Neo4j archive")
        archives += 1
    if not archives:
        raise ValueError("restore input directory contains no .dump or .backup archive")


def _escape_pgpass(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _validate_backup_file_destination(path: Path, force: bool) -> None:
    if path.is_symlink():
        raise ValueError(f"backup output must not be a symlink: {path}")
    if not path.exists():
        return
    if not path.is_file():
        raise ValueError(f"backup output is not a regular file: {path}")
    if not force:
        raise ValueError("backup output already exists; pass --force to replace it")


def _validate_backup_directory_destination(path: Path, force: bool) -> None:
    if path.is_symlink():
        raise ValueError(f"backup output directory must not be a symlink: {path}")
    if not path.exists():
        return
    if not path.is_dir():
        raise ValueError(f"backup output is not a directory: {path}")
    if path.stat().st_mode & 0o077:
        raise ValueError(
            f"backup output directory must not be group/world accessible: {path}"
        )
    if not force:
        raise ValueError(
            "backup output directory already exists; pass --force to replace it"
        )


def _validate_private_file(path: Path, require_nonempty: bool) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("backup file must be a regular, non-symlink file")
    metadata = path.stat()
    if metadata.st_mode & 0o077:
        raise ValueError("backup file must not be group/world accessible")
    if require_nonempty and metadata.st_size == 0:
        raise ValueError("backup file must not be empty")


def _harden_private_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("backup output must be a private directory")
    os.chmod(path, 0o700)
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise ValueError("backup output must not contain symlinks")
        if entry.is_dir():
            os.chmod(entry, 0o700)
        elif entry.is_file():
            os.chmod(entry, 0o600)
        else:
            raise ValueError("backup output contains a non-regular entry")


def _safe_diagnostic(stderr: str | None, sensitive_values: Sequence[str]) -> str:
    if not stderr:
        return ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    diagnostic = " ".join(stderr.split())
    for secret in sorted(
        (value for value in sensitive_values if value), key=len, reverse=True
    ):
        diagnostic = diagnostic.replace(secret, "<redacted>")
    diagnostic = _URL_CREDENTIALS_RE.sub(r"\1<redacted>\2", diagnostic)
    diagnostic = _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", diagnostic)
    return diagnostic[:_MAX_DIAGNOSTIC_LENGTH]


__all__ = [
    "PostgresTarget",
    "atomic_backup_directory",
    "atomic_backup_file",
    "ensure_directory",
    "maintenance_child_environment",
    "parse_neo4j_database",
    "parse_postgres_url",
    "postgres_connection_args",
    "postgres_environment",
    "run_command",
    "validate_neo4j_restore_input",
    "validate_postgres_restore_input",
    "validate_restore_confirmation",
]
