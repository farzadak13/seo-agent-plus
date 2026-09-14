"""Migration runner rules, exercised without a database.

The behaviour against real PostgreSQL is covered by
tests/integration/test_postgres_live.py; these tests pin the rules that must
hold regardless of driver: ordering, idempotency, tamper detection, and the
fact that a failed migration is never recorded as applied.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.persistence.migrator import (
    MigrationError,
    apply_migrations,
    discover_migrations,
)


# --- a connection just real enough to drive the runner ----------------------


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self._connection.statements.append((sql.strip(), params))
        if self._connection.fail_on and self._connection.fail_on in sql:
            raise RuntimeError("syntax error at or near")
        if "INSERT INTO schema_migrations" in sql:
            self._connection.pending.append((params[0], params[2]))
        self._rows = (
            list(self._connection.recorded) if "SELECT version, checksum" in sql else []
        )

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, *, recorded=(), fail_on=None):
        self.recorded = list(recorded)
        self.pending: list[tuple[str, str]] = []
        self.statements: list[tuple[str, object]] = []
        self.autocommit = False
        self.commits = 0
        self.rollbacks = 0
        self.fail_on = fail_on

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        self.recorded.extend(self.pending)
        self.pending.clear()

    def rollback(self):
        self.rollbacks += 1
        self.pending.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def factory_for(connection):
    def connect(dsn):
        return connection

    return connect


@pytest.fixture
def migrations_dir(tmp_path):
    source = Path("migrations")
    target = tmp_path / "migrations"
    target.mkdir()
    for path in source.glob("*.sql"):
        shutil.copy(path, target)
    return target


# --- discovery ---------------------------------------------------------------


def test_migrations_are_ordered_numerically_not_alphabetically(tmp_path):
    for name in ["002_b.sql", "010_c.sql", "001_a.sql"]:
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")

    assert [item.version for item in discover_migrations(tmp_path)] == ["001", "002", "010"]


def test_a_file_without_a_leading_number_is_rejected(tmp_path):
    (tmp_path / "001_ok.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "cleanup.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationError, match="NNN_name.sql"):
        discover_migrations(tmp_path)


def test_two_migrations_with_the_same_number_are_rejected(tmp_path):
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_second.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationError, match="Duplicate migration number"):
        discover_migrations(tmp_path)


def test_an_empty_migration_is_rejected(tmp_path):
    (tmp_path / "001_empty.sql").write_text("   \n", encoding="utf-8")

    with pytest.raises(MigrationError, match="empty"):
        discover_migrations(tmp_path)


def test_a_missing_directory_is_reported_clearly(tmp_path):
    with pytest.raises(MigrationError, match="not found"):
        discover_migrations(tmp_path / "absent")


def test_an_empty_directory_is_reported_clearly(tmp_path):
    with pytest.raises(MigrationError, match="No migrations found"):
        discover_migrations(tmp_path)


def test_the_project_migrations_are_discoverable_and_ordered():
    versions = [item.version for item in discover_migrations("migrations")]

    assert versions == sorted(versions, key=int)
    assert "003" in versions


def test_the_checksum_follows_the_content(tmp_path):
    path = tmp_path / "001_a.sql"
    path.write_text("SELECT 1;", encoding="utf-8")
    first = discover_migrations(tmp_path)[0].checksum

    path.write_text("SELECT 2;", encoding="utf-8")
    second = discover_migrations(tmp_path)[0].checksum

    assert first != second


# --- applying ----------------------------------------------------------------


def test_a_fresh_database_receives_every_migration(migrations_dir):
    connection = FakeConnection()

    result = apply_migrations(
        dsn="postgresql://x",
        directory=migrations_dir,
        connection_factory=factory_for(connection),
    )

    assert result.applied == ("001", "002", "003")
    assert result.skipped == ()
    assert result.changed is True


def test_a_second_run_applies_nothing(migrations_dir):
    first = FakeConnection()
    apply_migrations(
        dsn="postgresql://x",
        directory=migrations_dir,
        connection_factory=factory_for(first),
    )

    second = FakeConnection(recorded=first.recorded)
    result = apply_migrations(
        dsn="postgresql://x",
        directory=migrations_dir,
        connection_factory=factory_for(second),
    )

    assert result.applied == ()
    assert result.skipped == ("001", "002", "003")
    assert result.changed is False
    assert second.commits == 0


def test_editing_an_applied_migration_is_refused(migrations_dir):
    connection = FakeConnection()
    apply_migrations(
        dsn="postgresql://x",
        directory=migrations_dir,
        connection_factory=factory_for(connection),
    )

    target = migrations_dir / "003_tenant_scope.sql"
    target.write_text(target.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="changed after it was applied"):
        apply_migrations(
            dsn="postgresql://x",
            directory=migrations_dir,
            connection_factory=factory_for(FakeConnection(recorded=connection.recorded)),
        )


def test_a_failing_migration_is_rolled_back_and_not_recorded(migrations_dir):
    (migrations_dir / "004_broken.sql").write_text(
        "SELECT * FROM missing_table;", encoding="utf-8"
    )
    connection = FakeConnection(fail_on="missing_table")

    with pytest.raises(MigrationError, match="rolled back"):
        apply_migrations(
            dsn="postgresql://x",
            directory=migrations_dir,
            connection_factory=factory_for(connection),
        )

    assert connection.rollbacks == 1
    assert [version for version, _ in connection.recorded] == ["001", "002", "003"]


def test_the_run_is_serialized_by_an_advisory_lock(migrations_dir):
    connection = FakeConnection()

    apply_migrations(
        dsn="postgresql://x",
        directory=migrations_dir,
        connection_factory=factory_for(connection),
    )

    statements = [sql for sql, _ in connection.statements]
    assert any("pg_advisory_lock" in sql for sql in statements)
    assert any("pg_advisory_unlock" in sql for sql in statements)


def test_the_lock_is_released_even_when_a_migration_fails(migrations_dir):
    (migrations_dir / "004_broken.sql").write_text(
        "SELECT * FROM missing_table;", encoding="utf-8"
    )
    connection = FakeConnection(fail_on="missing_table")

    with pytest.raises(MigrationError):
        apply_migrations(
            dsn="postgresql://x",
            directory=migrations_dir,
            connection_factory=factory_for(connection),
        )

    assert any("pg_advisory_unlock" in sql for sql, _ in connection.statements)


def test_the_tracking_table_is_created_before_anything_is_read(migrations_dir):
    connection = FakeConnection()

    apply_migrations(
        dsn="postgresql://x",
        directory=migrations_dir,
        connection_factory=factory_for(connection),
    )

    statements = [sql for sql, _ in connection.statements]
    create = next(i for i, sql in enumerate(statements) if "CREATE TABLE IF NOT EXISTS schema_migrations" in sql)
    read = next(i for i, sql in enumerate(statements) if "SELECT version, checksum" in sql)
    assert create < read


# --- command line ------------------------------------------------------------


def test_status_lists_migrations_without_touching_a_database(capsys):
    from app.migrate import main

    assert main(["--status"]) == 0
    assert "003" in capsys.readouterr().out


def test_the_cli_refuses_to_run_without_a_dsn(monkeypatch, capsys):
    from app.migrate import main

    monkeypatch.delenv("SEO_AGENT_DATABASE_DSN", raising=False)

    assert main([]) == 2
    assert "SEO_AGENT_DATABASE_DSN" in capsys.readouterr().err
