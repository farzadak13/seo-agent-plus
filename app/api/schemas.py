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

    property_url: str = Field(min_length=1, max_length=2000)
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
    # Echoed back exactly as stored, so a UI can show which property is
    # connected without the customer having to remember how they spelled it.
    gsc_property_url: str | None = None
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
            gsc_property_url=site.gsc.property_url if site.gsc else None,
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


class AvailableGSCPropertiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_ref: str = Field(min_length=1, max_length=200)
    auth_mode: str = Field(default="service_account", min_length=1, max_length=50)


class GSCPropertyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The exact string to send back when configuring. Never edit it.
    site_url: str
    display_name: str
    permission_level: str
    is_domain_property: bool
    readable: bool


class AvailableGSCPropertiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    properties: list[GSCPropertyResponse]

    @classmethod
    def from_properties(cls, properties) -> "AvailableGSCPropertiesResponse":
        return cls(
            properties=[
                GSCPropertyResponse(
                    site_url=item.site_url,
                    display_name=item.display_name,
                    permission_level=item.permission_level,
                    is_domain_property=item.is_domain_property,
                    readable=item.readable,
                )
                for item in properties
            ]
        )


class ApproveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional: approve a reviewer's edit in place of the proposed title.
    title: str | None = Field(default=None, min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=1000)


class ActionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class ActionEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    status: str
    actor: str
    reason: str
    detail: dict[str, Any]


class AppliedChangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str
    change_id: str | None
    previous_value: str
    new_value: str
    applied_at: datetime


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    site_id: str
    run_id: str
    proposal_id: str | None
    action_type: str
    status: str
    normalized_url: str
    normalized_query: str
    current_title: str | None
    proposed_title: str | None
    edited_by_reviewer: bool
    applied: AppliedChangeResponse | None
    rolled_back: AppliedChangeResponse | None
    error: str | None
    history: list[ActionEventResponse]
    created_at: datetime
    updated_at: datetime
    job_id: str | None = None

    @classmethod
    def from_managed(cls, managed, *, job_id: str | None = None):
        action = managed.action
        return cls(
            action_id=action.action_id,
            site_id=action.site_id,
            run_id=managed.run_id,
            proposal_id=managed.proposal_id,
            action_type=action.action_type.value,
            status=action.status.value,
            normalized_url=action.normalized_url,
            normalized_query=action.normalized_query,
            current_title=managed.observed_value,
            proposed_title=action.parameters.get("recommended_title"),
            edited_by_reviewer=bool(action.parameters.get("edited_by_reviewer")),
            applied=managed.applied.model_dump() if managed.applied else None,
            rolled_back=managed.rolled_back.model_dump() if managed.rolled_back else None,
            error=managed.error,
            history=[event.model_dump(mode="json") for event in managed.history],
            created_at=managed.created_at,
            updated_at=managed.updated_at,
            job_id=job_id,
        )


class ActionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ActionResponse]
    next_cursor: str | None
