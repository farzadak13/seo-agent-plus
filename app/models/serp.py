from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SERPInvestigationStatus(StrEnum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class SERPResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    url: str
    title: str
    snippet: str | None = None
    is_target: bool = False


class SERPQuerySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    provider: str
    response_id: str
    fetched_at: datetime
    results: list[SERPResultItem]


class SERPQueryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    target_found: bool
    target_position: int | None

    top_results_count: int
    competitor_count: int

    competitor_titles: list[str]

    target_title: str | None
    target_title_length: int | None

    competitor_title_lengths: list[int]

    competitor_title_length_median: float | None
    competitor_title_length_average: float | None

    title_length_gap_vs_median: float | None


class SERPInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: str

    strategy_id: str
    opportunity_id: str
    site_id: str
    normalized_url: str

    status: SERPInvestigationStatus

    primary_queries: list[str]

    query_evidence: list[SERPQueryEvidence]

    target_title: str | None

    target_found_in_any_query: bool
    target_best_position: int | None

    evidence: dict