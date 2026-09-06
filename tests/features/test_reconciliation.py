from app.features.reconciliation import calculate_reconciliation_completeness
from app.models.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)


def test_partial_reconciliation_completeness_is_half():
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