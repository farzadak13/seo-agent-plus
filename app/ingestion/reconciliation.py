from collections.abc import Sequence

from app.models.ingestion import GSCIngestionResult
from app.models.reconciliation import (
    ReconciliationInput,
    ReconciliationResult,
    ReconciliationStatus,
)
from app.models.url_metrics import URLDailyMetric


def _classify(
    query_total: int,
    url_total: int,
) -> tuple[ReconciliationStatus, int]:
    """
    Classify query-level totals against URL-level totals.
    """

    if query_total == url_total:
        return ReconciliationStatus.MATCH, 0

    if query_total < url_total:
        return (
            ReconciliationStatus.PARTIAL,
            url_total - query_total,
        )

    return (
        ReconciliationStatus.INVALID,
        query_total - url_total,
    )


def reconcile_input(
    input_data: ReconciliationInput,
) -> ReconciliationResult:
    """
    Reconcile already-normalized persisted evidence.

    This is the core reconciliation logic shared by
    ingestion and replay.
    """

    query_impressions = input_data.daily_query_impressions
    query_clicks = input_data.daily_query_clicks

    url_impressions = sum(
        metric.total_impressions
        for metric in input_data.url_daily_metrics
    )

    url_clicks = sum(
        metric.total_clicks
        for metric in input_data.url_daily_metrics
    )

    impression_status, impression_difference = _classify(
        query_impressions,
        url_impressions,
    )

    click_status, click_difference = _classify(
        query_clicks,
        url_clicks,
    )

    is_valid = (
        impression_status != ReconciliationStatus.INVALID
        and click_status != ReconciliationStatus.INVALID
    )

    is_partial = (
        impression_status == ReconciliationStatus.PARTIAL
        or click_status == ReconciliationStatus.PARTIAL
    )

    return ReconciliationResult(
        impression_status=impression_status,
        click_status=click_status,
        query_impressions=query_impressions,
        url_impressions=url_impressions,
        query_clicks=query_clicks,
        url_clicks=url_clicks,
        impression_gap=impression_difference,
        click_gap=click_difference,
        is_valid=is_valid,
        is_partial=is_partial,
    )


def reconcile_ingestion(
    ingestion_result: GSCIngestionResult,
    *,
    url_daily_metrics: Sequence[URLDailyMetric] | None = None,
) -> ReconciliationResult:
    """
    Adapt a GSC ingestion result into the shared reconciliation contract.
    """

    metrics = (
        list(url_daily_metrics)
        if url_daily_metrics is not None
        else ingestion_result.url_daily_metrics
    )

    return reconcile_input(
        ReconciliationInput(
            daily_query_impressions=(
                ingestion_result.daily_query_impressions
            ),
            daily_query_clicks=(
                ingestion_result.daily_query_clicks
            ),
            url_daily_metrics=metrics,
        )
    )