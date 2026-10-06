"""The SQL inside the deploy scripts, run where it will run: on PostgreSQL.

A query in a shell script is invisible to every other test. The restore test's
count once grouped by the position of an expression containing count(*), which
PostgreSQL refuses, and the first anyone knew was the restore test failing on
the server.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")

if not DSN:
    pytest.skip("SEO_AGENT_TEST_DSN is not set; live deploy-SQL tests are skipped.", allow_module_level=True)

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(f"SEO_AGENT_TEST_DSN is set but the database cannot be reached. Error: {_exc}",
                allow_module_level=True)

from app.models.persistence import PersistenceRecord  # noqa: E402
from app.persistence.migrator import apply_migrations  # noqa: E402
from app.persistence.postgres import PostgresRepository  # noqa: E402

BACKUP_SETUP = Path(__file__).resolve().parents[2] / "deploy" / "backup-setup.sh"


def script_sql(name: str) -> str:
    found = re.search(rf'^{name}="(?P<sql>[^"]+)"$', BACKUP_SETUP.read_text(encoding="utf-8"), re.MULTILINE)
    assert found, f"{name} not found in {BACKUP_SETUP.name}"
    return found.group("sql")


@pytest.fixture(autouse=True)
def clean_database():
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS persistence_records, schema_migrations CASCADE")
    apply_migrations(dsn=DSN, directory="migrations")


def test_the_restore_tests_count_runs_and_counts_each_kind():
    repository = PostgresRepository(DSN)
    for kind, count in (("account", 2), ("site", 1)):
        for n in range(count):
            repository.create(PersistenceRecord(
                record_id=f"{kind}:{n}:v1", aggregate_type=kind, aggregate_id=str(n), version=1, payload={},
            ))

    with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(script_sql("COUNT_SQL"))
        lines = [row[0] for row in cursor.fetchall()]

    assert lines == ["account 2", "site 1"]
