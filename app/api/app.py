from __future__ import annotations

from contextlib import nullcontext
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.auth import APIKeyAuthenticator
from app.api.schemas import (
    AvailableGSCPropertiesRequest,
    AvailableGSCPropertiesResponse,
    ConfigureGSCRequest,
    ConfigureSiteAdapterRequest,
    CreateJobRequest,
    CreateRunRequest,
    CreateSiteRequest,
    JobResponse,
    RunResponse,
    SiteResponse,
)
from app.jobs.scheduler import JobScheduler
from app.models.jobs import Job
from app.models.runs import SEORun
from app.models.sites import (
    GSCConnectionConfig,
    SecretProvider,
    SecretRef,
    Site,
    SiteAdapterConnection,
)
from app.onboarding.site_store import SiteStore
from app.persistence.contracts import PersistenceNotFoundError
from app.runs.handler import SEO_RUN_JOB_TYPE
from app.runs.store import RunStore


class APIDependencies:
    def __init__(
        self,
        *,
        scheduler: JobScheduler,
        authenticator: APIKeyAuthenticator,
        job_id_factory: Callable[[], str] | None = None,
        site_store: SiteStore | None = None,
        run_store: RunStore | None = None,
        site_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        transaction_factory=None,
        adapter_factory=None,
        gsc_property_lister=None,
    ) -> None:
        self.transaction_factory = transaction_factory or nullcontext
        self.adapter_factory = adapter_factory
        # None means we cannot ask Google which properties exist (stub mode).
        # The property is then taken on trust, as it was before.
        self.gsc_property_lister = gsc_property_lister
        self.scheduler = scheduler
        self.authenticator = authenticator
        self.job_id_factory = job_id_factory or (lambda: f"job-{uuid4().hex}")
        self.site_store = site_store
        self.run_store = run_store
        self.site_id_factory = site_id_factory or (lambda: f"site-{uuid4().hex[:16]}")
        self.run_id_factory = run_id_factory or (lambda: f"run-{uuid4().hex}")


def create_app(dependencies: APIDependencies) -> FastAPI:
    app = FastAPI(title="AI SEO Agent API", version="0.31.0")
    app.state.dependencies = dependencies

    def principal_id(
        credentials: HTTPAuthorizationCredentials | None = Depends(
            dependencies.authenticator.scheme
        ),
    ) -> str:
        return dependencies.authenticator.authenticate(credentials)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_job(
        request: CreateJobRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> JobResponse:
        job = Job(
            job_id=dependencies.job_id_factory(),
            job_type=request.job_type,
            principal_id=authenticated_principal_id,
            payload=dict(request.payload),
            max_attempts=request.max_attempts,
        )
        try:
            dependencies.scheduler.store.create(job)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Unable to create job.",
            ) from exc
        return JobResponse.from_job(job)

    @app.get("/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(
        job_id: str,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> JobResponse:
        job = _get_job(dependencies.scheduler, job_id)
        _ensure_owner(job.principal_id, authenticated_principal_id)
        return JobResponse.from_job(job)

    @app.post("/v1/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(
        job_id: str,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> JobResponse:
        job = _get_job(dependencies.scheduler, job_id)
        _ensure_owner(job.principal_id, authenticated_principal_id)
        try:
            cancelled = job.cancel()
            dependencies.scheduler.store.update(cancelled)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return JobResponse.from_job(cancelled)

    @app.post("/v1/sites", response_model=SiteResponse, status_code=status.HTTP_201_CREATED)
    def create_site(
        request: CreateSiteRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> SiteResponse:
        store = _require_site_store(dependencies)
        site = Site(
            site_id=dependencies.site_id_factory(),
            principal_id=authenticated_principal_id,
            name=request.name,
            base_url=request.base_url,
        )
        try:
            store.create(site)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Unable to create site.",
            ) from exc
        return SiteResponse.from_site(site)

    @app.get("/v1/sites/{site_id}", response_model=SiteResponse)
    def get_site(
        site_id: str,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> SiteResponse:
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        return SiteResponse.from_site(site)

    @app.post(
        "/v1/sites/{site_id}/connections/gsc/available",
        response_model=AvailableGSCPropertiesResponse,
    )
    def available_gsc_properties(
        site_id: str,
        request: AvailableGSCPropertiesRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> AvailableGSCPropertiesResponse:
        """List the properties this credential can see, so nothing is typed.

        A property string has to match Google's byte for byte. Typing it is
        the step that produces a 403 later and sends the customer off to
        re-grant access they already had.
        """
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        if dependencies.gsc_property_lister is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Search Console is not configured for this runtime.",
            )
        try:
            properties = dependencies.gsc_property_lister(
                auth_mode=request.auth_mode, credential_ref=request.credential_ref
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        return AvailableGSCPropertiesResponse.from_properties(properties)

    @app.put("/v1/sites/{site_id}/connections/gsc", response_model=SiteResponse)
    def configure_gsc(
        site_id: str,
        request: ConfigureGSCRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> SiteResponse:
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        if dependencies.gsc_property_lister is not None:
            # Refuse a property Google does not report, rather than storing it
            # and failing on the first run with a 403 that names nothing.
            try:
                properties = dependencies.gsc_property_lister(
                    auth_mode=request.auth_mode, credential_ref=request.credential_ref
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
                ) from exc
            chosen = next(
                (item for item in properties if item.matches(request.property_url)), None
            )
            if chosen is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": (
                            "This credential cannot see that property. It must match "
                            "exactly, including the trailing slash."
                        ),
                        "requested": request.property_url,
                        "available": [item.site_url for item in properties],
                    },
                )
            if not chosen.readable:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": (
                            "That property is listed but not readable with this "
                            "credential, so every run would fail. Verify it in "
                            "Search Console first."
                        ),
                        "requested": request.property_url,
                        "permission_level": chosen.permission_level,
                    },
                )
        updated = site.model_copy(
            update={
                "gsc": GSCConnectionConfig(
                    property_url=request.property_url,
                    credential_ref=SecretRef(
                        provider=SecretProvider.ENVIRONMENT,
                        key=request.credential_ref,
                    ),
                    auth_mode=request.auth_mode,
                    row_limit=request.row_limit,
                )
            }
        )
        return SiteResponse.from_site(_require_site_store(dependencies).update(updated))

    @app.put("/v1/sites/{site_id}/connections/site-adapter", response_model=SiteResponse)
    def configure_site_adapter(
        site_id: str,
        request: ConfigureSiteAdapterRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> SiteResponse:
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        refs = {
            key: SecretRef(provider=SecretProvider.ENVIRONMENT, key=value)
            for key, value in request.secret_refs.items()
        }
        updated = site.model_copy(
            update={
                "site_adapter": SiteAdapterConnection(
                    adapter_type=request.adapter_type,
                    config=dict(request.config),
                    secret_refs=refs,
                )
            }
        )
        if dependencies.adapter_factory is not None:
            try:
                dependencies.adapter_factory.build(updated)
            except Exception as exc:
                raise HTTPException(status_code=422, detail="Invalid adapter configuration or missing credential reference.") from exc
        return SiteResponse.from_site(_require_site_store(dependencies).update(updated))

    @app.get("/v1/sites/{site_id}/capabilities")
    def site_capabilities(site_id: str, authenticated_principal_id: str = Depends(principal_id)):
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        if dependencies.adapter_factory is None:
            raise HTTPException(status_code=503, detail="Adapter factory is not configured.")
        try:
            adapter = dependencies.adapter_factory.build(site)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Site adapter is not ready.") from exc
        return {"site_id": site_id, "adapter_id": adapter.adapter_id,
                "capabilities": sorted(cap.value for cap in adapter.capabilities)}

    @app.post(
        "/v1/sites/{site_id}/runs",
        response_model=RunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_run(
        site_id: str,
        request: CreateRunRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> RunResponse:
        if request.end_date < request.start_date:
            raise HTTPException(
                status_code=422,
                detail="end_date must be greater than or equal to start_date",
            )
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        if site.gsc is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="GSC is not configured for this site.",
            )

        run_store = _require_run_store(dependencies)
        job = Job(
            job_id=dependencies.job_id_factory(),
            job_type=SEO_RUN_JOB_TYPE,
            principal_id=authenticated_principal_id,
            payload={"run_id": dependencies.run_id_factory()},
            max_attempts=request.max_attempts,
        )
        run = SEORun(
            run_id=job.payload["run_id"],
            site_id=site_id,
            principal_id=authenticated_principal_id,
            job_id=job.job_id,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            normalized_url=request.normalized_url,
            normalized_query=request.normalized_query,
            candidate_id=request.candidate_id,
        )
        try:
            with dependencies.transaction_factory():
                run_store.create(run)
                dependencies.scheduler.store.create(job)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Unable to create SEO run.",
            ) from exc
        return RunResponse.from_run(run)

    @app.get("/v1/runs/{run_id}", response_model=RunResponse)
    def get_run(
        run_id: str,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> RunResponse:
        run = _get_run(dependencies, run_id)
        _ensure_owner(run.principal_id, authenticated_principal_id)
        return RunResponse.from_run(run)

    return app


def _require_site_store(dependencies: APIDependencies) -> SiteStore:
    if dependencies.site_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Site onboarding is not configured.",
        )
    return dependencies.site_store


def _require_run_store(dependencies: APIDependencies) -> RunStore:
    if dependencies.run_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Run management is not configured.",
        )
    return dependencies.run_store


def _get_job(scheduler: JobScheduler, job_id: str) -> Job:
    try:
        return scheduler.store.get(job_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        ) from exc


def _get_site(dependencies: APIDependencies, site_id: str) -> Site:
    store = _require_site_store(dependencies)
    try:
        return store.get(site_id)
    except PersistenceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site not found.",
        ) from exc


def _get_run(dependencies: APIDependencies, run_id: str) -> SEORun:
    store = _require_run_store(dependencies)
    try:
        return store.get(run_id)
    except PersistenceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found.",
        ) from exc


def _ensure_owner(owner_id: str, principal_id: str) -> None:
    if owner_id != principal_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found.",
        )

