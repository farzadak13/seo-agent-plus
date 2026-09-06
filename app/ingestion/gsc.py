from collections.abc import Iterable
from datetime import date as Date

from app.features.url_metrics import aggregate_url_daily_metrics
from app.ingestion.daily_window import classify_daily_window
from app.models.gsc import NormalizedGSCRow, RawGSCResponse
from app.models.ingestion import GSCIngestionResult
from app.models.observations import DailyObservation
from app.models.url_metrics import URLDailyMetric
from app.normalization.gsc import normalize_gsc_response
from app.normalization.observation import rows_to_observations


def ingest_gsc_response(
    *,
    response: RawGSCResponse,
    start_date: Date,
    end_date: Date,
    expected_zero_dates: Iterable[Date] | None = None,
) -> GSCIngestionResult:
    """
    Process one raw GSC response through the deterministic ingestion layer.

    Pipeline:

        Raw GSC
          ↓
        Normalized Rows
          ├── Daily Observations
          └── URL Daily Metrics
          ↓
        Daily Status Classification

    No network, database, LLM, or side effects are performed.
    """

    normalized_rows: list[NormalizedGSCRow] = normalize_gsc_response(
        response
    )

    observations: list[DailyObservation] = rows_to_observations(
        normalized_rows
    )

    url_daily_metrics: list[URLDailyMetric] = (
        aggregate_url_daily_metrics(normalized_rows)
    )

    daily_status = classify_daily_window(
        start_date=start_date,
        end_date=end_date,
        observations=observations,
        expected_zero_dates=expected_zero_dates,
    )

    return GSCIngestionResult(
        site_id=response.site_id,
        response_id=response.response_id,
        normalized_rows=normalized_rows,
        observations=observations,
        url_daily_metrics=url_daily_metrics,
        daily_status=daily_status,
    )