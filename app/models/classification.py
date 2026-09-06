from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ClassificationStatus(StrEnum):
    PASS = "PASS"
    INVESTIGATE = "INVESTIGATE"
    REJECT = "REJECT"


class ClassificationResult(BaseModel):
    """
    Result of deterministic classification.

    Classification determines whether a case should proceed
    to deeper investigation. It does not define the final SEO action.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    status: ClassificationStatus

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reasons: list[str] = Field(
        default_factory=list,
    )

    signal_count: int = Field(
        ge=0,
    )

    risk_count: int = Field(
        ge=0,
    )