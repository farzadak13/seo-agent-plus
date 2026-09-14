from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LLMCallStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class LLMCallTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str

    logical_call_id: str

    provider_id: str
    model: str

    prompt_version: str
    rule_version: str
    config_version: str

    started_at: datetime
    completed_at: datetime

    latency_ms: float = Field(
        ge=0,
    )

    attempt_count: int = Field(
        ge=1,
    )

    provider_switch_count: int = Field(
        ge=0,
    )

    status: LLMCallStatus

    failure_type: str | None = None

    input_message_count: int = Field(
        ge=0,
    )

    input_character_count: int = Field(
        ge=0,
    )

    output_character_count: int = Field(
        ge=0,
    )

    candidate_count: int = Field(
        ge=0,
    )

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

    evidence: dict