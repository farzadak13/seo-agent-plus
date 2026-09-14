from datetime import date as Date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.snapshots import SnapshotMetadata


class MeasurementStatus(StrEnum):
    READY = "ready"
    INSUFFICIENT_DATA = "insufficient_data"
    INVALID = "invalid"


class MeasurementMetric(StrEnum):
    CTR = "ctr"
    POSITION = "position"
    CLICKS = "clicks"
    IMPRESSIONS = "impressions"


class MeasurementWindowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: Date
    end_date: Date
    expected_days: int = Field(ge=1)
    known_days: int = Field(ge=0)
    observed_days: int = Field(ge=0)
    completeness: float = Field(ge=0, le=1)
    observation_count: int = Field(ge=0)
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    ctr: float = Field(ge=0, le=1)
    median_position: float | None = Field(default=None, ge=0)


class MetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: MeasurementMetric
    baseline_value: float
    current_value: float
    absolute_delta: float
    relative_change: float | None


class MeasurementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_id: str
    action_id: str
    site_id: str
    normalized_url: str
    normalized_query: str

    status: MeasurementStatus

    baseline: MeasurementWindowSummary
    current: MeasurementWindowSummary

    comparisons: list[MetricComparison]

    reasons: list[str]
    evidence: dict

    snapshot: SnapshotMetadata
    evaluated_at: datetime
