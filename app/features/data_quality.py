from collections.abc import Sequence
from datetime import date as Date

from app.models.observations import DataStatus


def calculate_completeness(observations) -> float:
    """
    Calculate completeness from usable observations.

    OBSERVED and MISSING_EXPECTED_ZERO are considered usable.
    """

    if not observations:
        return 0.0

    usable_statuses = {
        DataStatus.OBSERVED,
        DataStatus.MISSING_EXPECTED_ZERO,
    }

    usable = sum(
        1
        for observation in observations
        if getattr(observation, "data_status", None)
        in usable_statuses
    )

    return usable / len(observations)


def calculate_window_completeness(
    daily_statuses: Sequence[tuple[Date, DataStatus]],
) -> float:
    """
    Calculate completeness across an expected calendar window.

    OBSERVED and MISSING_EXPECTED_ZERO are usable.
    MISSING_UNKNOWN is not usable.

    Unlike observation-based completeness, this function evaluates
    the expected calendar itself.
    """

    if not daily_statuses:
        return 0.0

    usable_statuses = {
        DataStatus.OBSERVED,
        DataStatus.MISSING_EXPECTED_ZERO,
    }

    usable = sum(
        1
        for _, status in daily_statuses
        if status in usable_statuses
    )

    return usable / len(daily_statuses)