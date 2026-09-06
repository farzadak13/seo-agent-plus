from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SignalType(StrEnum):
    CTR_DROP = "CTR_DROP"
    POSITION_DECLINE = "POSITION_DECLINE"
    HIGH_VALUE_OPPORTUNITY = "HIGH_VALUE_OPPORTUNITY"
    VOLATILITY = "VOLATILITY"
    CTR_DROP_UNDER_VOLATILITY = "CTR_DROP_UNDER_VOLATILITY"
    POSITION_DECLINE_UNDER_VOLATILITY = "POSITION_DECLINE_UNDER_VOLATILITY"


class SignalSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Signal(BaseModel):
    """
    Deterministic signal produced by a detector.

    A signal is evidence that a condition exists.
    It is NOT an SEO decision or an action recommendation.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    signal_type: SignalType
    detected: bool

    severity: SignalSeverity = SignalSeverity.INFO

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence that the detected condition is real.",
    )

    evidence: dict[str, object] = Field(
        default_factory=dict,
        description="Structured evidence supporting the signal.",
    )