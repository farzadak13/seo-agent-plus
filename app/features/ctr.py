from collections.abc import Sequence

from app.models.observations import DailyObservation, DataStatus


def _usable_observations(
    observations: Sequence[DailyObservation],
) -> list[DailyObservation]:
    return [
        observation
        for observation in observations
        if observation.data_status
        in {DataStatus.OBSERVED, DataStatus.MISSING_EXPECTED_ZERO}
    ]


def calculate_ctr(observations: Sequence[DailyObservation]) -> float:
    usable = _usable_observations(observations)

    total_impressions = sum(
        observation.impressions for observation in usable
    )
    total_clicks = sum(
        observation.clicks for observation in usable
    )

    if total_impressions == 0:
        return 0.0

    return total_clicks / total_impressions


def calculate_ctr_delta(
    baseline_ctr: float,
    current_ctr: float,
) -> float:
    return current_ctr - baseline_ctr


def calculate_relative_ctr_change(
    baseline_ctr: float,
    current_ctr: float,
) -> float:
    if baseline_ctr == 0:
        return 0.0

    return (current_ctr - baseline_ctr) / baseline_ctr