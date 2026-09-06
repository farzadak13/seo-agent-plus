from collections.abc import Sequence

from app.features.ctr import (
    calculate_ctr,
    calculate_ctr_delta,
    calculate_relative_ctr_change,
)
from app.features.data_quality import (
    calculate_completeness,
    calculate_window_completeness,
)
from app.features.data_quality import calculate_completeness
from app.features.position import (
    calculate_mad,
    calculate_median_position,
    calculate_position_delta,
    calculate_trend_slope,
    classify_trend,
)
from app.models.features import (
    CTRFeatures,
    DataQualityMatrix,
    FeatureSet,
    PositionFeatures,
    VisibilityFeatures,
    VolumeFeatures,
)
from app.models.observations import DailyObservation
from app.models.url_metrics import URLDailyMetric


def _total_impressions(
    observations: Sequence[DailyObservation],
) -> int:
    return sum(
        observation.impressions
        for observation in observations
    )


def _total_clicks(
    observations: Sequence[DailyObservation],
) -> int:
    return sum(
        observation.clicks
        for observation in observations
    )


def extract_feature_set(
    *,
    baseline_observations: Sequence[DailyObservation],
    current_observations: Sequence[DailyObservation],
    baseline_url_metrics: Sequence[URLDailyMetric],
    current_url_metrics: Sequence[URLDailyMetric],
    reconciliation_completeness: float = 1.0,
    current_window_completeness: float | None = None,
    baseline_window_completeness: float | None = None,
) -> FeatureSet:
    baseline_impressions = _total_impressions(
        baseline_observations
    )
    current_impressions = _total_impressions(
        current_observations
    )

    baseline_clicks = _total_clicks(
        baseline_observations
    )
    current_clicks = _total_clicks(
        current_observations
    )

    baseline_ctr = calculate_ctr(baseline_observations)
    current_ctr = calculate_ctr(current_observations)

    baseline_position = calculate_median_position(
        baseline_observations
    )
    current_position = calculate_median_position(
        current_observations
    )

    position_slope = calculate_trend_slope(
        current_observations
    )

    query_impressions = current_impressions

    url_total_impressions = sum(
        metric.total_impressions
        for metric in current_url_metrics
    )

    query_visibility_share = (
        query_impressions / url_total_impressions
        if url_total_impressions > 0
        else 0.0
    )
    if current_window_completeness is None:
        current_window_completeness = calculate_completeness(
            current_observations
        )

    if baseline_window_completeness is None:
        baseline_window_completeness = calculate_completeness(
            baseline_observations
        )
    return FeatureSet(
        volume=VolumeFeatures(
            current_impressions=current_impressions,
            baseline_impressions=baseline_impressions,
            current_clicks=current_clicks,
            baseline_clicks=baseline_clicks,
        ),
        ctr=CTRFeatures(
            baseline_ctr=baseline_ctr,
            current_ctr=current_ctr,
            absolute_delta=calculate_ctr_delta(
                baseline_ctr,
                current_ctr,
            ),
            relative_change=calculate_relative_ctr_change(
                baseline_ctr,
                current_ctr,
            ),
            baseline_ctr_zero=baseline_ctr == 0,
        ),
        position=PositionFeatures(
            baseline_median=baseline_position,
            current_median=current_position,
            delta=calculate_position_delta(
                baseline_position,
                current_position,
            ),
            mad_current=calculate_mad(
                current_observations
            ),
            trend_slope=position_slope,
            trend_direction=classify_trend(
                position_slope
            ),
        ),
        visibility=VisibilityFeatures(
            query_impressions=query_impressions,
            url_total_impressions=url_total_impressions,
            query_visibility_share=query_visibility_share,
        ),
        data_quality=DataQualityMatrix(
            url_level_completeness=calculate_completeness(
                current_url_metrics
            ),
            query_level_completeness=calculate_completeness(
                current_observations
            ),
            current_window_completeness=current_window_completeness,
            baseline_window_completeness=baseline_window_completeness,
            reconciliation_completeness=reconciliation_completeness,
        ),
    )