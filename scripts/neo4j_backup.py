"""Create an offline Neo4j database dump without exposing credentials."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

try:
    from .maintenance_common import (
        ensure_directory,
        parse_neo4j_database,
        run_command,
    )
except ImportError:  # pragma: no cover - direct script execution
    from maintenance_common import ensure_directory, parse_neo4j_database, run_command


def build_command(admin_executable: str, database: str, output_dir: Path) -> list[str]:
    """Build neo4j-admin argv; offline dumps do not need a password argument."""

    return [
        admin_executable,
        "database",
        "dump",
        database,
        f"--to-path={output_dir}",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Validate arguments, run neo4j-admin database dump, and exit safely."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    parser.add_argument(
        "--neo4j-admin", default=os.environ.get("NEO4J_ADMIN", "neo4j-admin")
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        database = parse_neo4j_database(args.database)
        ensure_directory(args.output_dir)
        run_command(
            build_command(args.neo4j_admin, database, args.output_dir),
            os.environ.copy(),
            "neo4j-admin dump",
        )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print("Neo4j backup completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
