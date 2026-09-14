from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(min_length=1)
    job_type: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    result: dict[str, Any] | None = None
    error: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1, le=10)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def transition_running(self) -> "Job":
        if self.status != JobStatus.QUEUED:
            raise ValueError("Only queued jobs can start running.")
        if self.attempt_count >= self.max_attempts:
            raise ValueError("Job has exhausted its maximum attempts.")
        return self.model_copy(
            update={
                "status": JobStatus.RUNNING,
                "attempt_count": self.attempt_count + 1,
                "started_at": datetime.now(timezone.utc),
                "error": None,
            }
        )

    def complete(self, result: dict[str, Any] | None = None) -> "Job":
        if self.status != JobStatus.RUNNING:
            raise ValueError("Only running jobs can complete.")
        return self.model_copy(
            update={
                "status": JobStatus.COMPLETED,
                "result": result or {},
                "error": None,
                "completed_at": datetime.now(timezone.utc),
            }
        )

    def retry_or_fail(self, error: str) -> "Job":
        """Requeue a failed attempt while attempts remain; otherwise fail terminally."""
        if self.status != JobStatus.RUNNING:
            raise ValueError("Only running jobs can be retried or failed.")

        normalized_error = error.strip() or "Job failed."
        if self.attempt_count < self.max_attempts:
            return self.model_copy(
                update={
                    "status": JobStatus.QUEUED,
                    "result": None,
                    "error": normalized_error[:2000],
                    "started_at": None,
                    "completed_at": None,
                }
            )

        return self.fail(normalized_error)

    def fail(self, error: str) -> "Job":
        if self.status != JobStatus.RUNNING:
            raise ValueError("Only running jobs can fail.")
        normalized_error = error.strip() or "Job failed."
        return self.model_copy(
            update={
                "status": JobStatus.FAILED,
                "result": None,
                "error": normalized_error[:2000],
                "completed_at": datetime.now(timezone.utc),
            }
        )

    def cancel(self) -> "Job":
        if self.status != JobStatus.QUEUED:
            raise ValueError("Only queued jobs can be cancelled.")
        return self.model_copy(
            update={
                "status": JobStatus.CANCELLED,
                "completed_at": datetime.now(timezone.utc),
            }
        )
