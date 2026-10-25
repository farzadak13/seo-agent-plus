"""Stage 39 against a real PostgreSQL: the warehouse reads, and one atomic settlement.

The reads are new SQL over migration 004's tables, and the settlement leans on
PostgresRepository.transaction to write the outcome, the action and the
feedback together or not at all. Neither is something an in-memory double
can vouch for.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")

if not DSN:
    pytest.skip("SEO_AGENT_TEST_DSN is not set; live measurement tests are skipped.", allow_module_level=True)

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(
        f"SEO_AGENT_TEST_DSN is set but the database cannot be reached. Error: {_exc}",
        allow_module_level=True,
    )

from app.action.approval import approve  # noqa: E402
from app.action.executor import build_execute_action_handler, build_rollback_action_handler  # noqa: E402
from app.action.store import ManagedActionStore  # noqa: E402
from app.learning.store import FeedbackStore  # noqa: E402
from app.measurement.runner import OUTCOME_AGGREGATE_TYPE, MeasurementRunner  # noqa: E402
from app.models.actions import ActionStatus  # noqa: E402
from app.onboarding.site_store import SiteStore  # noqa: E402
from app.persistence.migrator import apply_migrations  # noqa: E402
from app.persistence.postgres import PostgresRepository  # noqa: E402
from app.warehouse.models import Device, GSCDay, Metrics, PageMetrics, QueryMetrics  # noqa: E402
from app.warehouse.store import PostgresWarehouse  # noqa: E402
from tests.unit.test_stage37_38_actions import (  # noqa: E402
    URL,
    FakeAdapterFactory,
    FakePageAdapter,
    make_pending,
    make_site,
)

QUERY = "کفش مردانه"
PROPERTY = "sc-domain:example.com"
ACTION_ID = "action:run-1"
APPLIED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
WAREHOUSE_TABLES = (
    "gsc_daily_queries, gsc_daily_pages, gsc_daily_totals, gsc_sync_days, gsc_queries, gsc_pages"
)


@pytest.fixture(autouse=True)
def clean_database():
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {WAREHOUSE_TABLES}, persistence_records, schema_migrations")
    apply_migrations(dsn=DSN, directory="migrations")


def store_day(warehouse, day, *, clicks, query=QUERY, url=URL, site_id="site-1"):
    """One day on two devices, so the reads have to sum them."""
    warehouse.replace_day(
        GSCDay(
            site_id=site_id,
            day=day,
            property_url=PROPERTY,
            totals=[Metrics(device=Device.MOBILE, clicks=clicks, impressions=60, position_sum=300.0)],
            pages=[
                PageMetrics(device=Device.MOBILE, url=url, clicks=clicks, impressions=60, position_sum=300.0),
                PageMetrics(device=Device.DESKTOP, url=url, clicks=0, impressions=40, position_sum=200.0),
            ],
            queries=[
                QueryMetrics(device=Device.MOBILE, url=url, query=query, clicks=clicks, impressions=60,
                             position_sum=300.0),
                QueryMetrics(device=Device.DESKTOP, url=url, query=query, clicks=0, impressions=40,
                             position_sum=200.0),
            ],
        )
    )


def days(start, end):
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def test_query_days_sums_devices_for_one_page_and_query_only():
    warehouse = PostgresWarehouse(DSN)
    day = date(2026, 7, 10)
    store_day(warehouse, day, clicks=5)
    store_day(warehouse, day + timedelta(days=1), clicks=5, query="something else")
    store_day(warehouse, day + timedelta(days=2), clicks=5, site_id="site-2")

    rows = warehouse.query_days(site_id="site-1", url=URL, query=QUERY, start=day, end=day + timedelta(days=5))

    assert rows == {day: (5, 100, 500.0)}


def test_page_days_are_the_days_the_page_had_impressions():
    warehouse = PostgresWarehouse(DSN)
    day = date(2026, 7, 10)
    store_day(warehouse, day, clicks=1)
    store_day(warehouse, day + timedelta(days=1), clicks=1, url="https://example.com/other")

    assert warehouse.page_days(site_id="site-1", url=URL, start=day, end=day + timedelta(days=3)) == {day}


def world():
    repository = PostgresRepository(DSN)
    warehouse = PostgresWarehouse(DSN)
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(make_site())
    factory = FakeAdapterFactory(FakePageAdapter())
    execute = build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    rollback = build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    actions.create(approve(make_pending(), actor="principal-1"))
    execute({"action_id": ACTION_ID})
    managed = actions.get(ACTION_ID)
    actions.update(
        managed.model_copy(update={"applied": managed.applied.model_copy(update={"applied_at": APPLIED_AT})})
    )
    for day in days(date(2026, 7, 4), date(2026, 9, 5)):
        store_day(warehouse, day, clicks=5 if day < date(2026, 8, 1) else 8)
    feedback = FeedbackStore(repository)
    runner = MeasurementRunner(
        repository=repository, action_store=actions, feedback_store=feedback, warehouse=warehouse,
        transaction=repository.transaction, clock=lambda: NOW,
    )
    return repository, actions, feedback, runner, rollback, warehouse


def test_a_change_is_settled_from_the_stored_days():
    repository, actions, feedback, runner, _, _ = world()

    assert runner.run_round().settled == 1

    stored = actions.get(ACTION_ID)
    assert stored.status == ActionStatus.SUCCEEDED  # CTR 5% -> 8%
    assert stored.history[-2].detail["baseline"]["ctr"] == 0.05
    assert stored.history[-2].detail["current"]["ctr"] == 0.08
    assert len(feedback.list_for_tenant("principal-1")) == 1
    assert repository.get(aggregate_type=OUTCOME_AGGREGATE_TYPE, aggregate_id=ACTION_ID) is not None


def test_a_failed_settlement_writes_nothing():
    repository, actions, feedback, runner, rollback, warehouse = world()
    read = warehouse.page_days

    def page_days_then_rollback(**kwargs):
        result = read(**kwargs)
        rollback({"action_id": ACTION_ID})
        return result

    warehouse.page_days = page_days_then_rollback

    assert runner.run_round().skipped == 1

    assert actions.get(ACTION_ID).status == ActionStatus.ROLLED_BACK
    assert repository.get(aggregate_type=OUTCOME_AGGREGATE_TYPE, aggregate_id=ACTION_ID) is None
    assert feedback.list_for_tenant("principal-1") == []
