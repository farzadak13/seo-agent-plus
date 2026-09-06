from collections.abc import Sequence

from app.models.gsc import NormalizedGSCRow
from app.models.integrity import IngestionIntegrityResult
from app.models.url_metrics import URLDailyMetric


def _row_key(row: NormalizedGSCRow) -> tuple:
    return (
        row.site_id,
        row.date,
        row.normalized_url,
        row.normalized_query,
        row.impressions,
        row.clicks,
        row.avg_position,
    )


def check_ingestion_integrity(
    *,
    rows: Sequence[NormalizedGSCRow],
    url_daily_metrics: Sequence[URLDailyMetric],
    known_response_ids: set[str] | None = None,
    response_id: str | None = None,
) -> IngestionIntegrityResult:
    """
    Check deterministic integrity properties of an ingestion batch.

    Duplicate rows are identified by the complete normalized row identity.

    URL-level metrics are compared against the sum of query-level rows.
    Any gap is preserved as information and is not treated as an error,
    because GSC may expose URL-level totals that exceed query-level totals.
    """

    known_ids = known_response_ids or set()

    response_is_unique = (
        response_id is None or response_id not in known_ids
    )

    seen: set[tuple] = set()
    duplicate_row_count = 0

    query_impressions = 0
    query_clicks = 0

    for row in rows:
        key = _row_key(row)

        if key in seen:
            duplicate_row_count += 1
        else:
            seen.add(key)

        query_impressions += row.impressions
        query_clicks += row.clicks

    unique_row_count = len(seen)

    url_impressions = sum(
        metric.total_impressions
        for metric in url_daily_metrics
    )

    url_clicks = sum(
        metric.total_clicks
        for metric in url_daily_metrics
    )

    return IngestionIntegrityResult(
        response_is_unique=response_is_unique,
        duplicate_row_count=duplicate_row_count,
        unique_row_count=unique_row_count,
        query_impressions=query_impressions,
        url_impressions=url_impressions,
        query_clicks=query_clicks,
        url_clicks=url_clicks,
    )