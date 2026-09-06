from datetime import date

from app.features.reconciliation import (
    calculate_reconciliation_completeness,
)
from app.ingestion.reconciliation import reconcile_ingestion
from app.models.gsc import NormalizedGSCRow
from app.models.ingestion import GSCIngestionResult
from app.models.observations import DataStatus
from app.models.url_metrics import URLDailyMetric
from app.features.reconciliation import calculate_reconciliation_completeness
from app.models.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)


def make_ingestion(
    *,
    query_impressions: int,
    query_clicks: int,
    url_impressions: int,
    url_clicks: int,
) -> GSCIngestionResult:
    return GSCIngestionResult(
        site_id="site-1",
        response_id="response-1",
        normalized_rows=[
            NormalizedGSCRow(
                site_id="site-1",
                date=date(2026, 9, 1),
                normalized_url="https://example.com/page",
                normalized_query="کفش",
                impressions=query_impressions,
                clicks=query_clicks,
                avg_position=5.0,
            )
        ],
        observations=[],
        url_daily_metrics=[
            URLDailyMetric(
                site_id="site-1",
                normalized_url="https://example.com/page",
                date=date(2026, 9, 1),
                total_impressions=url_impressions,
                total_clicks=url_clicks,
            )
        ],
        daily_status=[
            (date(2026, 9, 1), DataStatus.OBSERVED),
        ],
    )


def test_match_has_full_reconciliation_completeness():
    ingestion = make_ingestion(
        query_impressions=100,
        query_clicks=10,
        url_impressions=100,
        url_clicks=10,
    )

    result = reconcile_ingestion(ingestion)

    assert calculate_reconciliation_completeness(result) == 1.0


def test_partial_on_both_metrics_has_half_completeness():
    ingestion = make_ingestion(
        query_impressions=80,
        query_clicks=8,
        url_impressions=100,
        url_clicks=10,
    )

    result = reconcile_ingestion(ingestion)

    assert calculate_reconciliation_completeness(result) == 0.5


def test_invalid_on_both_metrics_has_zero_completeness():
    ingestion = make_ingestion(
        query_impressions=120,
        query_clicks=20,
        url_impressions=100,
        url_clicks=10,
    )

    result = reconcile_ingestion(ingestion)

    assert calculate_reconciliation_completeness(result) == 0.0


def test_matched_impressions_partial_clicks():
    ingestion = make_ingestion(
        query_impressions=100,
        query_clicks=8,
        url_impressions=100,
        url_clicks=10,
    )

    result = reconcile_ingestion(ingestion)

    assert calculate_reconciliation_completeness(result) == 0.75


def test_partial_impressions_matched_clicks():
    ingestion = make_ingestion(
        query_impressions=80,
        query_clicks=10,
        url_impressions=100,
        url_clicks=10,
    )

    result = reconcile_ingestion(ingestion)

    assert calculate_reconciliation_completeness(result) == 0.75


def test_score_is_always_between_zero_and_one():
    cases = [
        (100, 10, 100, 10),
        (80, 8, 100, 10),
        (120, 20, 100, 10),
    ]

    for query_impressions, query_clicks, url_impressions, url_clicks in cases:
        ingestion = make_ingestion(
            query_impressions=query_impressions,
            query_clicks=query_clicks,
            url_impressions=url_impressions,
            url_clicks=url_clicks,
        )

        result = reconcile_ingestion(ingestion)
        score = calculate_reconciliation_completeness(result)

        assert 0.0 <= score <= 1.0
def test_partial_reconciliation_completeness_is_averaged_by_metric():
    result = ReconciliationResult(
        impression_status=ReconciliationStatus.PARTIAL,
        click_status=ReconciliationStatus.MATCH,
        query_impressions=100,
        url_impressions=110,
        query_clicks=10,
        url_clicks=10,
        impression_gap=10,
        click_gap=0,
        is_valid=True,
        is_partial=True,
    )

    assert calculate_reconciliation_completeness(result) == 0.75