from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.snapshots import SnapshotMetadata


class SERPDecisionStatus(StrEnum):
    PASS = "pass"
    INVESTIGATE = "investigate"
    REJECT = "reject"


class SERPDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str

    investigation_id: str
    strategy_id: str
    opportunity_id: str

    status: SERPDecisionStatus

    confidence_score: float = Field(ge=0, le=1)

    reasons: list[str]

    evidence: dict

    snapshot: SnapshotMetadata