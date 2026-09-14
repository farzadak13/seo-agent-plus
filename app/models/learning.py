from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.actions import ActionType
from app.models.outcomes import OutcomeMetric, OutcomeStatus
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType


class LearningMaturity(StrEnum):
    EXPLORATION = "exploration"
    ESTABLISHED = "established"


class FeedbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_id: str
    action_id: str
    strategy_id: str
    opportunity_id: str

    site_id: str
    normalized_url: str
    normalized_query: str

    strategy_type: StrategyType
    action_type: ActionType

    outcome_status: OutcomeStatus
    primary_metric: OutcomeMetric
    relative_change: float | None
    improved: bool | None
    regressed: bool | None

    snapshot: SnapshotMetadata
    evidence: dict


class StrategyPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_type: StrategyType
    action_type: ActionType
    primary_metric: OutcomeMetric

    sample_size: int = Field(ge=0)
    usable_sample_size: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    inconclusive_count: int = Field(ge=0)

    success_rate: float | None = Field(default=None, ge=0, le=1)
    average_relative_change: float | None = None
    improvement_rate: float | None = Field(default=None, ge=0, le=1)
    regression_rate: float | None = Field(default=None, ge=0, le=1)

    maturity: LearningMaturity
    confidence: float = Field(ge=0, le=1)

    feedback_ids: list[str]


class LearningSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    strategy_type: StrategyType
    action_type: ActionType
    primary_metric: OutcomeMetric

    maturity: LearningMaturity
    confidence: float = Field(ge=0, le=1)

    sample_size: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0, le=1)
    average_relative_change: float | None = None

    direction: str
    evidence: dict


class LearningContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_id: str
    min_samples_for_learning: int = Field(ge=1)
    performances: list[StrategyPerformance]
    signals: list[LearningSignal]
    evidence: dict

