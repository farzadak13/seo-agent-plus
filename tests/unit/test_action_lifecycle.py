from datetime import datetime, timezone

import pytest

from app.action.lifecycle import (
    mark_evaluating,
    mark_execution_failed,
    mark_execution_succeeded,
    mark_executing,
    mark_measurement_window_active,
    mark_outcome,
    mark_waiting_for_recrawl,
)
from app.models.action_lifecycle import ActionLifecycleResultStatus
from app.models.actions import Action, ActionRiskLevel, ActionStatus, ActionType
from app.models.measurement import (
    MeasurementMetric,
    MeasurementResult,
    MeasurementStatus,
    MeasurementWindowSummary,
    MetricComparison,
)
from app.models.outcomes import (
    MetricOutcome,
    OutcomeEvaluationPolicy,
    OutcomeEvaluationResult,
    OutcomeMetric,
    OutcomeStatus,
)
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType


def snapshot():
    return SnapshotMetadata(
        snapshot_id="snapshot-lifecycle",
        data_snapshot_id="data-lifecycle",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )


def action(status):
    return Action(
        action_id="action-lifecycle-001",
        strategy_id="strategy-001",
        opportunity_id="opportunity-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        action_type=ActionType.OPTIMIZE_TITLE,
        status=status,
        risk_level=ActionRiskLevel.LOW,
        confidence_score=0.9,
        expected_impact_score=0.8,
        priority_score=0.85,
        requires_approval=True,
        reasons=[],
        parameters={},
        evidence={},
        snapshot=snapshot(),
    )


def measurement(status=MeasurementStatus.READY):
    summary = MeasurementWindowSummary(
        start_date=datetime(2026, 8, 1).date(),
        end_date=datetime(2026, 8, 1).date(),
        expected_days=1,
        known_days=1,
        observed_days=1,
        completeness=1.0,
        observation_count=1,
        impressions=100,
        clicks=10,
        ctr=0.1,
        median_position=10.0,
    )
    return MeasurementResult(
        measurement_id="measurement-001",
        action_id="action-lifecycle-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        status=status,
        baseline=summary,
        current=summary,
        comparisons=[
            MetricComparison(
                metric=MeasurementMetric.CTR,
                baseline_value=0.1,
                current_value=0.2,
                absolute_delta=0.1,
                relative_change=1.0,
            )
        ],
        reasons=[],
        evidence={},
        snapshot=snapshot(),
        evaluated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )


def outcome(status):
    metric = MetricOutcome(
        metric=OutcomeMetric.CTR,
        baseline_value=0.1,
        current_value=0.2,
        absolute_delta=0.1,
        relative_change=1.0,
        direction="higher_is_better",
        improved=status == OutcomeStatus.SUCCESS,
        regressed=status == OutcomeStatus.FAILURE,
    )
    return OutcomeEvaluationResult(
        outcome_id="outcome-001",
        action_id="action-lifecycle-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        status=status,
        primary_metric=OutcomeMetric.CTR,
        primary_metric_outcome=metric,
        metric_outcomes=[metric],
        policy=OutcomeEvaluationPolicy(
            primary_metric=OutcomeMetric.CTR,
            min_relative_improvement=0.1,
            max_relative_regression=0.2,
        ),
        measurement=measurement(),
        reasons=[status.value],
        evidence={},
        snapshot=snapshot(),
    )


def test_execution_success_path_reaches_executed():
    result = mark_executing(action(ActionStatus.APPROVED))
    assert result.status == ActionLifecycleResultStatus.TRANSITIONED
    assert result.action.status == ActionStatus.EXECUTING

    result = mark_execution_succeeded(
        action=result.action,
        execution_id="execution-001",
        adapter_id="fake-adapter",
    )
    assert result.action.status == ActionStatus.EXECUTED


def test_execution_failure_path_reaches_failed():
    result = mark_executing(action(ActionStatus.APPROVED))
    result = mark_execution_failed(
        action=result.action,
        execution_id="execution-002",
        reason="adapter failure",
    )
    assert result.action.status == ActionStatus.FAILED


def test_executed_can_wait_for_recrawl():
    result = mark_waiting_for_recrawl(
        action=action(ActionStatus.EXECUTED),
        execution_id="execution-001",
    )
    assert result.action.status == ActionStatus.WAITING_FOR_RECRAWL


def test_verified_action_enters_measurement_window():
    result = mark_measurement_window_active(
        action=action(ActionStatus.WAITING_FOR_RECRAWL),
        verification_id="verification-001",
    )
    assert result.action.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE


def test_measurement_ready_enters_evaluation():
    result = mark_evaluating(
        action=action(ActionStatus.MEASUREMENT_WINDOW_ACTIVE),
        measurement=measurement(),
    )
    assert result.action.status == ActionStatus.EVALUATING


def test_success_outcome_closes_action_as_succeeded():
    result = mark_outcome(
        action=action(ActionStatus.EVALUATING),
        outcome=outcome(OutcomeStatus.SUCCESS),
    )
    assert result.action.status == ActionStatus.SUCCEEDED


def test_failure_outcome_closes_action_as_failed():
    result = mark_outcome(
        action=action(ActionStatus.EVALUATING),
        outcome=outcome(OutcomeStatus.FAILURE),
    )
    assert result.action.status == ActionStatus.FAILED


def test_inconclusive_outcome_closes_action_as_inconclusive():
    result = mark_outcome(
        action=action(ActionStatus.EVALUATING),
        outcome=outcome(OutcomeStatus.INCONCLUSIVE),
    )
    assert result.action.status == ActionStatus.INCONCLUSIVE


def test_outcome_requires_evaluating_state():
    with pytest.raises(ValueError):
        mark_outcome(
            action=action(ActionStatus.EXECUTED),
            outcome=outcome(OutcomeStatus.SUCCESS),
        )


def test_outcome_action_mismatch_is_rejected():
    other = outcome(OutcomeStatus.SUCCESS).model_copy(
        update={"action_id": "other-action"}
    )
    with pytest.raises(ValueError, match="action_id"):
        mark_outcome(
            action=action(ActionStatus.EVALUATING),
            outcome=other,
        )
