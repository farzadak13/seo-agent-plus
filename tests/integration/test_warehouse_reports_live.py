"""The dashboard's reads, against a real PostgreSQL: the numbers must add up exactly."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")

if not DSN:
    pytest.skip("SEO_AGENT_TEST_DSN is not set; live report tests are skipped.", allow_module_level=True)

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(f"SEO_AGENT_TEST_DSN is set but the database cannot be reached. Error: {_exc}",
                allow_module_level=True)

from app.persistence.migrator import apply_migrations  # noqa: E402
from app.warehouse.models import Device, GSCDay, Metrics, PageMetrics, QueryMetrics  # noqa: E402
from app.warehouse.reports import WarehouseReports  # noqa: E402
from app.warehouse.store import PostgresWarehouse  # noqa: E402

PROPERTY = "sc-domain:tennisino.com"
A = "https://tennisino.com/rackets"
B = "https://tennisino.com/balls"
D1, D2, D3 = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)


@pytest.fixture(autouse=True)
def data():
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP TABLE IF EXISTS gsc_daily_queries, gsc_daily_pages, gsc_daily_totals,"
                " gsc_sync_days, gsc_queries, gsc_pages, persistence_records, schema_migrations"
            )
    apply_migrations(dsn=DSN, directory="migrations")
    store = PostgresWarehouse(DSN, clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc))
    # Day 1: rackets on mobile at position 2, balls on desktop at position 10.
    store.replace_day(day(D1, [
        (Device.MOBILE, A, "راکت تنیس", 10, 100, 2.0),
        (Device.DESKTOP, B, "توپ تنیس", 1, 50, 10.0),
    ]))
    # Day 2: rackets again, worse position.
    store.replace_day(day(D2, [(Device.MOBILE, A, "راکت تنیس", 5, 100, 4.0)]))
    # Day 3: balls only.
    store.replace_day(day(D3, [(Device.MOBILE, B, "توپ تنیس", 3, 30, 1.0)]))
    return store


def day(when, rows):
    """A day whose totals, pages and queries all agree."""
    totals: dict = {}
    for device, _, _, clicks, impressions, position in rows:
        t = totals.setdefault(device, [0, 0, 0.0])
        t[0] += clicks
        t[1] += impressions
        t[2] += position * impressions
    return GSCDay(
        site_id="s1", day=when, property_url=PROPERTY,
        totals=[Metrics(device=d, clicks=c, impressions=i, position_sum=p) for d, (c, i, p) in totals.items()],
        pages=[PageMetrics(device=d, url=u, clicks=c, impressions=i, position_sum=pos * i)
               for d, u, _, c, i, pos in rows],
        queries=[QueryMetrics(device=d, url=u, query=q, clicks=c, impressions=i, position_sum=pos * i)
                 for d, u, q, c, i, pos in rows],
    )


def reports():
    return WarehouseReports(DSN)


def test_totals_recompute_ctr_and_position_from_sums_not_averages():
    report = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=D3)
    t = report.totals
    assert (t.clicks, t.impressions) == (19, 280)
    assert t.ctr == pytest.approx(19 / 280)
    # (2*100 + 10*50 + 4*100 + 1*30) / 280, not the mean of the daily averages.
    assert t.position == pytest.approx(1130 / 280)
    assert (report.days_covered, report.days_in_range) == (3, 3)


def test_the_daily_series_has_one_point_per_synced_day():
    series = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=D3).series
    assert [(p.period_start, p.clicks) for p in series] == [(D1, 11), (D2, 5), (D3, 3)]
    assert series[0].position == pytest.approx(700 / 150)


def test_weeks_group_from_monday():
    # 28 Sep 2026 is a Monday: all three days are one week.
    series = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=D3, interval="week").series
    assert [(p.period_start, p.clicks, p.days_covered) for p in series] == [(D1, 19, 3)]


def test_a_device_filter_reads_only_that_device():
    report = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=D3, device=Device.DESKTOP)
    assert (report.totals.clicks, report.totals.impressions) == (1, 50)


def test_a_day_held_for_another_property_is_left_out(data):
    # The site switched property and day 3 failed under the new one: its rows
    # are still in the tables, but they are the old property's.
    data.record_failure(site_id="s1", day=D3, property_url="https://tennisino.com/", error="403")
    old = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=D3)
    assert old.totals.clicks == 16 and old.days_covered == 2
    new = reports().performance(site_id="s1", property_url="https://tennisino.com/", start=D1, end=D3)
    assert new.totals.clicks == 0 and new.series == []


def test_pages_ranked_with_a_next_page_marker():
    first = reports().pages(site_id="s1", property_url=PROPERTY, start=D1, end=D3, limit=1)
    assert [(r.key, r.current.clicks) for r in first.rows] == [(A, 15)]
    assert first.next_offset == 1
    second = reports().pages(site_id="s1", property_url=PROPERTY, start=D1, end=D3, limit=1, offset=1)
    assert [r.key for r in second.rows] == [B] and second.next_offset is None


def test_best_position_first_when_sorting_by_position_ascending():
    rows = reports().pages(site_id="s1", property_url=PROPERTY, start=D1, end=D3,
                           sort="position", order="asc").rows
    # rackets: 600/200 = 3.0; balls: (500 + 30)/80 = 6.625
    assert [r.key for r in rows] == [A, B]
    assert rows[0].current.position == pytest.approx(3.0)


def test_compared_with_the_period_before():
    report = reports().pages(site_id="s1", property_url=PROPERTY, start=D2, end=D2, compare=True)
    assert report.compared_with == (D1, D1)
    [row] = [r for r in report.rows if r.key == A]
    assert (row.current.clicks, row.previous.clicks) == (5, 10)
    assert row.previous.position == pytest.approx(2.0)


def test_queries_of_one_page():
    rows = reports().queries(site_id="s1", property_url=PROPERTY, start=D1, end=D3, page_url=B).rows
    assert [(r.key, r.current.clicks) for r in rows] == [("توپ تنیس", 4)]


def test_another_site_reads_nothing():
    report = reports().pages(site_id="s2", property_url=PROPERTY, start=D1, end=D3)
    assert report.rows == []


def test_an_empty_range_is_empty_not_an_error():
    report = reports().performance(site_id="s1", property_url=PROPERTY,
                                   start=D3 + timedelta(days=10), end=D3 + timedelta(days=12))
    assert report.series == [] and report.totals.position is None and report.totals.ctr is None



# ---- the review's case: synced days without traffic -----------------------------


def test_a_synced_day_without_traffic_is_a_zero_not_a_gap(data):
    quiet = date(2026, 10, 1)
    data.replace_day(day(quiet, []))  # synced, nothing happened
    series = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=quiet).series
    assert [(p.period_start, p.clicks) for p in series][-1] == (quiet, 0)
    assert series[-1].position is None and series[-1].days_covered == 1


def test_a_device_filter_still_counts_every_synced_day():
    report = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=D3,
                                   device=Device.DESKTOP)
    assert [p.days_covered for p in report.series] == [1, 1, 1], "D2 and D3 had no desktop traffic"
    assert [p.clicks for p in report.series] == [1, 0, 0]
    week = reports().performance(site_id="s1", property_url=PROPERTY, start=D1, end=D3,
                                 interval="week", device=Device.DESKTOP).series
    assert week[0].days_covered == 3



def test_the_comparison_says_how_much_of_the_previous_period_is_there():
    # D2..D3 compared with D0..D1: only D1 is synced.
    report = reports().pages(site_id="s1", property_url=PROPERTY, start=D2, end=D3, compare=True)
    assert (report.previous_days_covered, report.previous_days_in_range) == (1, 2)


def test_without_comparison_there_is_no_coverage_to_report():
    report = reports().pages(site_id="s1", property_url=PROPERTY, start=D2, end=D3)
    assert report.previous_days_covered is None and report.previous_days_in_range is None
