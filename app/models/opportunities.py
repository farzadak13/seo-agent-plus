from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.snapshots import SnapshotMetadata


class OpportunityType(StrEnum):
    CTR_RECOVERY = "ctr_recovery"
    POSITION_RECOVERY = "position_recovery"
    VISIBILITY_GROWTH = "visibility_growth"
    MIXED_RECOVERY = "mixed_recovery"


class OpportunityStatus(StrEnum):
    QUALIFIED = "qualified"
    REJECTED = "rejected"


class Opportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    candidate_id: str

    site_id: str
    normalized_url: str
    normalized_query: str

    opportunity_type: OpportunityType
    status: OpportunityStatus

    impact_score: float = Field(ge=0, le=1)
    confidence_score: float = Field(ge=0, le=1)
    business_value_score: float = Field(ge=0, le=1)
    traffic_potential_score: float = Field(ge=0, le=1)
    effort_score: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=1)

    priority_score: float = Field(ge=0, le=1)

    signal_types: list[str]
    risk_types: list[str]

    reasons: list[str]
    evidence: dict

    snapshot: SnapshotMetadata