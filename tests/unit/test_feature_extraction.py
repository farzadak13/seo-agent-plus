from datetime import date, timedelta

import pytest

from app.features.ctr import (
    calculate_ctr,
    calculate_ctr_delta,
    calculate_relative_ctr_change,
)
from app.features.data_quality import calculate_completeness
from app.features.position import (
    calculate_mad,
    calculate_median_position,
    calculate_position_delta,
    calculate_trend_slope,
    classify_trend,
)
from app.models.observations import DailyObservation, DataStatus


def make_observation(
    day: int,
    *,
    impressions: int,
    clicks: int,
    position: float | None,
    status: DataStatus = DataStatus.OBSERVED,
) -> DailyObservation:
    return DailyObservation(
        site_id="test-site",
        normalized_url="https://example.com/page/",
        normalized_query="کفش",
        date=date(2026, 8, 1) + timedelta(days=day),
        impressions=impressions,
        clicks=clicks,
        avg_position=position,
        data_status=status,
    )


def test_ctr_is_calculated_from_aggregated_metrics():
    observations = [
        make_observation(
            0,
            impressions=10,
            clicks=1,
            position=4.0,
        ),
        make_observation(
            1,
            impressions=90,
            clicks=9,
            position=4.0,
        ),
    ]

    assert calculate_ctr(observations) == pytest.approx(0.10)


def test_zero_impressions_return_zero_ctr():
    observations = [
        make_observation(
            0,
            impressions=0,
            clicks=0,
            position=None,
            status=DataStatus.MISSING_EXPECTED_ZERO,
        ),
    ]

    assert calculate_ctr(observations) == 0.0


def test_ctr_delta():
    assert calculate_ctr_delta(
        baseline_ctr=0.05,
        current_ctr=0.02,
    ) == pytest.approx(-0.03)


def test_relative_ctr_change():
    assert calculate_relative_ctr_change(
        baseline_ctr=0.05,
        current_ctr=0.02,
    ) == pytest.approx(-0.60)


def test_relative_ctr_change_with_zero_baseline():
    assert calculate_relative_ctr_change(
        baseline_ctr=0.0,
        current_ctr=0.02,
    ) == 0.0


def test_completeness_with_all_valid_observations():
    observations = [
        make_observation(
            i,
            impressions=100,
            clicks=10,
            position=4.0,
        )
        for i in range(14)
    ]

    assert calculate_completeness(observations) == 1.0


def test_completeness_with_unknown_days():
    observations = [
        make_observation(
            i,
            impressions=100 if i < 7 else 0,
            clicks=10 if i < 7 else 0,
            position=4.0 if i < 7 else None,
            status=(
                DataStatus.OBSERVED
                if i < 7
                else DataStatus.MISSING_UNKNOWN
            ),
        )
        for i in range(14)
    ]

    assert calculate_completeness(observations) == pytest.approx(0.5)


def test_expected_zero_days_are_usable():
    observations = [
        make_observation(
            i,
            impressions=100 if i == 0 else 0,
            clicks=10 if i == 0 else 0,
            position=4.0 if i == 0 else None,
            status=(
                DataStatus.OBSERVED
                if i == 0
                else DataStatus.MISSING_EXPECTED_ZERO
            ),
        )
        for i in range(14)
    ]

    assert calculate_completeness(observations) == 1.0


def test_position_median():
    observations = [
        make_observation(
            0,
            impressions=100,
            clicks=10,
            position=2.0,
        ),
        make_observation(
            1,
            impressions=100,
            clicks=10,
            position=4.0,
        ),
        make_observation(
            2,
            impressions=100,
            clicks=10,
            position=6.0,
        ),
    ]

    assert calculate_median_position(observations) == 4.0


def test_position_mad():
    observations = [
        make_observation(
            0,
            impressions=100,
            clicks=10,
            position=2.0,
        ),
        make_observation(
            1,
            impressions=100,
            clicks=10,
            position=4.0,
        ),
        make_observation(
            2,
            impressions=100,
            clicks=10,
            position=6.0,
        ),
    ]

    assert calculate_mad(observations) == 2.0


def test_position_delta():
    assert calculate_position_delta(
        baseline_median=3.0,
        current_median=7.0,
    ) == 4.0


def test_positive_position_trend_is_declining():
    observations = [
        make_observation(
            i,
            impressions=100,
            clicks=10,
            position=float(i),
        )
        for i in range(2, 7)
    ]

    slope = calculate_trend_slope(observations)

    assert slope > 0
    assert classify_trend(slope) == "declining"


def test_negative_position_trend_is_improving():
    observations = [
        make_observation(
            i,
            impressions=100,
            clicks=10,
            position=float(10 - i),
        )
        for i in range(5)
    ]

    slope = calculate_trend_slope(observations)

    assert slope < 0
    assert classify_trend(slope) == "improving"


def test_near_zero_position_trend_is_stable():
    observations = [
        make_observation(
            0,
            impressions=100,
            clicks=10,
            position=4.0,
        ),
        make_observation(
            1,
            impressions=100,
            clicks=10,
            position=4.01,
        ),
        make_observation(
            2,
            impressions=100,
            clicks=10,
            position=3.99,
        ),
    ]

    slope = calculate_trend_slope(observations)

    assert classify_trend(slope) == "stable"


def test_no_position_data_returns_zero_metrics():
    observations = [
        make_observation(
            0,
            impressions=0,
            clicks=0,
            position=None,
            status=DataStatus.MISSING_EXPECTED_ZERO,
        ),
        make_observation(
            1,
            impressions=0,
            clicks=0,
            position=None,
            status=DataStatus.MISSING_EXPECTED_ZERO,
        ),
    ]

    assert calculate_median_position(observations) == 0.0
    assert calculate_mad(observations) == 0.0
    assert calculate_trend_slope(observations) == 0.0