from datetime import datetime, timezone

import pytest

from app.action.lifecycle import mark_outcome
from app.learning.engine import build_feedback_event, build_learning_context
from app.models.actions import Action, ActionRiskLevel, ActionStatus, ActionType
from app.models.learning import LearningMaturity
from app.models.measurement import MeasurementResult, MeasurementStatus, MeasurementWindowSummary, MetricComparison, MeasurementMetric
from app.models.opportunities import OpportunityType
from app.models.outcomes import OutcomeEvaluationPolicy, OutcomeEvaluationResult, OutcomeMetric, OutcomeStatus, MetricOutcome
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType


def snapshot():
    return SnapshotMetadata(
        snapshot_id="snap-learning", data_snapshot_id="data-learning",
        rule_version="rules-v1", config_version="config-v1",
        generated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )


def action():
    return Action(
        action_id="action-1", strategy_id="strategy-1", opportunity_id="opportunity-1",
        site_id="site-1", normalized_url="https://example.com/page", normalized_query="کفش مردانه",
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION, action_type=ActionType.OPTIMIZE_TITLE,
        status=ActionStatus.EVALUATING, risk_level=ActionRiskLevel.LOW, confidence_score=0.9,
        expected_impact_score=0.8, priority_score=0.9, requires_approval=True,
        reasons=[], parameters={}, evidence={}, snapshot=snapshot(),
    )


def outcome(status=OutcomeStatus.SUCCESS, relative_change=0.2):
    measurement = MeasurementResult(
        measurement_id="measurement-1", action_id="action-1", site_id="site-1",
        normalized_url="https://example.com/page", normalized_query="کفش مردانه",
        status=MeasurementStatus.READY,
        baseline=MeasurementWindowSummary(start_date=datetime(2026, 8, 1).date(), end_date=datetime(2026, 8, 7).date(), expected_days=7, known_days=7, observed_days=7, completeness=1, observation_count=7, impressions=100, clicks=5, ctr=0.05, median_position=8),
        current=MeasurementWindowSummary(start_date=datetime(2026, 9, 1).date(), end_date=datetime(2026, 9, 7).date(), expected_days=7, known_days=7, observed_days=7, completeness=1, observation_count=7, impressions=100, clicks=7, ctr=0.07, median_position=6),
        comparisons=[MetricComparison(metric=MeasurementMetric.CTR, baseline_value=0.05, current_value=0.07, absolute_delta=0.02, relative_change=relative_change)],
        reasons=[], evidence={}, snapshot=snapshot(), evaluated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    primary = MetricOutcome(metric=OutcomeMetric.CTR, baseline_value=0.05, current_value=0.07, absolute_delta=0.02, relative_change=relative_change, direction="higher_is_better", improved=status == OutcomeStatus.SUCCESS, regressed=status == OutcomeStatus.FAILURE)
    return OutcomeEvaluationResult(
        outcome_id=f"outcome-{status.value}", action_id="action-1", site_id="site-1",
        normalized_url="https://example.com/page", normalized_query="کفش مردانه", status=status,
        primary_metric=OutcomeMetric.CTR, primary_metric_outcome=primary, metric_outcomes=[primary],
        policy=OutcomeEvaluationPolicy(primary_metric=OutcomeMetric.CTR, min_relative_improvement=0.1, max_relative_regression=0.1),
        measurement=measurement, reasons=[], evidence={}, snapshot=snapshot(),
    )


def event(feedback_id, status=OutcomeStatus.SUCCESS, change=0.2):
    return build_feedback_event(feedback_id=feedback_id, action=action(), outcome=outcome(status, change))


def test_feedback_event_preserves_action_and_outcome_context():
    result = build_feedback_event(feedback_id="feedback-1", action=action(), outcome=outcome())
    assert result.action_id == "action-1"
    assert result.strategy_type == StrategyType.SERP_TITLE_OPTIMIZATION
    assert result.primary_metric == OutcomeMetric.CTR
    assert result.relative_change == 0.2


def test_feedback_rejects_action_mismatch():
    bad = outcome().model_copy(update={"action_id": "other-action"})
    with pytest.raises(ValueError, match="action_id"):
        build_feedback_event(feedback_id="feedback-1", action=action(), outcome=bad)


def test_learning_counts_success_failure_and_inconclusive_separately():
    events = [event("1", OutcomeStatus.SUCCESS), event("2", OutcomeStatus.FAILURE, -0.2), event("3", OutcomeStatus.INCONCLUSIVE)]
    context = build_learning_context(context_id="ctx-1", feedback_events=events, min_samples_for_learning=2)
    performance = context.performances[0]
    assert performance.sample_size == 3
    assert performance.usable_sample_size == 2
    assert performance.success_count == 1
    assert performance.failure_count == 1
    assert performance.inconclusive_count == 1
    assert performance.success_rate == 0.5


def test_learning_requires_minimum_usable_sample():
    context = build_learning_context(context_id="ctx-1", feedback_events=[event("1"), event("2")], min_samples_for_learning=3)
    assert context.performances[0].maturity == LearningMaturity.EXPLORATION
    assert context.signals[0].direction == "insufficient_sample"


def test_learning_becomes_established_at_minimum_sample():
    events = [event(str(i), OutcomeStatus.SUCCESS, 0.2) for i in range(1, 4)]
    context = build_learning_context(context_id="ctx-1", feedback_events=events, min_samples_for_learning=3)
    assert context.performances[0].maturity == LearningMaturity.ESTABLISHED
    assert context.signals[0].direction == "positive"


def test_negative_average_change_creates_negative_signal():
    events = [event("1", OutcomeStatus.FAILURE, -0.2), event("2", OutcomeStatus.FAILURE, -0.3)]
    context = build_learning_context(context_id="ctx-1", feedback_events=events, min_samples_for_learning=2)
    assert context.signals[0].direction == "negative"
    assert context.signals[0].average_relative_change == pytest.approx(-0.25)


def test_feedback_ids_are_deterministically_sorted():
    events = [event("b"), event("a"), event("c")]
    context = build_learning_context(context_id="ctx-1", feedback_events=events, min_samples_for_learning=2)
    assert context.performances[0].feedback_ids == ["a", "b", "c"]


def test_learning_context_is_deterministic():
    events = [event("1"), event("2", OutcomeStatus.FAILURE, -0.1)]
    first = build_learning_context(context_id="same", feedback_events=events, min_samples_for_learning=2)
    second = build_learning_context(context_id="same", feedback_events=list(reversed(events)), min_samples_for_learning=2)
    assert first.model_dump() == second.model_dump()


def test_learning_does_not_mutate_rules():
    events = [event("1"), event("2")]
    context = build_learning_context(context_id="ctx-1", feedback_events=events, min_samples_for_learning=2)
    assert context.evidence["learning_is_advisory_only"] is True
    assert context.signals[0].evidence["does_not_mutate_rules"] is True


def test_empty_learning_context_is_valid():
    context = build_learning_context(context_id="empty", feedback_events=[])
    assert context.performances == []
    assert context.signals == []


def test_invalid_minimum_sample_is_rejected():
    with pytest.raises(ValueError, match="min_samples_for_learning"):
        build_learning_context(context_id="ctx", feedback_events=[], min_samples_for_learning=0)
