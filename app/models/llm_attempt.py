from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.llm import LLMFailureType
from app.models.reasoning_version import ReasoningVersion


class LLMAttemptStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class LLMAttemptTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str

    logical_call_id: str

    provider_id: str
    model: str

    attempt_number: int = Field(
        ge=1,
    )

    provider_switch_index: int = Field(
        default=0,
        ge=0,
    )

    started_at: datetime
    completed_at: datetime

    latency_ms: float = Field(
        ge=0,
    )

    status: LLMAttemptStatus

    failure_type: LLMFailureType | None = None

    prompt_version: str
    rule_version: str
    config_version: str

    input_tokens: int | None = Field(
        default=None,
        ge=0,
    )

    output_tokens: int | None = Field(
        default=None,
        ge=0,
    )

    total_tokens: int | None = Field(
        default=None,
        ge=0,
    )

    finish_reason: str | None = None

    evidence: dict
