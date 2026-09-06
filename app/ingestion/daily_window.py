from collections.abc import Iterable, Sequence
from datetime import date as Date

from app.features.missing_data import classify_missing_days
from app.ingestion.calendar import expand_date_range
from app.models.observations import DataStatus, DailyObservation


def classify_daily_window(
    *,
    start_date: Date,
    end_date: Date,
    observations: Sequence[DailyObservation],
    expected_zero_dates: Iterable[Date] | None = None,
) -> list[tuple[Date, DataStatus]]:
    """
    Build the expected daily calendar and classify each day.

    This function only orchestrates deterministic domain logic.
    It does not fetch data, create observations, or mutate input.
    """

    expected_dates = expand_date_range(
        start_date,
        end_date,
    )

    return classify_missing_days(
        expected_dates=expected_dates,
        observations=observations,
        expected_zero_dates=expected_zero_dates,
    )