"""Command-line migration entry point.

    python -m app.migrate            # apply pending migrations
    python -m app.migrate --status   # report without changing anything

Reads SEO_AGENT_DATABASE_DSN so a deployment never has to repeat the DSN.
"""
from __future__ import annotations

import argparse
import os
import sys

from app.persistence.migrator import (
    MigrationError,
    apply_migrations,
    discover_migrations,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply SQL migrations.")
    parser.add_argument(
        "--directory",
        default="migrations",
        help="Directory holding NNN_name.sql files (default: migrations)",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Database DSN. Defaults to SEO_AGENT_DATABASE_DSN.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="List the migrations on disk without applying anything.",
    )
    args = parser.parse_args(argv)

    if args.status:
        for migration in discover_migrations(args.directory):
            print(f"{migration.version}  {migration.name}  {migration.checksum[:12]}")
        return 0

    dsn = args.dsn or os.getenv("SEO_AGENT_DATABASE_DSN")
    if dsn is None or not dsn.strip():
        print(
            "SEO_AGENT_DATABASE_DSN is required (or pass --dsn).",
            file=sys.stderr,
        )
        return 2

    try:
        result = apply_migrations(dsn=dsn, directory=args.directory)
    except MigrationError as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    for version in result.applied:
        print(f"applied  {version}")
    for version in result.skipped:
        print(f"already  {version}")
    if not result.changed:
        print("Database is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
