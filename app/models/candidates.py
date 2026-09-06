from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from app.models.snapshots import SnapshotMetadata

class CandidateStatus(StrEnum):
    PASS = "PASS"
    INVESTIGATE = "INVESTIGATE"
    REJECT = "REJECT"


class Candidate(BaseModel):
    """
    Replayable SEO investigation candidate.

    A candidate represents an entity that may proceed to a deeper
    investigation stage. It does not define the final SEO strategy
    or action.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )
    candidate_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)

    normalized_url: str = Field(min_length=1)

    normalized_query: str = Field(min_length=1)

    status: CandidateStatus

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    priority_score: float = Field(
        ge=0.0,
    )

    signal_types: list[str] = Field(
        default_factory=list,
    )

    risk_types: list[str] = Field(
        default_factory=list,
    )

    reasons: list[str] = Field(
        default_factory=list,
    )

    snapshot: SnapshotMetadata