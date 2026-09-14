from datetime import date, datetime, timezone

import pytest

from app.measurement.engine import measure_action
from app.models.measurement import (
    MeasurementMetric,
    MeasurementStatus,
)
from app.models.observations import DataStatus, DailyObservation
from app.models.snapshots import SnapshotMetadata


def make_snapshot():
    return SnapshotMetadata(
        snapshot_id="measurement-snapshot-001",
        data_snapshot_id="measurement-data-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            8,
            5,
            0,
            tzinfo=timezone.utc,
        ),
    )


def make_observation(
    *,
    day,
    impressions,
    clicks,
    position,
    status=DataStatus.OBSERVED,
):
    return DailyObservation(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=day,
        impressions=impressions,
        clicks=clicks,
        avg_position=position,
        data_status=status,
    )


def run_measurement(
    *,
    baseline_observations,
    current_observations,
    baseline_start_date=date(2026, 8, 1),
    baseline_end_date=date(2026, 8, 2),
    current_start_date=date(2026, 9, 1),
    current_end_date=date(2026, 9, 2),
    min_completeness=0.8,
):
    return measure_action(
        measurement_id="measurement-001",
        action_id="action-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        baseline_observations=baseline_observations,
        current_observations=current_observations,
        baseline_start_date=baseline_start_date,
        baseline_end_date=baseline_end_date,
        current_start_date=current_start_date,
        current_end_date=current_end_date,
        snapshot=make_snapshot(),
        min_completeness=min_completeness,
        evaluated_at=datetime(
            2026,
            9,
            8,
            5,
            0,
            tzinfo=timezone.utc,
        ),
    )


def test_ready_measurement_aggregates_windows():
    baseline = [
        make_observation(
            day=date(2026, 8, 1),
            impressions=1000,
            clicks=50,
            position=8,
        ),
        make_observation(
            day=date(2026, 8, 2),
            impressions=1000,
            clicks=50,
            position=6,
        ),
    ]
    current = [
        make_observation(
            day=date(2026, 9, 1),
            impressions=1200,
            clicks=84,
            position=5,
        ),
        make_observation(
            day=date(2026, 9, 2),
            impressions=800,
            clicks=56,
            position=4,
        ),
    ]

    result = run_measurement(
        baseline_observations=baseline,
        current_observations=current,
    )

    assert result.status == MeasurementStatus.READY
    assert result.baseline.impressions == 2000
    assert result.baseline.clicks == 100
    assert result.baseline.ctr == 0.05
    assert result.baseline.median_position == 7.0
    assert result.current.impressions == 2000
    assert result.current.clicks == 140
    assert result.current.ctr == 0.07
    assert result.current.median_position == 4.5


def test_comparisons_have_expected_direction_and_delta():
    result = run_measurement(
        baseline_observations=[
            make_observation(
                day=date(2026, 8, 1),
                impressions=1000,
                clicks=50,
                position=8,
            ),
            make_observation(
                day=date(2026, 8, 2),
                impressions=1000,
                clicks=50,
                position=6,
            ),
        ],
        current_observations=[
            make_observation(
                day=date(2026, 9, 1),
                impressions=1000,
                clicks=60,
                position=5,
            ),
            make_observation(
                day=date(2026, 9, 2),
                impressions=1000,
                clicks=60,
                position=4,
            ),
        ],
    )

    comparisons = {
        comparison.metric: comparison
        for comparison in result.comparisons
    }

    assert comparisons[MeasurementMetric.CTR].absolute_delta == 0.01
    assert comparisons[MeasurementMetric.POSITION].absolute_delta == -2.5
    assert comparisons[MeasurementMetric.CLICKS].absolute_delta == 20
    assert comparisons[MeasurementMetric.IMPRESSIONS].absolute_delta == 0


def test_zero_baseline_returns_no_relative_change():
    result = run_measurement(
        baseline_observations=[
            make_observation(
                day=date(2026, 8, 1),
                impressions=0,
                clicks=0,
                position=None,
            ),
            make_observation(
                day=date(2026, 8, 2),
                impressions=0,
                clicks=0,
                position=None,
            ),
        ],
        current_observations=[
            make_observation(
                day=date(2026, 9, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 9, 2),
                impressions=100,
                clicks=10,
                position=9,
            ),
        ],
    )

    comparisons = {
        comparison.metric: comparison
        for comparison in result.comparisons
    }

    assert comparisons[MeasurementMetric.CTR].baseline_value == 0
    assert comparisons[MeasurementMetric.CTR].relative_change is None
    assert comparisons[MeasurementMetric.IMPRESSIONS].relative_change is None


def test_missing_unknown_days_reduce_completeness():
    result = run_measurement(
        baseline_observations=[
            make_observation(
                day=date(2026, 8, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
        ],
        current_observations=[
            make_observation(
                day=date(2026, 9, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 9, 2),
                impressions=0,
                clicks=0,
                position=None,
                status=DataStatus.MISSING_UNKNOWN,
            ),
        ],
    )

    assert result.status == MeasurementStatus.INSUFFICIENT_DATA
    assert result.current.completeness == 0.5
    assert "current_window_insufficient_completeness" in result.reasons


def test_missing_expected_zero_counts_as_known_day():
    result = run_measurement(
        baseline_observations=[
            make_observation(
                day=date(2026, 8, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 8, 2),
                impressions=0,
                clicks=0,
                position=None,
                status=DataStatus.MISSING_EXPECTED_ZERO,
            ),
        ],
        current_observations=[
            make_observation(
                day=date(2026, 9, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 9, 2),
                impressions=0,
                clicks=0,
                position=None,
                status=DataStatus.MISSING_EXPECTED_ZERO,
            ),
        ],
    )

    assert result.status == MeasurementStatus.READY
    assert result.baseline.completeness == 1.0
    assert result.current.completeness == 1.0
    assert result.baseline.observed_days == 1
    assert result.baseline.known_days == 2


def test_observations_outside_window_are_ignored():
    result = run_measurement(
        baseline_observations=[
            make_observation(
                day=date(2026, 7, 31),
                impressions=9999,
                clicks=999,
                position=99,
            ),
            make_observation(
                day=date(2026, 8, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 8, 2),
                impressions=100,
                clicks=10,
                position=8,
            ),
        ],
        current_observations=[
            make_observation(
                day=date(2026, 9, 1),
                impressions=100,
                clicks=10,
                position=9,
            ),
            make_observation(
                day=date(2026, 9, 2),
                impressions=100,
                clicks=10,
                position=7,
            ),
            make_observation(
                day=date(2026, 9, 3),
                impressions=9999,
                clicks=999,
                position=1,
            ),
        ],
    )

    assert result.baseline.impressions == 200
    assert result.current.impressions == 200


def test_target_context_mismatch_is_rejected():
    bad = DailyObservation(
        site_id="other-site",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=date(2026, 8, 1),
        impressions=100,
        clicks=10,
        avg_position=10,
        data_status=DataStatus.OBSERVED,
    )

    with pytest.raises(ValueError, match="site_id"):
        run_measurement(
            baseline_observations=[bad],
            current_observations=[],
        )


def test_invalid_date_range_is_rejected():
    with pytest.raises(ValueError, match="end_date"):
        run_measurement(
            baseline_observations=[],
            current_observations=[],
            baseline_start_date=date(2026, 8, 2),
            baseline_end_date=date(2026, 8, 1),
        )


def test_invalid_completeness_threshold_is_rejected():
    with pytest.raises(ValueError, match="min_completeness"):
        run_measurement(
            baseline_observations=[],
            current_observations=[],
            min_completeness=1.5,
        )


def test_snapshot_and_identity_are_preserved():
    result = run_measurement(
        baseline_observations=[
            make_observation(
                day=date(2026, 8, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 8, 2),
                impressions=100,
                clicks=10,
                position=9,
            ),
        ],
        current_observations=[
            make_observation(
                day=date(2026, 9, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 9, 2),
                impressions=100,
                clicks=10,
                position=9,
            ),
        ],
    )

    assert result.measurement_id == "measurement-001"
    assert result.action_id == "action-001"
    assert result.site_id == "site-1"
    assert result.normalized_url == "https://example.com/page"
    assert result.normalized_query == "کفش مردانه"
    assert result.snapshot == make_snapshot()


def test_measurement_is_deterministic_with_fixed_evaluation_time():
    kwargs = dict(
        baseline_observations=[
            make_observation(
                day=date(2026, 8, 1),
                impressions=100,
                clicks=10,
                position=10,
            ),
            make_observation(
                day=date(2026, 8, 2),
                impressions=100,
                clicks=10,
                position=9,
            ),
        ],
        current_observations=[
            make_observation(
                day=date(2026, 9, 1),
                impressions=100,
                clicks=12,
                position=8,
            ),
            make_observation(
                day=date(2026, 9, 2),
                impressions=100,
                clicks=12,
                position=7,
            ),
        ],
    )

    first = run_measurement(**kwargs)
    second = run_measurement(**kwargs)

    assert first.model_dump() == second.model_dump()
