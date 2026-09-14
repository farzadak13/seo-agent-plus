from collections.abc import Iterable

from app.models.measurement import MeasurementMetric, MeasurementResult
from app.models.outcomes import (
    MetricOutcome,
    OutcomeEvaluationPolicy,
    OutcomeEvaluationResult,
    OutcomeMetric,
    OutcomeStatus,
)


_LOWER_IS_BETTER = {OutcomeMetric.POSITION}


def _to_outcome_metric(metric: MeasurementMetric) -> OutcomeMetric:
    return OutcomeMetric(metric.value)


def _direction(metric: OutcomeMetric) -> str:
    return "lower_is_better" if metric in _LOWER_IS_BETTER else "higher_is_better"


def _build_metric_outcomes(measurement: MeasurementResult) -> list[MetricOutcome]:
    outcomes: list[MetricOutcome] = []

    for comparison in measurement.comparisons:
        metric = _to_outcome_metric(comparison.metric)
        direction = _direction(metric)

        if direction == "lower_is_better":
            improved = comparison.absolute_delta < 0
            regressed = comparison.absolute_delta > 0
        else:
            improved = comparison.absolute_delta > 0
            regressed = comparison.absolute_delta < 0

        outcomes.append(
            MetricOutcome(
                metric=metric,
                baseline_value=comparison.baseline_value,
                current_value=comparison.current_value,
                absolute_delta=comparison.absolute_delta,
                relative_change=comparison.relative_change,
                direction=direction,
                improved=improved,
                regressed=regressed,
            )
        )

    return outcomes


def _find_metric(
    outcomes: Iterable[MetricOutcome],
    metric: OutcomeMetric,
) -> MetricOutcome | None:
    for outcome in outcomes:
        if outcome.metric == metric:
            return outcome
    return None


def _evaluate_primary_metric(
    outcome: MetricOutcome | None,
    policy: OutcomeEvaluationPolicy,
) -> tuple[bool, bool]:
    if outcome is None or outcome.relative_change is None:
        return False, False

    change = outcome.relative_change

    if outcome.direction == "lower_is_better":
        improvement = -change
        regression = change
    else:
        improvement = change
        regression = -change

    return (
        improvement >= policy.min_relative_improvement,
        regression > policy.max_relative_regression,
    )


def evaluate_outcome(
    *,
    outcome_id: str,
    measurement: MeasurementResult,
    policy: OutcomeEvaluationPolicy,
) -> OutcomeEvaluationResult:
    metric_outcomes = _build_metric_outcomes(measurement)
    primary = _find_metric(metric_outcomes, policy.primary_metric)

    reasons: list[str] = []

    if measurement.status.value != "ready":
        reasons.append("measurement_not_ready")
        status = OutcomeStatus.INCONCLUSIVE
    elif primary is None:
        reasons.append("primary_metric_not_available")
        status = (
            OutcomeStatus.INCONCLUSIVE
            if policy.require_primary_metric
            else OutcomeStatus.FAILURE
        )
    elif primary.relative_change is None:
        reasons.append("primary_metric_relative_change_undefined")
        status = OutcomeStatus.INCONCLUSIVE
    else:
        primary_improved, primary_regressed = _evaluate_primary_metric(
            primary,
            policy,
        )

        if primary_regressed:
            reasons.append("primary_metric_regressed_beyond_threshold")
            status = OutcomeStatus.FAILURE
        elif primary_improved:
            reasons.append("primary_metric_met_improvement_threshold")
            status = OutcomeStatus.SUCCESS
        else:
            reasons.append("primary_metric_did_not_meet_improvement_threshold")
            status = OutcomeStatus.FAILURE

    if primary is not None and primary.regressed:
        reasons.append("primary_metric_direction_regressed")

    return OutcomeEvaluationResult(
        outcome_id=outcome_id,
        action_id=measurement.action_id,
        site_id=measurement.site_id,
        normalized_url=measurement.normalized_url,
        normalized_query=measurement.normalized_query,
        status=status,
        primary_metric=policy.primary_metric,
        primary_metric_outcome=primary,
        metric_outcomes=metric_outcomes,
        policy=policy,
        measurement=measurement,
        reasons=reasons,
        evidence={
            "measurement_status": measurement.status.value,
            "measurement_id": measurement.measurement_id,
            "primary_metric": policy.primary_metric.value,
            "min_relative_improvement": policy.min_relative_improvement,
            "max_relative_regression": policy.max_relative_regression,
        },
        snapshot=measurement.snapshot,
    )
