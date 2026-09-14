from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.actions import Action
from app.models.execution import ExecutionRequest
from app.models.site_adapter import (
    AdapterOperationResult,
    SitePage,
)


class ExecutionRunStatus(StrEnum):
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"


class ExecutionRunResult(BaseModel):
    """
    Result of one end-to-end action execution attempt.

    The result keeps the canonical Action, the resolved adapter,
    the execution request, and the adapter response together.
    """

    model_config = ConfigDict(extra="forbid")

    action: Action

    adapter_id: str

    request: ExecutionRequest

    status: ExecutionRunStatus

    operation_result: AdapterOperationResult | None = None
    page: SitePage | None = None

    error: str | None = None