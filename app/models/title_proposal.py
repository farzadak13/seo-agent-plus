from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.reasoning import TitleReasoningResult
from app.models.serp import SERPInvestigation
from app.models.serp_decision import SERPDecision
from app.models.snapshots import SnapshotMetadata
from app.models.title_recommendation import TitleRecommendation


class TitleProposalStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class TitleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)

    # Owner scope. Optional so a proposal built outside a tenant context stays
    # valid, but the composition root always supplies it from the site.
    tenant_id: str | None = None
    site_id: str = Field(min_length=1)
    normalized_url: str = Field(min_length=1)
    primary_query: str = Field(min_length=1)

    status: TitleProposalStatus

    recommendation: TitleRecommendation
    investigation: SERPInvestigation
    decision: SERPDecision

    reasoning: TitleReasoningResult | None = None
    selected_title: str | None = None

    provider_id: str | None = None
    reasons: list[str]
    evidence: dict

    snapshot: SnapshotMetadata
