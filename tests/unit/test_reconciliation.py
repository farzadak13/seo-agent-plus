from datetime import date

from app.ingestion.reconciliation import reconcile_ingestion
from app.models.gsc import NormalizedGSCRow
from app.models.ingestion import GSCIngestionResult
from app.models.observations import DataStatus
from app.models.reconciliation import ReconciliationStatus
from app.models.url_metrics import URLDailyMetric


def make_ingestion(
    *,
    query_impressions: list[int],
    query_clicks: list[int],
    url_impressions: list[int],
    url_clicks: list[int],
) -> GSCIngestionResult:
    rows = [
        NormalizedGSCRow(
            site_id="site-1",
            date=date(2026, 9, 1),
            normalized_url="https://example.com/page",
            normalized_query=f"query-{index}",
            impressions=impressions,
            clicks=clicks,
            avg_position=5.0,
        )
        for index, (impressions, clicks) in enumerate(
            zip(query_impressions, query_clicks)
        )
    ]

    metrics = [
        URLDailyMetric(
            site_id="site-1",
            normalized_url="https://example.com/page",
            date=date(2026, 9, 1),
            total_impressions=impressions,
            total_clicks=clicks,
        )
        for impressions, clicks in zip(
            url_impressions,
            url_clicks,
        )
    ]

    return GSCIngestionResult(
        site_id="site-1",
        response_id="response-1",
        normalized_rows=rows,
        observations=[],
        url_daily_metrics=metrics,
        daily_status=[
            (date(2026, 9, 1), DataStatus.OBSERVED),
        ],
    )


def test_match_when_totals_are_equal():
    result = reconcile_ingestion(
        make_ingestion(
            query_impressions=[100, 50],
            query_clicks=[10, 5],
            url_impressions=[150],
            url_clicks=[15],
        )
    )

    assert result.impression_status == ReconciliationStatus.MATCH
    assert result.click_status == ReconciliationStatus.MATCH
    assert result.impression_gap == 0
    assert result.click_gap == 0
    assert result.is_valid is True
    assert result.is_partial is False


def test_partial_when_query_impressions_are_lower():
    result = reconcile_ingestion(
        make_ingestion(
            query_impressions=[100],
            query_clicks=[10],
            url_impressions=[120],
            url_clicks=[12],
        )
    )

    assert result.impression_status == ReconciliationStatus.PARTIAL
    assert result.click_status == ReconciliationStatus.PARTIAL
    assert result.impression_gap == 20
    assert result.click_gap == 2
    assert result.is_valid is True
    assert result.is_partial is True


def test_invalid_when_query_impressions_exceed_url_impressions():
    result = reconcile_ingestion(
        make_ingestion(
            query_impressions=[120],
            query_clicks=[10],
            url_impressions=[100],
            url_clicks=[10],
        )
    )

    assert result.impression_status == ReconciliationStatus.INVALID
    assert result.is_valid is False


def test_invalid_when_query_clicks_exceed_url_clicks():
    result = reconcile_ingestion(
        make_ingestion(
            query_impressions=[100],
            query_clicks=[20],
            url_impressions=[100],
            url_clicks=[10],
        )
    )

    assert result.click_status == ReconciliationStatus.INVALID
    assert result.is_valid is False


def test_impressions_can_match_while_clicks_are_partial():
    result = reconcile_ingestion(
        make_ingestion(
            query_impressions=[100],
            query_clicks=[8],
            url_impressions=[100],
            url_clicks=[10],
        )
    )

    assert result.impression_status == ReconciliationStatus.MATCH
    assert result.click_status == ReconciliationStatus.PARTIAL
    assert result.is_valid is True
    assert result.is_partial is True


def test_query_level_totals_are_exposed():
    ingestion = make_ingestion(
        query_impressions=[100, 50],
        query_clicks=[10, 5],
        url_impressions=[150],
        url_clicks=[15],
    )

    assert ingestion.daily_query_impressions == 150
    assert ingestion.daily_query_clicks == 15


def test_url_level_totals_are_exposed():
    ingestion = make_ingestion(
        query_impressions=[100],
        query_clicks=[10],
        url_impressions=[120, 30],
        url_clicks=[12, 3],
    )

    assert ingestion.daily_url_impressions == 150
    assert ingestion.daily_url_clicks == 15


def test_partial_reconciliation_is_still_valid():
    result = reconcile_ingestion(
        make_ingestion(
            query_impressions=[80],
            query_clicks=[8],
            url_impressions=[100],
            url_clicks=[10],
        )
    )

    assert result.is_valid is True
    assert result.is_partial is True


def test_exact_zero_totals_match():
    result = reconcile_ingestion(
        make_ingestion(
            query_impressions=[0],
            query_clicks=[0],
            url_impressions=[0],
            url_clicks=[0],
        )
    )

    assert result.impression_status == ReconciliationStatus.MATCH
    assert result.click_status == ReconciliationStatus.MATCH
    assert result.is_valid is True