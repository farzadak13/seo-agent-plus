from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.measurement import MeasurementResult
from app.models.snapshots import SnapshotMetadata


class OutcomeStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    INCONCLUSIVE = "inconclusive"


class OutcomeMetric(StrEnum):
    CTR = "ctr"
    POSITION = "position"
    CLICKS = "clicks"
    IMPRESSIONS = "impressions"


class MetricOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: OutcomeMetric
    baseline_value: float
    current_value: float
    absolute_delta: float
    relative_change: float | None
    direction: str
    improved: bool
    regressed: bool


class OutcomeEvaluationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_metric: OutcomeMetric
    min_relative_improvement: float = Field(ge=0)
    max_relative_regression: float = Field(ge=0)
    require_primary_metric: bool = True


class OutcomeEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: str
    action_id: str
    site_id: str
    normalized_url: str
    normalized_query: str

    status: OutcomeStatus
    primary_metric: OutcomeMetric
    primary_metric_outcome: MetricOutcome | None
    metric_outcomes: list[MetricOutcome]

    policy: OutcomeEvaluationPolicy
    measurement: MeasurementResult

    reasons: list[str]
    evidence: dict
    snapshot: SnapshotMetadata
