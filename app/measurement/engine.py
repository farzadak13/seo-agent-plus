from collections.abc import Sequence
from datetime import date as Date, datetime, timedelta, timezone
from statistics import median

from app.models.measurement import (
    MeasurementMetric,
    MeasurementResult,
    MeasurementStatus,
    MeasurementWindowSummary,
    MetricComparison,
)
from app.models.observations import DataStatus, DailyObservation
from app.models.snapshots import SnapshotMetadata


def _validate_window_dates(
    *,
    start_date: Date,
    end_date: Date,
) -> None:
    if end_date < start_date:
        raise ValueError("end_date cannot be before start_date")


def _validate_observations(
    observations: Sequence[DailyObservation],
    *,
    site_id: str,
    normalized_url: str,
    normalized_query: str,
) -> None:
    for observation in observations:
        if observation.site_id != site_id:
            raise ValueError("observation site_id does not match target")
        if observation.normalized_url != normalized_url:
            raise ValueError("observation normalized_url does not match target")
        if observation.normalized_query != normalized_query:
            raise ValueError("observation normalized_query does not match target")


def _window_summary(
    observations: Sequence[DailyObservation],
    *,
    start_date: Date,
    end_date: Date,
) -> MeasurementWindowSummary:
    _validate_window_dates(
        start_date=start_date,
        end_date=end_date,
    )

    expected_days = (end_date - start_date).days + 1
    window_observations = [
        observation
        for observation in observations
        if start_date <= observation.date <= end_date
    ]

    unique = {}
    for observation in window_observations:
        existing = unique.get(observation.date)
        if existing is not None:
            fields = ("impressions", "clicks", "avg_position", "data_status")
            if any(getattr(existing, field) != getattr(observation, field) for field in fields):
                raise ValueError("Conflicting observations for the same date.")
            continue
        unique[observation.date] = observation
    window_observations = list(unique.values())

    observed_days = sum(
        1
        for observation in window_observations
        if observation.data_status == DataStatus.OBSERVED
    )

    known_days = sum(
        1
        for observation in window_observations
        if observation.data_status
        in {
            DataStatus.OBSERVED,
            DataStatus.MISSING_EXPECTED_ZERO,
        }
    )

    completeness = known_days / expected_days

    impressions = sum(
        observation.impressions
        for observation in window_observations
        if observation.data_status != DataStatus.MISSING_UNKNOWN
    )

    clicks = sum(
        observation.clicks
        for observation in window_observations
        if observation.data_status != DataStatus.MISSING_UNKNOWN
    )

    ctr = clicks / impressions if impressions else 0.0

    positions = [
        observation.avg_position
        for observation in window_observations
        if observation.data_status != DataStatus.MISSING_UNKNOWN
        and observation.avg_position is not None
    ]

    return MeasurementWindowSummary(
        start_date=start_date,
        end_date=end_date,
        expected_days=expected_days,
        known_days=known_days,
        observed_days=observed_days,
        completeness=round(completeness, 6),
        observation_count=len(window_observations),
        impressions=impressions,
        clicks=clicks,
        ctr=round(ctr, 6),
        median_position=(
            round(float(median(positions)), 6)
            if positions
            else None
        ),
    )


def _relative_change(
    *,
    baseline: float,
    current: float,
) -> float | None:
    if baseline == 0:
        return None

    return round((current - baseline) / abs(baseline), 6)


def _comparison(
    *,
    metric: MeasurementMetric,
    baseline: float | None,
    current: float | None,
) -> MetricComparison | None:
    if baseline is None or current is None:
        return None

    return MetricComparison(
        metric=metric,
        baseline_value=round(float(baseline), 6),
        current_value=round(float(current), 6),
        absolute_delta=round(float(current - baseline), 6),
        relative_change=_relative_change(
            baseline=float(baseline),
            current=float(current),
        ),
    )


def measure_action(
    *,
    measurement_id: str,
    action_id: str,
    site_id: str,
    normalized_url: str,
    normalized_query: str,
    baseline_observations: Sequence[DailyObservation],
    current_observations: Sequence[DailyObservation],
    baseline_start_date: Date,
    baseline_end_date: Date,
    current_start_date: Date,
    current_end_date: Date,
    snapshot: SnapshotMetadata,
    min_completeness: float = 0.8,
    evaluated_at: datetime | None = None,
) -> MeasurementResult:
    if not 0 <= min_completeness <= 1:
        raise ValueError("min_completeness must be between 0 and 1")

    _validate_window_dates(
        start_date=baseline_start_date,
        end_date=baseline_end_date,
    )
    _validate_window_dates(
        start_date=current_start_date,
        end_date=current_end_date,
    )

    if baseline_end_date >= current_start_date:
        raise ValueError("Baseline must precede the current window without overlap.")
    if (baseline_end_date - baseline_start_date) != (current_end_date - current_start_date):
        raise ValueError("Comparison windows must have equal duration.")

    _validate_observations(
        baseline_observations,
        site_id=site_id,
        normalized_url=normalized_url,
        normalized_query=normalized_query,
    )
    _validate_observations(
        current_observations,
        site_id=site_id,
        normalized_url=normalized_url,
        normalized_query=normalized_query,
    )

    baseline = _window_summary(
        baseline_observations,
        start_date=baseline_start_date,
        end_date=baseline_end_date,
    )
    current = _window_summary(
        current_observations,
        start_date=current_start_date,
        end_date=current_end_date,
    )

    comparisons: list[MetricComparison] = []

    for comparison in (
        _comparison(
            metric=MeasurementMetric.CTR,
            baseline=baseline.ctr,
            current=current.ctr,
        ),
        _comparison(
            metric=MeasurementMetric.POSITION,
            baseline=baseline.median_position,
            current=current.median_position,
        ),
        _comparison(
            metric=MeasurementMetric.CLICKS,
            baseline=float(baseline.clicks),
            current=float(current.clicks),
        ),
        _comparison(
            metric=MeasurementMetric.IMPRESSIONS,
            baseline=float(baseline.impressions),
            current=float(current.impressions),
        ),
    ):
        if comparison is not None:
            comparisons.append(comparison)

    reasons: list[str] = []

    if baseline.completeness < min_completeness:
        reasons.append("baseline_window_insufficient_completeness")

    if current.completeness < min_completeness:
        reasons.append("current_window_insufficient_completeness")

    if baseline.known_days == 0:
        reasons.append("baseline_window_has_no_known_days")

    if current.known_days == 0:
        reasons.append("current_window_has_no_known_days")

    if reasons:
        status = MeasurementStatus.INSUFFICIENT_DATA
    else:
        status = MeasurementStatus.READY
        reasons.append("measurement_data_ready")

    return MeasurementResult(
        measurement_id=measurement_id,
        action_id=action_id,
        site_id=site_id,
        normalized_url=normalized_url,
        normalized_query=normalized_query,
        status=status,
        baseline=baseline,
        current=current,
        comparisons=comparisons,
        reasons=reasons,
        evidence={
            "min_completeness": min_completeness,
            "baseline_observation_count": len(baseline_observations),
            "current_observation_count": len(current_observations),
        },
        snapshot=snapshot,
        evaluated_at=(
            evaluated_at
            if evaluated_at is not None
            else datetime.now(timezone.utc)
        ),
    )

