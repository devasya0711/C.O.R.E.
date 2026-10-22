"""Restore a PostgreSQL custom-format backup without exposing credentials."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

try:
    from .maintenance_common import (
        PostgresTarget,
        parse_postgres_url,
        postgres_connection_args,
        postgres_environment,
        run_command,
        validate_postgres_restore_input,
        validate_restore_confirmation,
    )
except ImportError:  # pragma: no cover - direct script execution
    from maintenance_common import (
        PostgresTarget,
        parse_postgres_url,
        postgres_connection_args,
        postgres_environment,
        run_command,
        validate_postgres_restore_input,
        validate_restore_confirmation,
    )


def build_command(target: PostgresTarget, input_path: Path) -> list[str]:
    """Build a pg_restore command whose argv contains no password or URL."""

    return [
        "pg_restore",
        "--exit-on-error",
        "--clean",
        "--if-exists",
        "--no-owner",
        *postgres_connection_args(target),
        str(input_path),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Validate arguments, run pg_restore, and return a shell-friendly status."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="environment variable containing the PostgreSQL URL",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--yes-i-really-mean-it", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_restore_confirmation(args.yes_i_really_mean_it)
        raw_url = os.environ.get(args.database_url_env)
        if not raw_url:
            raise ValueError(f"{args.database_url_env} is not set")
        validate_postgres_restore_input(args.input)
        target = parse_postgres_url(raw_url)
        with postgres_environment(target) as environment:
            run_command(
                build_command(target, args.input),
                environment,
                "pg_restore",
                (target.password,) if target.password is not None else (),
            )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print("PostgreSQL restore completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
