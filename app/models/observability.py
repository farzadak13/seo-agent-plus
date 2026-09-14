from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObservabilityEventType(StrEnum):
    JOB = "job"
    RUN = "run"
    PIPELINE = "pipeline"
    LLM = "llm"
    EXECUTION = "execution"
    SYSTEM = "system"


class ObservabilityLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ObservabilityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(min_length=1, max_length=100)
    event_type: ObservabilityEventType
    level: ObservabilityLevel = ObservabilityLevel.INFO
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = Field(min_length=1, max_length=100)
    trace_id: str = Field(min_length=1, max_length=100)
    span_id: str = Field(min_length=1, max_length=100)
    site_id: str | None = Field(default=None, max_length=50)
    job_id: str | None = Field(default=None, max_length=100)
    run_id: str | None = Field(default=None, max_length=100)
    parent_span_id: str | None = Field(default=None, max_length=100)
    action_id: str | None = Field(default=None, max_length=100)
    logical_call_id: str | None = Field(default=None, max_length=200)
    provider_id: str | None = Field(default=None, max_length=100)
    operation: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    attributes: dict[str, Any] = Field(default_factory=dict)


class CounterMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=100)
    value: int = Field(ge=0)
    labels: dict[str, str] = Field(default_factory=dict)


class DurationMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=0)
    total_ms: float = Field(ge=0)
    min_ms: float | None = Field(default=None, ge=0)
    max_ms: float | None = Field(default=None, ge=0)
    labels: dict[str, str] = Field(default_factory=dict)
    @property
    def average_ms(self) -> float:
        return 0.0 if self.count == 0 else self.total_ms / self.count

