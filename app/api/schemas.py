from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.jobs import JobStatus
from app.models.runs import SEORunStatus
from app.models.sites import SiteStatus


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=1, ge=1, le=10)


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: str
    principal_id: str
    status: JobStatus
    attempt_count: int
    max_attempts: int
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_job(cls, job):
        # Keep the Stage30 public response contract stable. The internal
        # Job model also contains a payload field, but payload is not part
        # of JobResponse and must not be forwarded via model_dump().
        return cls(
            job_id=job.job_id,
            job_type=job.job_type,
            principal_id=job.principal_id,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            result=job.result,
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )


class CreateSiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    base_url: HttpUrl


class ConfigureGSCRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_url: HttpUrl
    credential_ref: str = Field(min_length=1, max_length=200)
    auth_mode: str = Field(default="access_token", min_length=1, max_length=50)
    row_limit: int = Field(default=25_000, ge=1, le=25_000)


class ConfigureSiteAdapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_type: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)


class SiteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str
    principal_id: str
    name: str
    base_url: HttpUrl
    status: SiteStatus
    gsc_configured: bool
    site_adapter_configured: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_site(cls, site):
        return cls(
            site_id=site.site_id,
            principal_id=site.principal_id,
            name=site.name,
            base_url=site.base_url,
            status=site.status,
            gsc_configured=site.gsc is not None,
            site_adapter_configured=site.site_adapter is not None,
            created_at=site.created_at,
            updated_at=site.updated_at,
        )


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    normalized_url: str = Field(min_length=1)
    normalized_query: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    max_attempts: int = Field(default=3, ge=1, le=10)

    def validate_date_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return self


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    site_id: str
    principal_id: str
    job_id: str
    status: SEORunStatus
    start_date: str
    end_date: str
    normalized_url: str
    normalized_query: str
    candidate_id: str
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_run(cls, run):
        return cls.model_validate(run.model_dump())
