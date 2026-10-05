"""The Search Console warehouse against a real PostgreSQL (migration 004).

The in-memory doubles have hidden a schema constraint in this project once
before. These run the store's SQL as it runs in production: a day replaced
whole, a failed refetch leaving the good copy alone, the sync ledger, and
the fetch -> store path end to end.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")

if not DSN:
    pytest.skip("SEO_AGENT_TEST_DSN is not set; live warehouse tests are skipped.", allow_module_level=True)

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(
        f"SEO_AGENT_TEST_DSN is set but the database cannot be reached. Error: {_exc}",
        allow_module_level=True,
    )

from app.persistence.migrator import apply_migrations  # noqa: E402
from app.warehouse.fetch import PAGE_DIMENSIONS, QUERY_DIMENSIONS, TOTALS_DIMENSIONS  # noqa: E402
from app.warehouse.models import Device, GSCDay, Metrics, PageMetrics, QueryMetrics  # noqa: E402
from app.warehouse.store import PostgresWarehouse  # noqa: E402
from app.warehouse.sync import DaySync  # noqa: E402

DAY = date(2026, 9, 30)
PROPERTY = "sc-domain:tennisino.com"
WAREHOUSE_TABLES = (
    "gsc_daily_queries, gsc_daily_pages, gsc_daily_totals, gsc_sync_days, gsc_queries, gsc_pages"
)


@pytest.fixture(autouse=True)
def clean_database():
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {WAREHOUSE_TABLES}, persistence_records, schema_migrations")
    apply_migrations(dsn=DSN, directory="migrations")


def warehouse():
    return PostgresWarehouse(DSN, clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc))


def a_day(clicks, *, site_id="s1", day=DAY, query="راکت تنیس"):
    return GSCDay(
        site_id=site_id,
        day=day,
        property_url=PROPERTY,
        totals=[Metrics(device=Device.MOBILE, clicks=clicks, impressions=100, position_sum=350.0)],
        pages=[PageMetrics(device=Device.MOBILE, url="https://tennisino.com/a", clicks=clicks,
                           impressions=100, position_sum=350.0)],
        queries=[QueryMetrics(device=Device.MOBILE, url="https://tennisino.com/a", query=query,
                              clicks=clicks, impressions=100, position_sum=350.0)],
    )


def count(table, site_id="s1"):
    with psycopg.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE site_id = %s", (site_id,))  # noqa: S608
            return cursor.fetchone()[0]


def test_a_refetched_day_replaces_the_old_one_and_never_doubles_it():
    store = warehouse()
    store.replace_day(a_day(10))
    store.replace_day(a_day(12))

    assert store.day_totals(site_id="s1", day=DAY) == {"MOBILE": (12, 100, 350.0)}
    assert count("gsc_daily_pages") == 1
    assert count("gsc_daily_queries") == 1
    assert count("gsc_pages") == 1 and count("gsc_queries") == 1, "lookup rows reused, not repeated"


def test_a_failed_refetch_leaves_the_good_copy_and_its_status():
    store = warehouse()
    store.replace_day(a_day(10))
    store.record_failure(site_id="s1", day=DAY, property_url=PROPERTY, error="boom")

    assert store.sync_status(site_id="s1", day=DAY)["status"] == "synced"
    assert store.day_totals(site_id="s1", day=DAY) == {"MOBILE": (10, 100, 350.0)}


def test_a_day_never_stored_records_its_failure():
    store = warehouse()
    store.record_failure(site_id="s1", day=DAY, property_url=PROPERTY, error="x" * 900)
    status = store.sync_status(site_id="s1", day=DAY)
    assert status["status"] == "failed"
    assert len(status["error"]) == 500, "bounded"
    assert store.synced_days(site_id="s1", property_url=PROPERTY, start=DAY, end=DAY) == set()


def test_synced_days_belong_to_the_property_they_came_from():
    store = warehouse()
    store.replace_day(a_day(10))
    assert store.synced_days(site_id="s1", property_url=PROPERTY, start=DAY, end=DAY) == {DAY}
    # The site switched property: its history must be fetched again.
    assert store.synced_days(site_id="s1", property_url="https://tennisino.com/", start=DAY, end=DAY) == set()


def test_sites_do_not_share_rows():
    store = warehouse()
    store.replace_day(a_day(10, site_id="s1"))
    store.replace_day(a_day(99, site_id="s2"))
    assert store.day_totals(site_id="s1", day=DAY) == {"MOBILE": (10, 100, 350.0)}
    assert store.day_totals(site_id="s2", day=DAY) == {"MOBILE": (99, 100, 350.0)}


def test_persian_text_round_trips_exactly():
    store = warehouse()
    store.replace_day(a_day(1, query="کفش‌مردانه"))
    with psycopg.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT query FROM gsc_queries WHERE site_id = 's1'")
            assert cursor.fetchone()[0] == "کفش‌مردانه", "ZWNJ kept"


def test_fetch_then_store_end_to_end():
    def search_console(dimensions, day):
        d = day.isoformat()
        return {
            TOTALS_DIMENSIONS: [{"keys": [d, "DESKTOP"], "clicks": 5, "impressions": 50, "position": 2.0}],
            PAGE_DIMENSIONS: [{"keys": [d, "https://tennisino.com/a", "DESKTOP"],
                               "clicks": 5, "impressions": 50, "position": 2.0}],
            QUERY_DIMENSIONS: [{"keys": [d, "https://tennisino.com/a", "راكت", "DESKTOP"],
                                "clicks": 4, "impressions": 40, "position": 2.5}],
        }[dimensions]

    result = DaySync(warehouse()).sync_day(site_id="s1", property_url=PROPERTY, day=DAY, query=search_console)

    assert result["query_rows"] == 1
    assert warehouse().day_totals(site_id="s1", day=DAY) == {"DESKTOP": (5, 50, 100.0)}
