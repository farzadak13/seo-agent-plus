from datetime import date

from app.ingestion.integrity import check_ingestion_integrity
from app.models.gsc import NormalizedGSCRow
from app.models.url_metrics import URLDailyMetric


def make_row(
    *,
    query: str,
    impressions: int,
    clicks: int,
) -> NormalizedGSCRow:
    return NormalizedGSCRow(
        site_id="site-1",
        date=date(2026, 9, 1),
        normalized_url="https://example.com/page",
        normalized_query=query,
        impressions=impressions,
        clicks=clicks,
        avg_position=5.0,
    )


def make_metric(
    *,
    impressions: int,
    clicks: int,
) -> URLDailyMetric:
    return URLDailyMetric(
        site_id="site-1",
        normalized_url="https://example.com/page",
        date=date(2026, 9, 1),
        total_impressions=impressions,
        total_clicks=clicks,
    )


def test_unique_response_is_detected():
    result = check_ingestion_integrity(
        rows=[],
        url_daily_metrics=[],
        known_response_ids={"response-1"},
        response_id="response-2",
    )

    assert result.response_is_unique is True


def test_duplicate_response_is_detected():
    result = check_ingestion_integrity(
        rows=[],
        url_daily_metrics=[],
        known_response_ids={"response-1"},
        response_id="response-1",
    )

    assert result.response_is_unique is False


def test_duplicate_rows_are_counted():
    row = make_row(
        query="کفش",
        impressions=100,
        clicks=10,
    )

    result = check_ingestion_integrity(
        rows=[row, row],
        url_daily_metrics=[
            make_metric(
                impressions=200,
                clicks=20,
            )
        ],
    )

    assert result.duplicate_row_count == 1
    assert result.unique_row_count == 1


def test_unique_rows_are_counted():
    result = check_ingestion_integrity(
        rows=[
            make_row(
                query="کفش",
                impressions=100,
                clicks=10,
            ),
            make_row(
                query="کفش ورزشی",
                impressions=50,
                clicks=5,
            ),
        ],
        url_daily_metrics=[
            make_metric(
                impressions=150,
                clicks=15,
            )
        ],
    )

    assert result.duplicate_row_count == 0
    assert result.unique_row_count == 2


def test_query_totals_are_calculated():
    result = check_ingestion_integrity(
        rows=[
            make_row(
                query="کفش",
                impressions=100,
                clicks=10,
            ),
            make_row(
                query="کفش ورزشی",
                impressions=50,
                clicks=5,
            ),
        ],
        url_daily_metrics=[
            make_metric(
                impressions=150,
                clicks=15,
            )
        ],
    )

    assert result.query_impressions == 150
    assert result.query_clicks == 15


def test_url_totals_are_calculated():
    result = check_ingestion_integrity(
        rows=[
            make_row(
                query="کفش",
                impressions=100,
                clicks=10,
            ),
        ],
        url_daily_metrics=[
            make_metric(
                impressions=120,
                clicks=12,
            )
        ],
    )

    assert result.url_impressions == 120
    assert result.url_clicks == 12


def test_query_and_url_totals_can_have_a_gap():
    result = check_ingestion_integrity(
        rows=[
            make_row(
                query="کفش",
                impressions=100,
                clicks=10,
            ),
        ],
        url_daily_metrics=[
            make_metric(
                impressions=120,
                clicks=12,
            )
        ],
    )

    assert result.query_impression_gap == 20
    assert result.query_click_gap == 2


def test_no_gap_when_totals_match():
    result = check_ingestion_integrity(
        rows=[
            make_row(
                query="کفش",
                impressions=100,
                clicks=10,
            ),
            make_row(
                query="کفش ورزشی",
                impressions=50,
                clicks=5,
            ),
        ],
        url_daily_metrics=[
            make_metric(
                impressions=150,
                clicks=15,
            )
        ],
    )

    assert result.query_impression_gap == 0
    assert result.query_click_gap == 0
def test_different_queries_are_not_duplicates():
    rows = [
        make_row(
            query="کفش",
            impressions=100,
            clicks=10,
        ),
        make_row(
            query="کفش مردانه",
            impressions=100,
            clicks=10,
        ),
    ]

    result = check_ingestion_integrity(
        rows=rows,
        url_daily_metrics=[
            make_metric(
                impressions=200,
                clicks=20,
            )
        ],
    )

    assert result.duplicate_row_count == 0
    assert result.unique_row_count == 2