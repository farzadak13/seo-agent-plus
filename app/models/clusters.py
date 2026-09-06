from pydantic import BaseModel, ConfigDict, Field


class CandidateCluster(BaseModel):
    """
    Deterministic grouping of candidates.

    A cluster is a grouping mechanism, not a root-cause diagnosis.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    cluster_id: str = Field(min_length=1)

    fingerprint: str = Field(min_length=1)

    candidate_ids: list[str] = Field(
        min_length=1,
    )

    candidate_count: int = Field(
        ge=1,
    )

    representative_priority_score: float = Field(
        ge=0.0,
    )