"""Restore an offline Neo4j database dump without exposing credentials."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

try:
    from .maintenance_common import (
        maintenance_child_environment,
        parse_neo4j_database,
        run_command,
        validate_neo4j_restore_input,
        validate_restore_confirmation,
    )
except ImportError:  # pragma: no cover - direct script execution
    from maintenance_common import (
        maintenance_child_environment,
        parse_neo4j_database,
        run_command,
        validate_neo4j_restore_input,
        validate_restore_confirmation,
    )


def build_command(admin_executable: str, database: str, input_dir: Path) -> list[str]:
    """Build neo4j-admin argv without any authentication or shell expansion."""

    return [
        admin_executable,
        "database",
        "load",
        database,
        f"--from-path={input_dir}",
        "--overwrite-destination=true",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Validate arguments, run neo4j-admin database load, and exit safely."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    parser.add_argument(
        "--neo4j-admin", default=os.environ.get("NEO4J_ADMIN", "neo4j-admin")
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--yes-i-really-mean-it", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_restore_confirmation(args.yes_i_really_mean_it)
        database = parse_neo4j_database(args.database)
        validate_neo4j_restore_input(args.input_dir)
        run_command(
            build_command(args.neo4j_admin, database, args.input_dir),
            maintenance_child_environment(),
            "neo4j-admin load",
        )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print("Neo4j restore completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
