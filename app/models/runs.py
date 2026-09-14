from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.snapshots import SnapshotMetadata


class RunStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineRun(BaseModel):
    """Persisted lifecycle model for the existing decision pipeline run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    status: RunStatus = RunStatus.STARTED
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    completed_at: datetime | None = None
    snapshot: SnapshotMetadata
    raw_gsc_record_id: str | None = None
    decision_record_id: str | None = None
    error: str | None = None


class SEORunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SEORun(BaseModel):
    """Application-level lifecycle for an externally requested SEO run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=100)
    site_id: str = Field(min_length=1, max_length=50)
    principal_id: str = Field(min_length=1, max_length=100)
    job_id: str = Field(min_length=1, max_length=100)
    status: SEORunStatus = SEORunStatus.QUEUED
    start_date: str = Field(min_length=10, max_length=10)
    end_date: str = Field(min_length=10, max_length=10)
    normalized_url: str = Field(min_length=1)
    normalized_query: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def start(self) -> "SEORun":
        if self.status != SEORunStatus.QUEUED:
            raise ValueError("Only queued runs can start.")
        return self.model_copy(
            update={
                "status": SEORunStatus.RUNNING,
                "started_at": datetime.now(timezone.utc),
                "error": None,
            }
        )

    def complete(self, result: dict[str, Any]) -> "SEORun":
        if self.status != SEORunStatus.RUNNING:
            raise ValueError("Only running runs can complete.")
        return self.model_copy(
            update={
                "status": SEORunStatus.COMPLETED,
                "result": result,
                "error": None,
                "completed_at": datetime.now(timezone.utc),
            }
        )

    def fail(self, error: str) -> "SEORun":
        if self.status != SEORunStatus.RUNNING:
            raise ValueError("Only running runs can fail.")
        return self.model_copy(
            update={
                "status": SEORunStatus.FAILED,
                "result": None,
                "error": error.strip()[:2000] or "Run failed.",
                "completed_at": datetime.now(timezone.utc),
            }
        )
