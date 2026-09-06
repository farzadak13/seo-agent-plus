from collections.abc import Sequence

import numpy as np

from app.models.observations import DailyObservation, DataStatus


def _valid_positions(
    observations: Sequence[DailyObservation],
) -> list[tuple]:
    usable = [
        observation
        for observation in observations
        if observation.data_status
        in {DataStatus.OBSERVED, DataStatus.MISSING_EXPECTED_ZERO}
        and observation.avg_position is not None
    ]

    return sorted(
        ((observation.date, observation.avg_position) for observation in usable),
        key=lambda item: item[0],
    )


def calculate_median_position(
    observations: Sequence[DailyObservation],
) -> float:
    positions = [position for _, position in _valid_positions(observations)]

    if not positions:
        return 0.0

    return float(np.median(positions))


def calculate_mad(
    observations: Sequence[DailyObservation],
) -> float:
    positions = [position for _, position in _valid_positions(observations)]

    if not positions:
        return 0.0

    median = float(np.median(positions))
    deviations = [abs(position - median) for position in positions]

    return float(np.median(deviations))


def calculate_position_delta(
    baseline_median: float,
    current_median: float,
) -> float:
    return current_median - baseline_median


def calculate_trend_slope(
    observations: Sequence[DailyObservation],
) -> float:
    dated_positions = _valid_positions(observations)

    if len(dated_positions) < 2:
        return 0.0

    positions = [position for _, position in dated_positions]
    x = np.arange(len(positions), dtype=float)
    y = np.array(positions, dtype=float)

    slope = np.polyfit(x, y, 1)[0]

    return float(slope)


def classify_trend(
    slope: float,
    stable_threshold: float = 0.05,
) -> str:
    if slope > stable_threshold:
        return "declining"

    if slope < -stable_threshold:
        return "improving"

    return "stable"