import pytest

from app.detectors.position import detect_position_decline
from app.models.features import (
    CTRFeatures,
    DataQualityMatrix,
    FeatureSet,
    PositionFeatures,
    VisibilityFeatures,
    VolumeFeatures,
)


def make_features(
    *,
    baseline_median: float,
    current_median: float,
    delta: float,
    trend_slope: float = 0.0,
    trend_direction: str = "stable",
    baseline_completeness: float = 1.0,
    current_completeness: float = 1.0,
) -> FeatureSet:
    return FeatureSet(
        volume=VolumeFeatures(
            current_impressions=1000,
            current_clicks=100,
            baseline_impressions=1000,
            baseline_clicks=100,
        ),
        ctr=CTRFeatures(
            baseline_ctr=0.10,
            current_ctr=0.10,
            absolute_delta=0.0,
            relative_change=0.0,
            baseline_ctr_zero=False,
        ),
        position=PositionFeatures(
            baseline_median=baseline_median,
            current_median=current_median,
            delta=delta,
            mad_current=0.5,
            trend_slope=trend_slope,
            trend_direction=trend_direction,
        ),
        visibility=VisibilityFeatures(
            query_impressions=1000,
            url_total_impressions=2000,
            query_visibility_share=0.5,
        ),
        data_quality=DataQualityMatrix(
            url_level_completeness=1.0,
            query_level_completeness=1.0,
            current_window_completeness=current_completeness,
            baseline_window_completeness=baseline_completeness,
        ),
    )


def test_detects_meaningful_position_decline():
    features = make_features(
        baseline_median=3.0,
        current_median=5.0,
        delta=2.0,
        trend_slope=0.5,
        trend_direction="declining",
    )

    signal = detect_position_decline(features)

    assert signal.signal_type.value == "POSITION_DECLINE"
    assert signal.detected is True
    assert signal.severity.value == "warning"
    assert signal.confidence == pytest.approx(1.0)
    assert signal.evidence["reason"] == "meaningful_position_decline"


def test_small_position_drop_is_not_detected():
    features = make_features(
        baseline_median=3.0,
        current_median=4.0,
        delta=1.0,
    )

    signal = detect_position_decline(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "position_drop_below_threshold"


def test_improving_position_is_not_detected():
    features = make_features(
        baseline_median=5.0,
        current_median=3.0,
        delta=-2.0,
        trend_slope=-0.5,
        trend_direction="improving",
    )

    signal = detect_position_decline(features)

    assert signal.detected is False


@pytest.mark.parametrize(
    "baseline_completeness,current_completeness",
    [
        (0.79, 1.0),
        (1.0, 0.79),
        (0.5, 0.5),
    ],
)
def test_insufficient_data_prevents_detection(
    baseline_completeness,
    current_completeness,
):
    features = make_features(
        baseline_median=3.0,
        current_median=6.0,
        delta=3.0,
        baseline_completeness=baseline_completeness,
        current_completeness=current_completeness,
    )

    signal = detect_position_decline(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "insufficient_data_completeness"


def test_large_position_decline_is_critical():
    features = make_features(
        baseline_median=3.0,
        current_median=6.0,
        delta=3.0,
        trend_slope=1.0,
        trend_direction="declining",
    )

    signal = detect_position_decline(features)

    assert signal.detected is True
    assert signal.severity.value == "critical"


def test_threshold_is_configurable():
    features = make_features(
        baseline_median=3.0,
        current_median=5.0,
        delta=2.0,
    )

    signal = detect_position_decline(
        features,
        min_position_drop=3.0,
    )

    assert signal.detected is False


def test_confidence_reflects_data_completeness():
    features = make_features(
        baseline_median=3.0,
        current_median=5.0,
        delta=2.0,
        baseline_completeness=0.9,
        current_completeness=0.8,
    )

    signal = detect_position_decline(features)

    assert signal.detected is True
    assert signal.confidence == pytest.approx(0.72)