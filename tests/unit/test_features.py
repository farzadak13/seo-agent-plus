import pytest
from pydantic import ValidationError

from app.models.features import (
    CTRFeatures,
    DataQualityMatrix,
    FeatureSet,
    PositionFeatures,
    VisibilityFeatures,
    VolumeFeatures,
)


@pytest.fixture
def valid_feature_set() -> FeatureSet:
    return FeatureSet(
        volume=VolumeFeatures(
            current_impressions=1900,
            current_clicks=40,
            baseline_impressions=2000,
            baseline_clicks=100,
        ),
        ctr=CTRFeatures(
            baseline_ctr=0.05,
            current_ctr=0.02,
            absolute_delta=-0.03,
            relative_change=-0.60,
            baseline_ctr_zero=False,
        ),
        position=PositionFeatures(
            baseline_median=4.1,
            current_median=4.2,
            delta=0.1,
            mad_current=0.3,
            trend_slope=0.01,
            trend_direction="stable",
        ),
        visibility=VisibilityFeatures(
            query_impressions=1900,
            url_total_impressions=2500,
            query_visibility_share=0.76,
        ),
        data_quality=DataQualityMatrix(
            url_level_completeness=1.0,
            query_level_completeness=1.0,
            current_window_completeness=1.0,
            baseline_window_completeness=1.0,
            reconciliation_completeness=1.0,
        ),
    )

def test_data_quality_matrix_accepts_reconciliation_completeness():
    matrix = DataQualityMatrix(
        url_level_completeness=1.0,
        query_level_completeness=0.9,
        current_window_completeness=0.8,
        baseline_window_completeness=0.95,
        reconciliation_completeness=0.75,
    )

    assert matrix.reconciliation_completeness == 0.75
def test_valid_feature_set(valid_feature_set: FeatureSet) -> None:
    assert valid_feature_set.volume.current_impressions == 1900
    assert valid_feature_set.ctr.current_ctr == 0.02
    assert valid_feature_set.position.current_median == 4.2
    assert valid_feature_set.visibility.query_visibility_share == 0.76


@pytest.mark.parametrize(
    "field_name",
    [
        "url_level_completeness",
        "query_level_completeness",
        "current_window_completeness",
        "baseline_window_completeness",
        "reconciliation_completeness",
    ],
)
def test_data_quality_must_be_between_zero_and_one(
    field_name: str,
) -> None:
    values = {
        "url_level_completeness": 1.0,
        "query_level_completeness": 1.0,
        "current_window_completeness": 1.0,
        "baseline_window_completeness": 1.0,
        "reconciliation_completeness": 1.0,
    }

    values[field_name] = 1.1

    with pytest.raises(ValidationError):
        DataQualityMatrix(**values)
def test_negative_impressions_are_rejected() -> None:
    with pytest.raises(ValidationError):
        VolumeFeatures(
            current_impressions=-1,
            current_clicks=10,
            baseline_impressions=100,
            baseline_clicks=10,
        )


def test_negative_clicks_are_rejected() -> None:
    with pytest.raises(ValidationError):
        VolumeFeatures(
            current_impressions=100,
            current_clicks=-1,
            baseline_impressions=100,
            baseline_clicks=10,
        )


def test_ctr_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValidationError):
        CTRFeatures(
            baseline_ctr=1.2,
            current_ctr=0.2,
            absolute_delta=-1.0,
            relative_change=-0.5,
        )


def test_position_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        PositionFeatures(
            baseline_median=-1.0,
            current_median=4.2,
            delta=5.2,
            mad_current=0.3,
            trend_slope=0.1,
            trend_direction="stable",
        )


def test_position_trend_direction_is_restricted() -> None:
    with pytest.raises(ValidationError):
        PositionFeatures(
            baseline_median=4.0,
            current_median=4.2,
            delta=0.2,
            mad_current=0.3,
            trend_slope=0.1,
            trend_direction="random",
        )


def test_visibility_share_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValidationError):
        VisibilityFeatures(
            query_impressions=100,
            url_total_impressions=1000,
            query_visibility_share=1.5,
        )


def test_extra_feature_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        VolumeFeatures(
            current_impressions=100,
            current_clicks=10,
            baseline_impressions=100,
            baseline_clicks=10,
            unexpected_field=123,
        )


def test_feature_set_requires_all_main_sections() -> None:
    with pytest.raises(ValidationError):
        FeatureSet(
            volume=VolumeFeatures(
                current_impressions=100,
                current_clicks=10,
                baseline_impressions=100,
                baseline_clicks=10,
            ),
        )