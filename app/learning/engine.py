from app.learning.direction import benefit_change
from collections import defaultdict
from collections.abc import Sequence

from app.models.actions import Action
from app.models.learning import (
    FeedbackEvent,
    LearningContext,
    LearningMaturity,
    LearningSignal,
    StrategyPerformance,
)
from app.models.outcomes import OutcomeEvaluationResult, OutcomeMetric, OutcomeStatus


def build_feedback_event(
    *,
    feedback_id: str,
    action: Action,
    outcome: OutcomeEvaluationResult,
) -> FeedbackEvent:
    if outcome.action_id != action.action_id:
        raise ValueError("Outcome action_id does not match action.")

    if outcome.site_id != action.site_id:
        raise ValueError("Outcome site_id does not match action.")

    if outcome.normalized_url != action.normalized_url:
        raise ValueError("Outcome normalized_url does not match action.")

    if outcome.normalized_query != action.normalized_query:
        raise ValueError("Outcome normalized_query does not match action.")

    primary = outcome.primary_metric_outcome

    return FeedbackEvent(
        feedback_id=feedback_id,
        action_id=action.action_id,
        strategy_id=action.strategy_id,
        opportunity_id=action.opportunity_id,
        site_id=action.site_id,
        normalized_url=action.normalized_url,
        normalized_query=action.normalized_query,
        strategy_type=action.strategy_type,
        action_type=action.action_type,
        outcome_status=outcome.status,
        primary_metric=outcome.primary_metric,
        relative_change=(
            primary.relative_change if primary is not None else None
        ),
        improved=primary.improved if primary is not None else None,
        regressed=primary.regressed if primary is not None else None,
        snapshot=outcome.snapshot,
        evidence={
            "outcome_id": outcome.outcome_id,
            "outcome_status": outcome.status.value,
            "measurement_id": outcome.measurement.measurement_id,
            "learning_safe": True,
        },
    )


def _group_key(feedback: FeedbackEvent) -> tuple[str, str, str]:
    return (
        feedback.strategy_type.value,
        feedback.action_type.value,
        feedback.primary_metric.value,
    )


def _build_performance(
    *,
    events: Sequence[FeedbackEvent],
    min_samples_for_learning: int,
) -> StrategyPerformance:
    ordered = sorted(events, key=lambda event: event.feedback_id)
    first = ordered[0]

    successes = sum(
        1 for event in ordered if event.outcome_status == OutcomeStatus.SUCCESS
    )
    failures = sum(
        1 for event in ordered if event.outcome_status == OutcomeStatus.FAILURE
    )
    inconclusive = sum(
        1
        for event in ordered
        if event.outcome_status == OutcomeStatus.INCONCLUSIVE
    )

    usable = successes + failures
    relative_changes = [
        event.relative_change
        for event in ordered
        if event.outcome_status in {OutcomeStatus.SUCCESS, OutcomeStatus.FAILURE}
        and event.relative_change is not None
    ]

    improvement_events = [
        event
        for event in ordered
        if event.improved is True
        and event.outcome_status in {OutcomeStatus.SUCCESS, OutcomeStatus.FAILURE}
    ]
    regression_events = [
        event
        for event in ordered
        if event.regressed is True
        and event.outcome_status in {OutcomeStatus.SUCCESS, OutcomeStatus.FAILURE}
    ]

    maturity = (
        LearningMaturity.ESTABLISHED
        if usable >= min_samples_for_learning
        else LearningMaturity.EXPLORATION
    )

    data_completeness = usable / len(ordered) if ordered else 0.0
    sample_confidence = min(1.0, usable / min_samples_for_learning)
    confidence = sample_confidence * data_completeness

    return StrategyPerformance(
        strategy_type=first.strategy_type,
        action_type=first.action_type,
        primary_metric=first.primary_metric,
        sample_size=len(ordered),
        usable_sample_size=usable,
        success_count=successes,
        failure_count=failures,
        inconclusive_count=inconclusive,
        success_rate=(successes / usable if usable else None),
        average_relative_change=(
            sum(relative_changes) / len(relative_changes)
            if relative_changes
            else None
        ),
        improvement_rate=(
            len(improvement_events) / usable if usable else None
        ),
        regression_rate=(
            len(regression_events) / usable if usable else None
        ),
        maturity=maturity,
        confidence=confidence,
        feedback_ids=[event.feedback_id for event in ordered],
    )


def _build_signal(
    *,
    signal_id: str,
    performance: StrategyPerformance,
) -> LearningSignal:
    benefit = benefit_change(performance.primary_metric, performance.average_relative_change)
    if performance.maturity == LearningMaturity.EXPLORATION:
        direction = "insufficient_sample"
    elif (
        benefit is not None
        and benefit > 0
    ):
        direction = "positive"
    elif (
        benefit is not None
        and benefit < 0
    ):
        direction = "negative"
    else:
        direction = "neutral"

    return LearningSignal(
        signal_id=signal_id,
        strategy_type=performance.strategy_type,
        action_type=performance.action_type,
        primary_metric=performance.primary_metric,
        maturity=performance.maturity,
        confidence=performance.confidence,
        sample_size=performance.sample_size,
        success_rate=performance.success_rate,
        average_relative_change=performance.average_relative_change,
        direction=direction,
        evidence={
            "learning_only": True,
            "does_not_mutate_rules": True,
            "feedback_ids": list(performance.feedback_ids),
        },
    )


def build_learning_context(
    *,
    context_id: str,
    feedback_events: Sequence[FeedbackEvent],
    min_samples_for_learning: int = 5,
) -> LearningContext:
    if min_samples_for_learning < 1:
        raise ValueError("min_samples_for_learning must be >= 1.")

    groups: dict[tuple[str, str, str], list[FeedbackEvent]] = defaultdict(list)
    seen = {}
    for event in feedback_events:
        if event.feedback_id in seen:
            if seen[event.feedback_id] != event:
                raise ValueError("Conflicting duplicate feedback_id.")
            continue
        seen[event.feedback_id] = event
        groups[_group_key(event)].append(event)

    performances = [
        _build_performance(
            events=events,
            min_samples_for_learning=min_samples_for_learning,
        )
        for _, events in sorted(groups.items())
    ]

    signals = [
        _build_signal(
            signal_id=(
                f"learning:{performance.strategy_type.value}:"
                f"{performance.action_type.value}:"
                f"{performance.primary_metric.value}"
            ),
            performance=performance,
        )
        for performance in performances
    ]

    return LearningContext(
        context_id=context_id,
        min_samples_for_learning=min_samples_for_learning,
        performances=performances,
        signals=signals,
        evidence={
            "feedback_count": len(seen),
            "learning_is_advisory_only": True,
            "rules_were_not_mutated": True,
        },
    )

