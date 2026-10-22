"""Create a PostgreSQL custom-format backup without exposing credentials."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

try:
    from .maintenance_common import (
        PostgresTarget,
        atomic_backup_file,
        parse_postgres_url,
        postgres_connection_args,
        postgres_environment,
        run_command,
    )
except ImportError:  # pragma: no cover - direct script execution
    from maintenance_common import (
        PostgresTarget,
        atomic_backup_file,
        parse_postgres_url,
        postgres_connection_args,
        postgres_environment,
        run_command,
    )


def build_command(target: PostgresTarget, output: Path) -> list[str]:
    """Build a pg_dump command whose argv contains no password or URL."""

    return [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        *postgres_connection_args(target),
        "--file",
        str(output),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Validate arguments, run pg_dump, and return a shell-friendly status."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="environment variable containing the PostgreSQL URL",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw_url = os.environ.get(args.database_url_env)
        if not raw_url:
            raise ValueError(f"{args.database_url_env} is not set")
        target = parse_postgres_url(raw_url)
        with (
            atomic_backup_file(args.output, args.force) as temporary_output,
            postgres_environment(target) as environment,
        ):
            run_command(
                build_command(target, temporary_output),
                environment,
                "pg_dump",
                (target.password,) if target.password is not None else (),
            )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print("PostgreSQL backup completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
