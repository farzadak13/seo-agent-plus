from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LLMFailureType(StrEnum):
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    EMPTY_OUTPUT = "empty_output"


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class TitleCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    title: str
    rationale: str
    confidence_score: float = Field(
        ge=0,
        le=1,
    )


class TitleCandidatePayloadList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[TitleCandidatePayload]


class LLMFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_type: LLMFailureType
    message: str
    retryable: bool
    attempt_count: int