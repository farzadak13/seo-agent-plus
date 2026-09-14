"""Repository and migration behaviour against a real PostgreSQL.

The other persistence tests are structural or in-memory: they cannot catch a
SQL error, a wrong index, or an ordering that only PostgreSQL decides. These
can, and they are the only tests that prove the deployed queries work.

Enable them by pointing SEO_AGENT_TEST_DSN at a throwaway database you have
actually created, substituting your own user, password and database name:

    $env:SEO_AGENT_TEST_DSN = "postgresql://USER:PASSWORD@127.0.0.1:5432/DATABASE"
    pytest -q -m integration

If the variable is unset the whole module is skipped. If it is set but the
database cannot be reached, the module is skipped with the connection error in
the reason, rather than failing every test with the same traceback.

The variable is deliberately not SEO_AGENT_DATABASE_DSN: these tests DROP the
tables they use, and must never be able to run against the real database by
inheriting its configuration.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.models.persistence import PersistenceRecord
from app.persistence.contracts import PersistenceConflictError
from app.persistence.migrator import apply_migrations
from app.persistence.postgres import PostgresRepository

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")

psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")

if not DSN:
    pytest.skip(
        "SEO_AGENT_TEST_DSN is not set; live database tests are skipped.",
        allow_module_level=True,
    )

try:
    # Probe once. Without this, an unreachable database reports the same
    # connection traceback for every test in the module and buries the cause.
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001 - any driver error means "cannot run"
    pytest.skip(
        "SEO_AGENT_TEST_DSN is set but the database cannot be reached, so live "
        f"database tests are skipped. Fix the DSN or start the server. Error: {_exc}",
        allow_module_level=True,
    )

BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def database():
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS persistence_records, schema_migrations")
    apply_migrations(dsn=DSN, directory="migrations")
    return PostgresRepository(DSN)


def record(aggregate_id, *, tenant, aggregate_type="seo_run", site=None, version=1, minutes=0):
    return PersistenceRecord(
        record_id=f"{aggregate_type}:{aggregate_id}:v{version}",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        version=version,
        payload={"n": version, "text": "کفش مردانه"},
        created_at=BASE + timedelta(minutes=minutes),
        tenant_id=tenant,
        site_id=site,
    )


@pytest.fixture
def populated(database):
    for index in range(5):
        database.create(record(f"a-{index}", tenant="tenant-a", site="site-a", minutes=10 + index))
    for index in range(3):
        database.create(record(f"b-{index}", tenant="tenant-b", site="site-b", minutes=10 + index))
    return database


# --- migrations -------------------------------------------------------------


def test_migrations_apply_to_an_empty_database(database):
    result = apply_migrations(dsn=DSN, directory="migrations")

    assert result.applied == ()
    assert result.skipped


def test_the_owner_columns_exist_after_migrating(database):
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'persistence_records'
                """
            )
            columns = {row[0] for row in cursor.fetchall()}

    assert {"tenant_id", "site_id"} <= columns


def test_the_tenant_index_exists(database):
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'persistence_records'"
            )
            indexes = {row[0] for row in cursor.fetchall()}

    assert "idx_persistence_records_tenant" in indexes


def test_the_backfill_lifts_the_owner_out_of_the_payload():
    """A database written before the owner columns existed must be scoped after migrating."""
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS persistence_records, schema_migrations")
    apply_migrations(dsn=DSN, directory="migrations")

    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE persistence_records DROP COLUMN tenant_id")
            cursor.execute("ALTER TABLE persistence_records DROP COLUMN site_id")
            cursor.execute(
                """
                INSERT INTO persistence_records
                    (record_id, aggregate_type, aggregate_id, version, schema_version, payload, created_at)
                VALUES ('site:old', 'site', 'old', 1, 1,
                        '{"site_id":"old","principal_id":"tenant-legacy"}'::jsonb, now())
                """
            )
            cursor.execute("DELETE FROM schema_migrations WHERE version = '003'")

    apply_migrations(dsn=DSN, directory="migrations")
    repository = PostgresRepository(DSN)
    restored = repository.get(aggregate_type="site", aggregate_id="old")

    assert restored.tenant_id == "tenant-legacy"
    assert restored.site_id == "old"


# --- tenant scoping ---------------------------------------------------------


def test_a_scoped_query_never_returns_another_tenants_records(populated):
    page = populated.query(tenant_id="tenant-a", aggregate_type="seo_run")

    assert len(page.records) == 5
    assert {item.tenant_id for item in page.records} == {"tenant-a"}


def test_a_site_filter_cannot_reach_across_tenants(populated):
    assert populated.query(tenant_id="tenant-a", site_id="site-b").records == []


def test_results_come_back_newest_first(populated):
    page = populated.query(tenant_id="tenant-a", aggregate_type="seo_run")

    assert [item.aggregate_id for item in page.records] == ["a-4", "a-3", "a-2", "a-1", "a-0"]


def test_paging_visits_every_record_exactly_once(populated):
    expected = [
        item.aggregate_id
        for item in populated.query(tenant_id="tenant-a", aggregate_type="seo_run").records
    ]

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = populated.query(
            tenant_id="tenant-a", aggregate_type="seo_run", limit=2, cursor=cursor
        )
        seen.extend(item.aggregate_id for item in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == expected
    assert cursor is None


def test_a_query_returns_the_latest_version_only(populated):
    populated.replace(
        record("a-0", tenant="tenant-a", site="site-a", version=2, minutes=99),
        expected_version=1,
    )

    page = populated.query(tenant_id="tenant-a", aggregate_type="seo_run")

    assert len(page.records) == 5
    assert page.records[0].aggregate_id == "a-0"
    assert page.records[0].version == 2


def test_an_aggregate_can_never_change_tenant(populated):
    with pytest.raises(PersistenceConflictError, match="immutable"):
        populated.replace(
            record("a-1", tenant="tenant-b", site="site-a", version=2), expected_version=1
        )

    current = populated.get(aggregate_type="seo_run", aggregate_id="a-1")
    assert current.tenant_id == "tenant-a"
    assert current.version == 1


def test_the_scoped_query_uses_the_tenant_index(database):
    """A sequential scan here would defeat the whole point of the migration.

    The table is seeded first: on a handful of rows a sequential scan really is
    cheaper and the planner is right to choose one, so a small table would make
    this assertion meaningless.
    """
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO persistence_records
                    (record_id, aggregate_type, aggregate_id, version, schema_version,
                     payload, created_at, tenant_id, site_id)
                SELECT 'seed:' || g, 'seo_run', 'seed-' || g, 1, 1, '{}'::jsonb,
                       timestamptz '2026-09-01 12:00+00' + (g || ' seconds')::interval,
                       'tenant-' || (g % 200), 'site-' || (g % 20)
                FROM generate_series(1, 20000) g
                """
            )
            cursor.execute("ANALYZE persistence_records")
            cursor.execute(
                """
                EXPLAIN SELECT * FROM (
                    SELECT DISTINCT ON (aggregate_type, aggregate_id) record_id, created_at, aggregate_id
                    FROM persistence_records
                    WHERE tenant_id = 'tenant-7' AND aggregate_type = 'seo_run'
                    ORDER BY aggregate_type, aggregate_id, version DESC
                ) AS latest
                ORDER BY latest.created_at DESC, latest.aggregate_id DESC
                LIMIT 51
                """
            )
            plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "idx_persistence_records_tenant" in plan, plan
    assert "Seq Scan on persistence_records" not in plan, plan


# --- append-only guarantees -------------------------------------------------


def test_a_duplicate_create_is_a_conflict(database):
    database.create(record("only-once", tenant="tenant-a"))

    with pytest.raises(PersistenceConflictError):
        database.create(record("only-once", tenant="tenant-a"))


def test_a_stale_expected_version_is_a_conflict(database):
    database.create(record("versioned", tenant="tenant-a"))
    database.replace(record("versioned", tenant="tenant-a", version=2), expected_version=1)

    with pytest.raises(PersistenceConflictError):
        database.replace(record("versioned", tenant="tenant-a", version=2), expected_version=1)


def test_earlier_versions_survive_a_replace(database):
    database.create(record("kept", tenant="tenant-a"))
    database.replace(record("kept", tenant="tenant-a", version=2), expected_version=1)

    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT version FROM persistence_records WHERE aggregate_id = 'kept' ORDER BY version"
            )
            versions = [row[0] for row in cursor.fetchall()]

    assert versions == [1, 2]


def test_persian_text_round_trips_through_jsonb(database):
    database.create(record("persian", tenant="tenant-a"))

    restored = database.get(aggregate_type="seo_run", aggregate_id="persian")

    assert restored.payload["text"] == "کفش مردانه"


def test_ping_succeeds_against_a_live_database(database):
    database.ping()
