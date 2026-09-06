from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DataQualityMatrix(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    url_level_completeness: float = Field(ge=0, le=1)
    query_level_completeness: float = Field(ge=0, le=1)
    current_window_completeness: float = Field(ge=0, le=1)
    baseline_window_completeness: float = Field(ge=0, le=1)
    reconciliation_completeness: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )


class VolumeFeatures(BaseModel):
    """
    Search Console volume metrics.
    """

    model_config = ConfigDict(extra="forbid")

    current_impressions: int = Field(ge=0)
    current_clicks: int = Field(ge=0)

    baseline_impressions: int = Field(ge=0)
    baseline_clicks: int = Field(ge=0)


class CTRFeatures(BaseModel):
    """
    CTR-related derived features.

    CTR is represented as a ratio:
        5% == 0.05
    """

    model_config = ConfigDict(extra="forbid")

    baseline_ctr: float = Field(ge=0.0, le=1.0)
    current_ctr: float = Field(ge=0.0, le=1.0)

    absolute_delta: float = Field(
        description="Current CTR minus baseline CTR.",
    )

    relative_change: float = Field(
        description=(
            "Relative CTR change. Undefined when baseline CTR is zero; "
            "in that case the extractor should return 0.0 and expose "
            "the zero-baseline condition separately."
        ),
    )

    baseline_ctr_zero: bool = Field(
        description=(
            "Whether the baseline CTR is exactly zero, making "
            "relative CTR change undefined."
        ),
    )


class PositionFeatures(BaseModel):
    """
    Search Console average-position derived features.
    """

    model_config = ConfigDict(extra="forbid")

    baseline_median: float = Field(ge=0.0)
    current_median: float = Field(ge=0.0)

    delta: float = Field(
        description="Current median position minus baseline median.",
    )

    mad_current: float = Field(
        ge=0.0,
        description="Median Absolute Deviation of daily positions.",
    )

    trend_slope: float = Field(
        description="Estimated linear trend of daily position values.",
    )

    trend_direction: Literal[
        "improving",
        "stable",
        "declining",
    ]


class VisibilityFeatures(BaseModel):
    """
    Relationship between a query and the total visibility of its URL.
    """

    model_config = ConfigDict(extra="forbid")

    query_impressions: int = Field(ge=0)
    url_total_impressions: int = Field(ge=0)

    query_visibility_share: float = Field(
        ge=0.0,
        le=1.0,
    )


class FeatureSet(BaseModel):
    """
    Complete deterministic feature vector used by Stage A detectors.

    This class is a data contract only.
    Feature calculation belongs to the feature extraction layer.
    """

    model_config = ConfigDict(extra="forbid")

    volume: VolumeFeatures
    ctr: CTRFeatures
    position: PositionFeatures
    visibility: VisibilityFeatures
    data_quality: DataQualityMatrix