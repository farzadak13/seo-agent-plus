from collections.abc import Iterable, Sequence
from datetime import date as Date

from app.models.observations import DataStatus, DailyObservation


def classify_missing_days(
    *,
    expected_dates: Iterable[Date],
    observations: Sequence[DailyObservation],
    expected_zero_dates: Iterable[Date] | None = None,
) -> list[tuple[Date, DataStatus]]:
    """
    Classify each expected date according to whether data was observed.

    Rules:
    - A date with at least one observation is OBSERVED.
    - A missing date explicitly marked as expected-zero is
      MISSING_EXPECTED_ZERO.
    - Other missing dates are MISSING_UNKNOWN.

    The function does not create synthetic observations.
    """

    expected = set(expected_dates)
    zero_dates = set(expected_zero_dates or [])

    observed_dates = {
        observation.date
        for observation in observations
    }

    results: list[tuple[Date, DataStatus]] = []

    for day in sorted(expected):
        if day in observed_dates:
            status = DataStatus.OBSERVED
        elif day in zero_dates:
            status = DataStatus.MISSING_EXPECTED_ZERO
        else:
            status = DataStatus.MISSING_UNKNOWN

        results.append((day, status))

    return results


def apply_data_status(
    observations: Sequence[DailyObservation],
    status_by_date: dict[Date, DataStatus],
) -> list[DailyObservation]:
    """
    Apply classified data status to existing observations.

    Returns copied observations and does not mutate the originals.
    """

    return [
        observation.model_copy(
            update={
                "data_status": status_by_date.get(
                    observation.date,
                    observation.data_status,
                )
            }
        )
        for observation in observations
    ]