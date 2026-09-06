from datetime import date

from app.features.url_metrics import aggregate_url_daily_metrics
from app.models.gsc import NormalizedGSCRow


def make_row(
    *,
    url: str,
    query: str,
    day: int,
    impressions: int,
    clicks: int,
) -> NormalizedGSCRow:
    return NormalizedGSCRow(
        site_id="site-1",
        date=date(2026, 9, day),
        normalized_url=url,
        normalized_query=query,
        impressions=impressions,
        clicks=clicks,
        avg_position=5.0,
    )


def test_aggregates_multiple_queries_for_same_url_and_date():
    rows = [
        make_row(
            url="https://example.com/page",
            query="کفش مردانه",
            day=1,
            impressions=100,
            clicks=10,
        ),
        make_row(
            url="https://example.com/page",
            query="کفش ورزشی",
            day=1,
            impressions=50,
            clicks=5,
        ),
    ]

    result = aggregate_url_daily_metrics(rows)

    assert len(result) == 1
    assert result[0].site_id == "site-1"
    assert result[0].normalized_url == "https://example.com/page"
    assert result[0].date == date(2026, 9, 1)
    assert result[0].total_impressions == 150
    assert result[0].total_clicks == 15


def test_keeps_different_urls_separate():
    rows = [
        make_row(
            url="https://example.com/page-1",
            query="کفش",
            day=1,
            impressions=100,
            clicks=10,
        ),
        make_row(
            url="https://example.com/page-2",
            query="کفش",
            day=1,
            impressions=200,
            clicks=20,
        ),
    ]

    result = aggregate_url_daily_metrics(rows)

    assert len(result) == 2

    assert result[0].normalized_url == "https://example.com/page-1"
    assert result[0].total_impressions == 100

    assert result[1].normalized_url == "https://example.com/page-2"
    assert result[1].total_impressions == 200


def test_keeps_different_dates_separate():
    rows = [
        make_row(
            url="https://example.com/page",
            query="کفش",
            day=1,
            impressions=100,
            clicks=10,
        ),
        make_row(
            url="https://example.com/page",
            query="کفش",
            day=2,
            impressions=200,
            clicks=20,
        ),
    ]

    result = aggregate_url_daily_metrics(rows)

    assert len(result) == 2

    assert result[0].date == date(2026, 9, 1)
    assert result[0].total_impressions == 100

    assert result[1].date == date(2026, 9, 2)
    assert result[1].total_impressions == 200


def test_keeps_sites_separate():
    rows = [
        NormalizedGSCRow(
            site_id="site-1",
            date=date(2026, 9, 1),
            normalized_url="https://example.com/page",
            normalized_query="کفش",
            impressions=100,
            clicks=10,
            avg_position=5.0,
        ),
        NormalizedGSCRow(
            site_id="site-2",
            date=date(2026, 9, 1),
            normalized_url="https://example.com/page",
            normalized_query="کفش",
            impressions=200,
            clicks=20,
            avg_position=5.0,
        ),
    ]

    result = aggregate_url_daily_metrics(rows)

    assert len(result) == 2
    assert result[0].site_id == "site-1"
    assert result[1].site_id == "site-2"


def test_empty_input_returns_empty_list():
    result = aggregate_url_daily_metrics([])

    assert result == []


def test_results_are_sorted():
    rows = [
        make_row(
            url="https://example.com/b",
            query="کفش",
            day=3,
            impressions=100,
            clicks=10,
        ),
        make_row(
            url="https://example.com/a",
            query="کفش",
            day=1,
            impressions=100,
            clicks=10,
        ),
        make_row(
            url="https://example.com/a",
            query="کفش",
            day=2,
            impressions=100,
            clicks=10,
        ),
    ]

    result = aggregate_url_daily_metrics(rows)

    assert [
        (metric.normalized_url, metric.date)
        for metric in result
    ] == [
        ("https://example.com/a", date(2026, 9, 1)),
        ("https://example.com/a", date(2026, 9, 2)),
        ("https://example.com/b", date(2026, 9, 3)),
    ]


def test_does_not_create_metrics_for_missing_url_date():
    rows = [
        make_row(
            url="https://example.com/page",
            query="کفش",
            day=1,
            impressions=100,
            clicks=10,
        ),
        make_row(
            url="https://example.com/page",
            query="کفش",
            day=3,
            impressions=100,
            clicks=10,
        ),
    ]

    result = aggregate_url_daily_metrics(rows)

    assert len(result) == 2
    assert [metric.date for metric in result] == [
        date(2026, 9, 1),
        date(2026, 9, 3),
    ]


def test_preserves_zero_metrics_when_explicitly_present():
    rows = [
        make_row(
            url="https://example.com/page",
            query="کفش",
            day=1,
            impressions=0,
            clicks=0,
        ),
    ]

    result = aggregate_url_daily_metrics(rows)

    assert len(result) == 1
    assert result[0].total_impressions == 0
    assert result[0].total_clicks == 0
