import pytest

from app.detectors.high_value import detect_high_value_opportunity
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
    impressions: int,
    current_ctr: float,
    baseline_ctr: float,
    position: float,
    absolute_delta: float,
    baseline_completeness: float = 1.0,
    current_completeness: float = 1.0,
) -> FeatureSet:
    return FeatureSet(
        volume=VolumeFeatures(
            current_impressions=impressions,
            current_clicks=100,
            baseline_impressions=1000,
            baseline_clicks=100,
        ),
        ctr=CTRFeatures(
            baseline_ctr=baseline_ctr,
            current_ctr=current_ctr,
            absolute_delta=absolute_delta,
            relative_change=0.0,
            baseline_ctr_zero=False,
        ),
        position=PositionFeatures(
            baseline_median=position,
            current_median=position,
            delta=0.0,
            mad_current=0.5,
            trend_slope=0.0,
            trend_direction="stable",
        ),
        visibility=VisibilityFeatures(
            query_impressions=impressions,
            url_total_impressions=impressions * 2,
            query_visibility_share=0.5,
        ),
        data_quality=DataQualityMatrix(
            url_level_completeness=1.0,
            query_level_completeness=1.0,
            current_window_completeness=current_completeness,
            baseline_window_completeness=baseline_completeness,
        ),
    )


def test_detects_high_value_opportunity():
    features = make_features(
        impressions=1000,
        current_ctr=0.04,
        baseline_ctr=0.08,
        position=7.0,
        absolute_delta=-0.04,
    )

    signal = detect_high_value_opportunity(features)

    assert signal.signal_type.value == "HIGH_VALUE_OPPORTUNITY"
    assert signal.detected is True
    assert signal.severity.value == "warning"
    assert signal.confidence == pytest.approx(1.0)
    assert signal.evidence["reason"] == "high_value_opportunity"
    assert signal.evidence["ctr_decline_support"] is True


def test_low_impressions_are_not_opportunity():
    features = make_features(
        impressions=99,
        current_ctr=0.03,
        baseline_ctr=0.08,
        position=7.0,
        absolute_delta=-0.05,
    )

    signal = detect_high_value_opportunity(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "insufficient_impressions"


@pytest.mark.parametrize(
    "position",
    [3.9, 20.1],
)
def test_position_outside_range_is_not_opportunity(position):
    features = make_features(
        impressions=1000,
        current_ctr=0.03,
        baseline_ctr=0.08,
        position=position,
        absolute_delta=-0.05,
    )

    signal = detect_high_value_opportunity(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "position_outside_opportunity_range"


def test_high_ctr_is_not_opportunity():
    features = make_features(
        impressions=1000,
        current_ctr=0.09,
        baseline_ctr=0.08,
        position=7.0,
        absolute_delta=0.01,
    )

    signal = detect_high_value_opportunity(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "ctr_not_low_enough"


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
        impressions=1000,
        current_ctr=0.03,
        baseline_ctr=0.08,
        position=7.0,
        absolute_delta=-0.05,
        baseline_completeness=baseline_completeness,
        current_completeness=current_completeness,
    )

    signal = detect_high_value_opportunity(features)

    assert signal.detected is False
    assert signal.evidence["reason"] == "insufficient_data_completeness"


def test_opportunity_without_ctr_decline_is_still_detected():
    features = make_features(
        impressions=1000,
        current_ctr=0.04,
        baseline_ctr=0.04,
        position=7.0,
        absolute_delta=0.0,
    )

    signal = detect_high_value_opportunity(features)

    assert signal.detected is True
    assert signal.severity.value == "info"
    assert signal.evidence["ctr_decline_support"] is False


def test_thresholds_are_configurable():
    features = make_features(
        impressions=500,
        current_ctr=0.04,
        baseline_ctr=0.08,
        position=7.0,
        absolute_delta=-0.04,
    )

    signal = detect_high_value_opportunity(
        features,
        min_impressions=1000,
        max_current_ctr=0.05,
    )

    assert signal.detected is False
    assert signal.evidence["reason"] == "insufficient_impressions"