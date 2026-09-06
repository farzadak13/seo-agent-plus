from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.snapshots import SnapshotMetadata


class ReasoningStatus(StrEnum):
    READY = "ready"
    REJECTED = "rejected"


class TitleReasoningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str

    site_id: str
    normalized_url: str

    primary_query: str

    current_title: str | None

    target_position: int | None

    competitor_titles: list[str]

    competitor_title_length_median: float | None
    competitor_title_length_average: float | None

    title_length_gap_vs_competitors: float | None

    confidence_score: float = Field(
        ge=0,
        le=1,
    )

    constraints: list[str]

    evidence: dict

    snapshot: SnapshotMetadata


class TitleReasoningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str

    title: str

    rationale: str

    confidence_score: float = Field(
        ge=0,
        le=1,
    )


class TitleReasoningResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str

    status: ReasoningStatus

    candidates: list[TitleReasoningCandidate]

    selected_candidate_id: str | None

    reasons: list[str]

    evidence: dict

    snapshot: SnapshotMetadata