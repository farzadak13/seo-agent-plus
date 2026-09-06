from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.snapshots import SnapshotMetadata


class TitleRecommendationStatus(StrEnum):
    RECOMMENDED = "recommended"
    REJECTED = "rejected"


class TitleRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str

    investigation_id: str
    decision_id: str

    strategy_id: str
    opportunity_id: str

    site_id: str
    normalized_url: str

    primary_query: str

    status: TitleRecommendationStatus

    current_title: str | None
    recommended_title: str | None

    current_title_length: int | None
    recommended_title_length: int | None

    competitor_title_length_median: float | None
    title_length_gap_vs_competitors: float | None

    confidence_score: float = Field(
        ge=0,
        le=1,
    )

    reasons: list[str]

    constraints: list[str]

    evidence: dict

    snapshot: SnapshotMetadata