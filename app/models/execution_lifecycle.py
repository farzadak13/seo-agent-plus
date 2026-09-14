from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.execution import ExecutionRequest, ExecutionResolution


class ExecutionResultStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    action_id: str
    adapter_id: str
    idempotency_key: str
    status: ExecutionResultStatus
    started_at: datetime
    completed_at: datetime
    error: str | None = None
    evidence: dict


class ActionExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    execution: ExecutionResult
    request: ExecutionRequest
    resolution: ExecutionResolution
    state_before: str
    state_after: str
