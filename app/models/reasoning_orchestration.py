from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.reasoning import TitleReasoningResult
from app.models.serp_decision import SERPDecision
from app.models.serp import SERPInvestigation
from app.models.snapshots import SnapshotMetadata
from app.models.title_recommendation import (
    TitleRecommendation,
)


class ReasoningOrchestrationStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class TitleReasoningOrchestrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orchestration_id: str

    status: ReasoningOrchestrationStatus

    investigation: SERPInvestigation
    decision: SERPDecision
    recommendation: TitleRecommendation

    reasoning: TitleReasoningResult | None

    provider_id: str | None

    reasons: list[str]

    snapshot: SnapshotMetadata