from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.opportunities import OpportunityType
from app.models.snapshots import SnapshotMetadata


class StrategyType(StrEnum):
    SERP_TITLE_OPTIMIZATION = "serp_title_optimization"
    CANNIBALIZATION_INVESTIGATION = "cannibalization_investigation"
    INTERNAL_LINKING = "internal_linking"
    CONTENT_DEPTH_IMPROVEMENT = "content_depth_improvement"
    MONITOR_ONLY = "monitor_only"


class StrategyStatus(StrEnum):
    RECOMMENDED = "recommended"
    REJECTED = "rejected"


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    opportunity_id: str

    site_id: str
    normalized_url: str
    normalized_query: str

    opportunity_type: OpportunityType
    strategy_type: StrategyType
    status: StrategyStatus

    confidence_score: float = Field(ge=0, le=1)
    expected_impact_score: float = Field(ge=0, le=1)
    effort_score: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=1)
    priority_score: float = Field(ge=0, le=1)

    reasons: list[str]
    evidence: dict

    snapshot: SnapshotMetadata