from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.llm import LLMFailureType


class FallbackPolicyMode(StrEnum):
    DISABLED = "disabled"
    NEXT_PROVIDER = "next_provider"


class ProviderFallbackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: FallbackPolicyMode = (
        FallbackPolicyMode.NEXT_PROVIDER
    )

    max_provider_switches: int = Field(
        default=1,
        ge=0,
        le=10,
    )

    fallback_failure_types: list[
        LLMFailureType
    ] = [
        LLMFailureType.TIMEOUT,
        LLMFailureType.RATE_LIMIT,
        LLMFailureType.TRANSPORT,
    ]