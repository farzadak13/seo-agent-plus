"""Forward-only SQL migration runner.

Three properties matter more than features here:

* **Recorded.** Every applied file is written to ``schema_migrations`` with a
  checksum, so "which migrations does this database have" is a question the
  database can answer instead of a thing someone remembers.
* **Serialized.** A session advisory lock means two instances starting at once
  cannot apply the same file twice.
* **Tamper-evident.** Editing a file that was already applied is refused. A
  migration that changed after deployment means two databases silently differ,
  which is far worse than a failed start.

Each file runs in its own transaction: a failure leaves earlier migrations
applied and recorded, and the failing one applied not at all.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import psycopg
except ImportError as exc:  # pragma: no cover
    psycopg = None
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None


MIGRATION_LOCK_ID = 733034
FILENAME_PATTERN = re.compile(r"^(\d+)_(.+)\.sql$")

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Raised when migrations cannot be applied safely."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()

    @property
    def sort_key(self) -> int:
        return int(self.version)


@dataclass(frozen=True)
class MigrationResult:
    applied: tuple[str, ...]
    skipped: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def discover_migrations(directory: str | Path) -> list[Migration]:
    """Load every NNN_name.sql file, ordered numerically, rejecting ambiguity."""
    root = Path(directory)
    if not root.is_dir():
        raise MigrationError(f"Migration directory not found: {root}")

    migrations: list[Migration] = []
    seen: dict[int, str] = {}

    for path in sorted(root.glob("*.sql")):
        match = FILENAME_PATTERN.match(path.name)
        if match is None:
            raise MigrationError(
                f"Migration file name must be NNN_name.sql: {path.name}"
            )
        version, name = match.group(1), match.group(2)
        number = int(version)
        if number in seen:
            raise MigrationError(
                f"Duplicate migration number {number}: {seen[number]} and {path.name}"
            )
        seen[number] = path.name
        sql = path.read_text(encoding="utf-8-sig")
        if not sql.strip():
            raise MigrationError(f"Migration file is empty: {path.name}")
        migrations.append(Migration(version=version, name=name, path=path, sql=sql))

    if not migrations:
        raise MigrationError(f"No migrations found in {root}")

    migrations.sort(key=lambda item: item.sort_key)
    return migrations


def apply_migrations(
    *,
    dsn: str,
    directory: str | Path = "migrations",
    connection_factory=None,
) -> MigrationResult:
    """Apply every migration the database has not recorded yet."""
    if psycopg is None and connection_factory is None:  # pragma: no cover
        raise MigrationError("psycopg is required to apply migrations") from _PSYCOPG_IMPORT_ERROR

    migrations = discover_migrations(directory)
    connect = connection_factory or psycopg.connect

    applied: list[str] = []
    skipped: list[str] = []

    with connect(dsn) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TRACKING_TABLE)
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
        try:
            recorded = _recorded_migrations(connection)
            for migration in migrations:
                previous = recorded.get(migration.version)
                if previous is not None:
                    if previous != migration.checksum:
                        raise MigrationError(
                            "Migration file changed after it was applied: "
                            f"{migration.path.name}. Add a new migration instead "
                            "of editing an applied one."
                        )
                    skipped.append(migration.version)
                    continue
                _apply_one(connection, migration)
                applied.append(migration.version)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))

    return MigrationResult(applied=tuple(applied), skipped=tuple(skipped))


def _recorded_migrations(connection) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT version, checksum FROM schema_migrations")
        rows = cursor.fetchall()
    recorded: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict):
            recorded[row["version"]] = row["checksum"]
        else:
            recorded[row[0]] = row[1]
    return recorded


def _apply_one(connection, migration: Migration) -> None:
    """Apply one file and record it in the same transaction."""
    connection.autocommit = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(migration.sql)
            cursor.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum),
            )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        raise MigrationError(
            f"Migration failed and was rolled back: {migration.path.name}"
        ) from exc
    finally:
        connection.autocommit = True


__all__ = [
    "Migration",
    "MigrationError",
    "MigrationResult",
    "apply_migrations",
    "discover_migrations",
]
