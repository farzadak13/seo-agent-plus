from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType


class ActionType(StrEnum):
    OPTIMIZE_TITLE = "optimize_title"
    INVESTIGATE_CANNIBALIZATION = (
        "investigate_cannibalization"
    )
    IMPROVE_INTERNAL_LINKING = (
        "improve_internal_linking"
    )
    IMPROVE_CONTENT_DEPTH = "improve_content_depth"
    MONITOR = "monitor"


class ActionStatus(StrEnum):
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    WAITING_FOR_RECRAWL = "waiting_for_recrawl"
    MEASUREMENT_WINDOW_ACTIVE = (
        "measurement_window_active"
    )
    EVALUATING = "evaluating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    ROLLED_BACK = "rolled_back"


class ActionRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str

    strategy_id: str
    opportunity_id: str

    site_id: str

    normalized_url: str
    normalized_query: str

    strategy_type: StrategyType
    action_type: ActionType
    status: ActionStatus
    risk_level: ActionRiskLevel

    confidence_score: float = Field(
        ge=0,
        le=1,
    )

    expected_impact_score: float = Field(
        ge=0,
        le=1,
    )

    priority_score: float = Field(
        ge=0,
        le=1,
    )

    requires_approval: bool

    reasons: list[str]

    parameters: dict

    evidence: dict

    snapshot: SnapshotMetadata