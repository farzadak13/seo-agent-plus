from collections import defaultdict
from collections.abc import Sequence

from app.models.gsc import NormalizedGSCRow
from app.models.url_metrics import URLDailyMetric


def aggregate_url_daily_metrics(
    rows: Sequence[NormalizedGSCRow],
) -> list[URLDailyMetric]:
    """
    Aggregate GSC rows from URL+Query level to URL+Date level.

    Rows are grouped by:
        site_id + normalized_url + date

    Query-level rows are summed for impressions and clicks.

    This function does not infer missing data and does not create
    synthetic zero-value metrics.
    """

    aggregates: dict[tuple, dict[str, int]] = defaultdict(
        lambda: {
            "impressions": 0,
            "clicks": 0,
        }
    )

    for row in rows:
        key = (
            row.site_id,
            row.normalized_url,
            row.date,
        )

        aggregates[key]["impressions"] += row.impressions
        aggregates[key]["clicks"] += row.clicks

    metrics: list[URLDailyMetric] = []

    for (site_id, normalized_url, day), values in sorted(
        aggregates.items(),
        key=lambda item: item[0],
    ):
        metrics.append(
            URLDailyMetric(
                site_id=site_id,
                normalized_url=normalized_url,
                date=day,
                total_impressions=values["impressions"],
                total_clicks=values["clicks"],
            )
        )

    return metrics