from datetime import date, datetime, timezone

from app.measurement.engine import measure_action
from app.models.measurement import MeasurementMetric, MeasurementStatus
from app.models.observations import DataStatus, DailyObservation
from app.models.outcomes import (
    OutcomeEvaluationPolicy,
    OutcomeMetric,
    OutcomeStatus,
)
from app.models.snapshots import SnapshotMetadata
from app.outcome.engine import evaluate_outcome


def make_snapshot():
    return SnapshotMetadata(
        snapshot_id="snapshot-outcome",
        data_snapshot_id="data-outcome",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )


def make_observation(day, impressions, clicks, position):
    return DailyObservation(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=day,
        impressions=impressions,
        clicks=clicks,
        avg_position=position,
        data_status=DataStatus.OBSERVED,
    )


def make_measurement(
    *,
    baseline_clicks=10,
    current_clicks=20,
    baseline_impressions=100,
    current_impressions=100,
    baseline_position=10.0,
    current_position=5.0,
    baseline_start=date(2026, 8, 1),
    baseline_end=date(2026, 8, 1),
    current_start=date(2026, 9, 1),
    current_end=date(2026, 9, 1),
):
    return measure_action(
        measurement_id="measurement-001",
        action_id="action-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        baseline_observations=[
            make_observation(
                baseline_start,
                baseline_impressions,
                baseline_clicks,
                baseline_position,
            )
        ],
        current_observations=[
            make_observation(
                current_start,
                current_impressions,
                current_clicks,
                current_position,
            )
        ],
        baseline_start_date=baseline_start,
        baseline_end_date=baseline_end,
        current_start_date=current_start,
        current_end_date=current_end,
        snapshot=make_snapshot(),
    )


def test_ctr_success_when_improvement_meets_threshold():
    measurement = make_measurement(
        baseline_clicks=10,
        current_clicks=20,
    )

    result = evaluate_outcome(
        outcome_id="outcome-001",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CTR,
            min_relative_improvement=0.5,
            max_relative_regression=0.2,
        ),
    )

    assert measurement.status == MeasurementStatus.READY
    assert result.status == OutcomeStatus.SUCCESS
    assert result.primary_metric == OutcomeMetric.CTR
    assert result.primary_metric_outcome is not None
    assert result.primary_metric_outcome.improved is True


def test_ctr_failure_when_improvement_is_below_threshold():
    measurement = make_measurement(
        baseline_clicks=10,
        current_clicks=11,
    )

    result = evaluate_outcome(
        outcome_id="outcome-002",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CTR,
            min_relative_improvement=0.5,
            max_relative_regression=0.2,
        ),
    )

    assert result.status == OutcomeStatus.FAILURE
    assert "primary_metric_did_not_meet_improvement_threshold" in result.reasons


def test_ctr_failure_when_regression_exceeds_threshold():
    measurement = make_measurement(
        baseline_clicks=20,
        current_clicks=10,
    )

    result = evaluate_outcome(
        outcome_id="outcome-003",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CTR,
            min_relative_improvement=0.1,
            max_relative_regression=0.2,
        ),
    )

    assert result.status == OutcomeStatus.FAILURE
    assert "primary_metric_regressed_beyond_threshold" in result.reasons


def test_position_success_when_position_improves():
    measurement = make_measurement(
        baseline_position=10.0,
        current_position=8.0,
    )

    result = evaluate_outcome(
        outcome_id="outcome-004",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.POSITION,
            min_relative_improvement=0.15,
            max_relative_regression=0.2,
        ),
    )

    assert result.status == OutcomeStatus.SUCCESS
    assert result.primary_metric_outcome is not None
    assert result.primary_metric_outcome.direction == "lower_is_better"


def test_position_regression_is_failure():
    measurement = make_measurement(
        baseline_position=10.0,
        current_position=13.0,
    )

    result = evaluate_outcome(
        outcome_id="outcome-005",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.POSITION,
            min_relative_improvement=0.1,
            max_relative_regression=0.2,
        ),
    )

    assert result.status == OutcomeStatus.FAILURE
    assert result.primary_metric_outcome is not None
    assert result.primary_metric_outcome.regressed is True


def test_insufficient_measurement_is_inconclusive():
    measurement = make_measurement(
        baseline_start=date(2026, 8, 1),
        baseline_end=date(2026, 8, 5),
        current_start=date(2026, 9, 1),
        current_end=date(2026, 9, 5),
    )

    measurement = measurement.model_copy(
        update={"status": MeasurementStatus.INSUFFICIENT_DATA}
    )

    result = evaluate_outcome(
        outcome_id="outcome-006",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CTR,
            min_relative_improvement=0.1,
            max_relative_regression=0.2,
        ),
    )

    assert result.status == OutcomeStatus.INCONCLUSIVE
    assert result.primary_metric_outcome is not None


def test_snapshot_is_preserved():
    measurement = make_measurement()

    result = evaluate_outcome(
        outcome_id="outcome-007",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CTR,
            min_relative_improvement=0.1,
            max_relative_regression=0.2,
        ),
    )

    assert result.snapshot == measurement.snapshot


def test_all_measurement_metrics_are_mapped():
    measurement = make_measurement()

    result = evaluate_outcome(
        outcome_id="outcome-008",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CTR,
            min_relative_improvement=0.1,
            max_relative_regression=0.2,
        ),
    )

    metrics = {item.metric for item in result.metric_outcomes}

    assert metrics == {
        OutcomeMetric.CTR,
        OutcomeMetric.POSITION,
        OutcomeMetric.CLICKS,
        OutcomeMetric.IMPRESSIONS,
    }


def test_relative_change_zero_baseline_is_inconclusive_for_primary_metric():
    measurement = make_measurement(
        baseline_clicks=0,
        current_clicks=10,
        baseline_impressions=100,
        current_impressions=100,
    )

    result = evaluate_outcome(
        outcome_id="outcome-009",
        measurement=measurement,
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CLICKS,
            min_relative_improvement=0.1,
            max_relative_regression=0.2,
        ),
    )

    assert result.status == OutcomeStatus.INCONCLUSIVE
    assert result.primary_metric_outcome is not None
    assert result.primary_metric_outcome.relative_change is None


def test_outcome_is_deterministic():
    measurement = make_measurement()
    policy = OutcomeEvaluationPolicy(
        primary_metric=OutcomeMetric.CTR,
        min_relative_improvement=0.1,
        max_relative_regression=0.2,
    )

    first = evaluate_outcome(
        outcome_id="outcome-010",
        measurement=measurement,
        policy=policy,
    )
    second = evaluate_outcome(
        outcome_id="outcome-010",
        measurement=measurement,
        policy=policy,
    )

    assert first.model_dump() == second.model_dump()
