import pytest

from app.detectors.ctr import detect_ctr_drop
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
    baseline_ctr: float,
    current_ctr: float,
    absolute_delta: float,
    baseline_ctr_zero: bool = False,
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
            baseline_ctr=baseline_ctr,
            current_ctr=current_ctr,
            absolute_delta=absolute_delta,
            relative_change=0.0,
            baseline_ctr_zero=baseline_ctr_zero,
        ),
        position=PositionFeatures(
            baseline_median=5.0,
            current_median=5.0,
            delta=0.0,
            mad_current=0.0,
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


def test_detects_meaningful_ctr_drop():
    features = make_features(
        baseline_ctr=0.10,
        current_ctr=0.06,
        absolute_delta=-0.04,
    )

    signal = detect_ctr_drop(features)

    assert signal.signal_type.value == "CTR_DROP"
    assert signal.detected is True
    assert signal.severity.value == "warning"
    assert signal.confidence == pytest.approx(1.0)
    assert signal.evidence["reason"] == "meaningful_ctr_drop"


def test_small_ctr_drop_is_not_detected():
    features = make_features(
        baseline_ctr=0.10,
        current_ctr=0.09,
        absolute_delta=-0.01,
    )

    signal = detect_ctr_drop(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "drop_below_threshold"


def test_zero_baseline_ctr_is_not_detected():
    features = make_features(
        baseline_ctr=0.0,
        current_ctr=0.10,
        absolute_delta=0.10,
        baseline_ctr_zero=True,
    )

    signal = detect_ctr_drop(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "baseline_ctr_zero"


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
        baseline_ctr=0.10,
        current_ctr=0.05,
        absolute_delta=-0.05,
        baseline_completeness=baseline_completeness,
        current_completeness=current_completeness,
    )

    signal = detect_ctr_drop(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "insufficient_data_completeness"


def test_large_ctr_drop_is_critical():
    features = make_features(
        baseline_ctr=0.10,
        current_ctr=0.05,
        absolute_delta=-0.05,
    )

    signal = detect_ctr_drop(
        features,
        min_absolute_drop=0.02,
    )

    assert signal.detected is True
    assert signal.severity.value == "critical"


def test_threshold_is_configurable():
    features = make_features(
        baseline_ctr=0.10,
        current_ctr=0.07,
        absolute_delta=-0.03,
    )

    signal = detect_ctr_drop(
        features,
        min_absolute_drop=0.05,
    )

    assert signal.detected is False