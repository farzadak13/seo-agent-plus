import pytest

from app.detectors.volatility import detect_position_volatility
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
    mad_current: float,
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
            baseline_median=5.0,
            current_median=5.0,
            delta=0.0,
            mad_current=mad_current,
            trend_slope=0.0,
            trend_direction="stable",
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


def test_detects_high_position_volatility():
    features = make_features(mad_current=2.0)

    signal = detect_position_volatility(features)

    assert signal.signal_type.value == "VOLATILITY"
    assert signal.detected is True
    assert signal.severity.value == "warning"
    assert signal.confidence == pytest.approx(1.0)
    assert signal.evidence["reason"] == "high_position_volatility"


def test_low_volatility_is_not_detected():
    features = make_features(mad_current=1.49)

    signal = detect_position_volatility(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "volatility_below_threshold"


def test_large_volatility_is_critical():
    features = make_features(mad_current=3.0)

    signal = detect_position_volatility(features)

    assert signal.detected is True
    assert signal.severity.value == "critical"


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
        mad_current=5.0,
        baseline_completeness=baseline_completeness,
        current_completeness=current_completeness,
    )

    signal = detect_position_volatility(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "insufficient_data_completeness"


def test_threshold_is_configurable():
    features = make_features(mad_current=2.0)

    signal = detect_position_volatility(
        features,
        min_mad=2.5,
    )

    assert signal.detected is False


def test_confidence_reflects_data_completeness():
    features = make_features(
        mad_current=2.0,
        baseline_completeness=0.9,
        current_completeness=0.8,
    )

    signal = detect_position_volatility(features)

    assert signal.detected is True
    assert signal.confidence == pytest.approx(0.72)