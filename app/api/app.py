from __future__ import annotations

import re
from contextlib import nullcontext
from urllib.parse import parse_qs
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials

from app.api import oauth_pages
from app.api.auth import APIKeyAuthenticator
from app.action.approval import ApprovalError, approve, check_rollback, reject
from app.action.executor import EXECUTE_ACTION_JOB_TYPE, ROLLBACK_ACTION_JOB_TYPE
from app.action.store import ActionChangedError, ManagedActionStore
from app.api.schemas import (
    ActionDecisionRequest,
    ActionListResponse,
    ActionResponse,
    ApproveActionRequest,
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
from app.onboarding.ownership import meta_tag as ownership_meta_tag
from app.onboarding.ownership import new_token as new_ownership_token
from app.onboarding.google_oauth import BEGIN_PATH, CALLBACK_PATH, OAuthError, tenant_credential_ref
from app.onboarding.google_oauth import COOKIE_NAME as OAUTH_COOKIE
from app.onboarding.ownership import credential_is_shared, property_matches_site
from app.onboarding.site_store import SiteStore
from app.persistence.contracts import (
    InvalidCursorError,
    PersistenceConflictError,
    PersistenceNotFoundError,
)
from app.runs.handler import SEO_RUN_JOB_TYPE
from app.runs.store import RunStore


SITE_SECRET_ENV_PREFIX = "SEO_AGENT_SITE_SECRET_"
GOOGLE_ACCOUNT_AUTH_MODE = "oauth_refresh_token"

RESERVED_JOB_TYPES = frozenset(
    {SEO_RUN_JOB_TYPE, EXECUTE_ACTION_JOB_TYPE, ROLLBACK_ACTION_JOB_TYPE}
)


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
        ownership_verifier=None,
        action_store: ManagedActionStore | None = None,
        vault=None,
        google_oauth=None,
        tenant_name=None,
    ) -> None:
        # None: SEO_AGENT_GOOGLE_OAUTH_* not configured; only operator
        # credentials can connect Search Console.
        self.google_oauth = google_oauth
        # tenant_id -> display name, for the page that confirms which account
        # a Google sign-in is about to be connected to.
        self.tenant_name = tenant_name or (lambda tenant_id: tenant_id)
        self.action_store = action_store
        # None: no SEO_AGENT_SECRET_KEYS, so credentials can only be referenced
        # from the environment, as before.
        self.vault = vault
        self.transaction_factory = transaction_factory or nullcontext
        self.adapter_factory = adapter_factory
        # None means we cannot ask Google which properties exist (stub mode).
        # The property is then taken on trust, as it was before.
        self.gsc_property_lister = gsc_property_lister
        self.ownership_verifier = ownership_verifier
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

    @app.exception_handler(PersistenceConflictError)
    def conflict(_request, exc):
        # Two writes raced; the losing one is refused rather than allowed to
        # undo the other. Retrying with a fresh read is always safe.
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "This changed while the request was being handled; reload and try again."},
        )

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
        if request.job_type.strip() in RESERVED_JOB_TYPES:
            # These act on a stored run or action named in the payload. Their
            # own endpoints check who owns it; a raw job would not.
            raise HTTPException(
                status_code=422,
                detail="This job type is created through its own endpoint only.",
            )
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
        credential, auth_mode, lister_ref = _gsc_credential(
            dependencies, request, authenticated_principal_id
        )
        shared = _require_ownership_for_shared_credential(site, credential)
        if dependencies.gsc_property_lister is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Search Console is not configured for this runtime.",
            )
        try:
            properties = dependencies.gsc_property_lister(
                auth_mode=auth_mode, credential_ref=lister_ref
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        if shared:
            # The shared account sees every customer's property. Only this
            # site's own may reach this tenant, not even their names.
            properties = [
                item for item in properties
                if property_matches_site(item.site_url, str(site.base_url))
            ]
        return AvailableGSCPropertiesResponse.from_properties(properties)

    @app.put("/v1/sites/{site_id}/connections/gsc", response_model=SiteResponse)
    def configure_gsc(
        site_id: str,
        request: ConfigureGSCRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> SiteResponse:
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        credential, auth_mode, lister_ref = _gsc_credential(
            dependencies, request, authenticated_principal_id
        )
        shared = _require_ownership_for_shared_credential(site, credential)
        if shared:
            if not property_matches_site(request.property_url, str(site.base_url)):
                raise HTTPException(
                    status_code=422,
                    detail="The property must be this site's own domain.",
                )
        if dependencies.gsc_property_lister is not None:
            # Refuse a property Google does not report, rather than storing it
            # and failing on the first run with a 403 that names nothing.
            try:
                properties = dependencies.gsc_property_lister(
                    auth_mode=auth_mode, credential_ref=lister_ref
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
                ) from exc
            chosen = next(
                (item for item in properties if item.matches(request.property_url)), None
            )
            if shared:
                # The refusal below lists what is available; with the shared
                # account that must not include other customers' properties.
                properties = [
                    item for item in properties
                    if property_matches_site(item.site_url, str(site.base_url))
                ]
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
                    credential_ref=credential,
                    auth_mode=auth_mode,
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
        # A reference names an environment variable on this server. Any name
        # would let a customer have, say, the vault key or the database DSN
        # sent to their site as a "password"; only variables set aside for
        # site credentials can be referenced.
        foreign = sorted(
            value for value in request.secret_refs.values()
            if not value.startswith(SITE_SECRET_ENV_PREFIX)
        )
        if foreign:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Environment references must start with {SITE_SECRET_ENV_PREFIX}; "
                    "or send the credential itself in 'secrets'."
                ),
            )
        overlap = set(request.secrets) & set(request.secret_refs)
        if overlap:
            raise HTTPException(
                status_code=422,
                detail=f"Give each credential once, as a value or a reference: {sorted(overlap)}",
            )
        if request.secrets and dependencies.vault is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Credential storage is not configured on this server.",
            )
        refs = {
            key: SecretRef(provider=SecretProvider.ENVIRONMENT, key=value)
            for key, value in request.secret_refs.items()
        }
        with dependencies.transaction_factory():
            for name, value in request.secrets.items():
                if not re.fullmatch(r"[a-z_]{1,50}", name):
                    raise HTTPException(status_code=422, detail="Credential names are lower-case field names.")
                if not value.strip():
                    raise HTTPException(status_code=422, detail=f"{name} must not be empty.")
                refs[name] = dependencies.vault.put(
                    tenant_id=site.principal_id, site_id=site.site_id, name=name, value=value
                )
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
            saved = _require_site_store(dependencies).update(updated)
        return SiteResponse.from_site(saved)

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

    @app.post("/v1/sites/{site_id}/connections/site-adapter/check")
    def check_site_adapter(site_id: str, authenticated_principal_id: str = Depends(principal_id)):
        """Talk to the site once, so a wrong password shows up now, not at the first approval."""
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        if dependencies.adapter_factory is None:
            raise HTTPException(status_code=503, detail="Adapter factory is not configured.")
        try:
            adapter = dependencies.adapter_factory.build(site)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Site adapter is not ready.") from exc
        status_call = getattr(adapter, "status", None)
        if status_call is None:
            return {"site_id": site_id, "adapter_id": adapter.adapter_id, "ok": True,
                    "detail": "This adapter has no connection check."}
        try:
            detail = status_call()
        except Exception as exc:
            # The adapter's message names the cause (plugin missing, password
            # rejected) and never contains the credential itself.
            return {"site_id": site_id, "adapter_id": adapter.adapter_id, "ok": False,
                    "detail": str(exc)}
        return {"site_id": site_id, "adapter_id": adapter.adapter_id, "ok": True, "detail": detail}

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
        # Checked on every analysis, not only when connecting: a site set up
        # before ownership was required, or one whose property was changed by
        # other means, must not keep reading through the shared account.
        if _require_ownership_for_shared_credential(site, site.gsc.credential_ref):
            if not property_matches_site(site.gsc.property_url, str(site.base_url)):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The connected property is not this site's own domain.",
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

    @app.get("/v1/sites/{site_id}/ownership")
    def get_ownership(site_id: str, authenticated_principal_id: str = Depends(principal_id)):
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        if site.verification_token is None:
            site = _require_site_store(dependencies).update(
                site.model_copy(update={"verification_token": new_ownership_token()})
            )
        return _ownership_response(site)

    @app.post("/v1/sites/{site_id}/ownership/verify")
    def verify_ownership(site_id: str, authenticated_principal_id: str = Depends(principal_id)):
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        if dependencies.ownership_verifier is None:
            raise HTTPException(status_code=503, detail="Ownership verification is not configured.")
        method, reasons = dependencies.ownership_verifier.verify(site)
        if method is not None:
            site = _require_site_store(dependencies).update(
                site.model_copy(
                    update={
                        "ownership_method": method,
                        "ownership_verified_at": datetime.now(timezone.utc),
                    }
                )
            )
        return {**_ownership_response(site), "reasons": reasons}

    # Sign in with Google: the customer grants read-only Search Console
    # access on Google's own screen. See app.onboarding.google_oauth.

    @app.post("/v1/google/connect")
    def google_connect(authenticated_principal_id: str = Depends(principal_id)):
        oauth = _require_google_oauth(dependencies)
        return {"connect_url": oauth.start(authenticated_principal_id)}

    @app.get("/v1/google/connection")
    def google_connection(authenticated_principal_id: str = Depends(principal_id)):
        oauth = _require_google_oauth(dependencies)
        connection = oauth.connection(authenticated_principal_id)
        if connection is None:
            return {"connected": False}
        return {
            "connected": True,
            "email": connection.email,
            "connected_at": connection.connected_at,
        }

    @app.delete("/v1/google/connection")
    def google_disconnect(authenticated_principal_id: str = Depends(principal_id)):
        oauth = _require_google_oauth(dependencies)
        try:
            revoked = oauth.disconnect(authenticated_principal_id)
        except OAuthError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        detached = []
        if dependencies.site_store is not None:
            # Sites reading through the revoked grant would fail on every run
            # with an error that names nothing; detach them so the dashboard
            # says plainly that Search Console needs connecting again. This
            # runs on every call, not only when a grant was just removed, so
            # a retry after a failure part-way through finishes the job.
            #
            # The grant's reference is the same for every connection a tenant
            # makes, so a sign-in completing alongside this call would put a
            # live grant behind it. Detach only while no connection is live,
            # checked again before each site.
            grant = tenant_credential_ref(authenticated_principal_id)
            for site in dependencies.site_store.list_for_tenant(authenticated_principal_id):
                if oauth.connection(authenticated_principal_id) is not None:
                    break
                if site.gsc is not None and site.gsc.credential_ref == grant:
                    dependencies.site_store.update(site.model_copy(update={"gsc": None}))
                    detached.append(site.site_id)
        return {"connected": False, "revoked": revoked, "sites_detached": detached}

    @app.get(BEGIN_PATH, include_in_schema=False)
    def google_begin_page(state: str = ""):
        oauth = _require_google_oauth(dependencies)
        try:
            tenant_id = oauth.preview(state)
        except OAuthError as exc:
            return oauth_pages.error_page(str(exc))
        return oauth_pages.confirm_page(dependencies.tenant_name(tenant_id), BEGIN_PATH, state)

    @app.post(BEGIN_PATH, include_in_schema=False)
    async def google_begin(http_request: Request):
        oauth = _require_google_oauth(dependencies)
        if http_request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            # Only our own confirmation page may press "continue". A page
            # elsewhere auto-submitting this form would skip the screen that
            # names the account being connected.
            return oauth_pages.error_page("This page can only be continued from HoshyarSEO itself.")
        form = parse_qs((await http_request.body()).decode("utf-8", errors="replace"))
        try:
            _, google_url, nonce = oauth.begin((form.get("state") or [""])[0])
        except OAuthError as exc:
            return oauth_pages.error_page(str(exc))
        response = RedirectResponse(google_url, status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            OAUTH_COOKIE, nonce, max_age=600, path="/v1/oauth/google",
            secure=True, httponly=True, samesite="lax",
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get(CALLBACK_PATH, include_in_schema=False)
    def google_callback(
        http_request: Request, state: str = "", code: str | None = None, error: str | None = None
    ):
        oauth = _require_google_oauth(dependencies)
        try:
            connection = oauth.complete(
                state_id=state,
                code=code,
                error=error,
                browser_nonce=http_request.cookies.get(OAUTH_COOKIE),
            )
        except OAuthError as exc:
            page = oauth_pages.error_page(str(exc))
        else:
            page = oauth_pages.done_page(connection.email)
        page.delete_cookie(OAUTH_COOKIE, path="/v1/oauth/google")
        return page

    # Stage 37 — nothing reaches a customer's site without a person saying yes.

    @app.get("/v1/sites/{site_id}/actions", response_model=ActionListResponse)
    def list_actions(
        site_id: str,
        limit: int = 50,
        cursor: str | None = None,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> ActionListResponse:
        site = _get_site(dependencies, site_id)
        _ensure_owner(site.principal_id, authenticated_principal_id)
        store = _require_action_store(dependencies)
        try:
            actions, next_cursor = store.query(
                tenant_id=authenticated_principal_id,
                site_id=site_id,
                limit=max(1, min(limit, 200)),
                cursor=cursor,
            )
        except InvalidCursorError as exc:
            raise HTTPException(status_code=422, detail="Invalid cursor.") from exc
        return ActionListResponse(
            actions=[ActionResponse.from_managed(item) for item in actions],
            next_cursor=next_cursor,
        )

    @app.get("/v1/actions/{action_id}", response_model=ActionResponse)
    def get_action(
        action_id: str,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> ActionResponse:
        managed = _get_action(dependencies, action_id)
        _ensure_owner(managed.tenant_id, authenticated_principal_id)
        return ActionResponse.from_managed(managed)

    @app.post(
        "/v1/actions/{action_id}/approve",
        response_model=ActionResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def approve_action(
        action_id: str,
        request: ApproveActionRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> ActionResponse:
        managed = _get_action(dependencies, action_id)
        _ensure_owner(managed.tenant_id, authenticated_principal_id)
        site = _get_site(dependencies, managed.site_id)
        if site.site_adapter is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Connect the site (PUT /v1/sites/{site_id}/connections/site-adapter) before approving changes to it.",
            )
        try:
            approved = approve(
                managed,
                actor=authenticated_principal_id,
                title=request.title,
                note=request.note,
            )
        except ApprovalError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        job = _action_job(dependencies, EXECUTE_ACTION_JOB_TYPE, approved, authenticated_principal_id)
        _save_with_job(dependencies, approved, job)
        return ActionResponse.from_managed(approved, job_id=job.job_id)

    @app.post("/v1/actions/{action_id}/reject", response_model=ActionResponse)
    def reject_action(
        action_id: str,
        request: ActionDecisionRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> ActionResponse:
        managed = _get_action(dependencies, action_id)
        _ensure_owner(managed.tenant_id, authenticated_principal_id)
        try:
            rejected = reject(managed, actor=authenticated_principal_id, reason=request.reason)
        except ApprovalError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        try:
            _require_action_store(dependencies).update(rejected)
        except ActionChangedError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The action changed while this request was being handled; reload and try again.",
            ) from exc
        return ActionResponse.from_managed(rejected)

    @app.post(
        "/v1/actions/{action_id}/rollback",
        response_model=ActionResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def rollback_action(
        action_id: str,
        request: ActionDecisionRequest,
        authenticated_principal_id: str = Depends(principal_id),
    ) -> ActionResponse:
        managed = _get_action(dependencies, action_id)
        _ensure_owner(managed.tenant_id, authenticated_principal_id)
        try:
            check_rollback(managed)
        except ApprovalError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        requested = managed.record(
            action=managed.action,
            actor=authenticated_principal_id,
            reason="rollback_requested",
            detail={"note": request.reason} if request.reason else {},
        )
        job = _action_job(dependencies, ROLLBACK_ACTION_JOB_TYPE, requested, authenticated_principal_id)
        _save_with_job(dependencies, requested, job)
        return ActionResponse.from_managed(requested, job_id=job.job_id)

    return app


def _require_google_oauth(dependencies: APIDependencies):
    if dependencies.google_oauth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured on this server.",
        )
    return dependencies.google_oauth


def _gsc_credential(dependencies, request, tenant_id: str):
    """(credential, auth_mode, what to hand the property lister) for a request."""
    if request.use_google_account:
        oauth = dependencies.google_oauth
        if oauth is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Google sign-in is not configured on this server.",
            )
        connection = oauth.connection(tenant_id)
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Connect your Google account first (POST /v1/google/connect).",
            )
        return connection.credential_ref, GOOGLE_ACCOUNT_AUTH_MODE, connection.credential_ref
    if not request.credential_ref:
        raise HTTPException(
            status_code=422,
            detail="Give credential_ref, or set use_google_account after connecting Google.",
        )
    reference = SecretRef(provider=SecretProvider.ENVIRONMENT, key=request.credential_ref)
    return reference, request.auth_mode, request.credential_ref


def _ownership_response(site: Site) -> dict:
    return {
        "site_id": site.site_id,
        "verified": site.ownership_verified_at is not None,
        "method": site.ownership_method,
        "verified_at": site.ownership_verified_at,
        "meta_tag": ownership_meta_tag(site.verification_token) if site.verification_token else None,
    }


def _require_ownership_for_shared_credential(site: Site, credential: SecretRef | None = None) -> bool:
    """Require proof of ownership whenever the credential is the operator's.

    Every credential named from the server's environment is the operator's
    and so shared by every tenant, whatever kind it is; only a credential the
    tenant granted themselves (their own Google sign-in) is theirs alone.
    Returns True when the credential is shared.
    """
    if credential is not None and not credential_is_shared(credential):
        return False
    if site.ownership_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Prove this site is yours first (POST /v1/sites/{site_id}/ownership/verify): "
                "connect the HoshyarSEO Connector plugin, or add the verification meta tag."
            ),
        )
    return True


def _require_action_store(dependencies: APIDependencies) -> ManagedActionStore:
    if dependencies.action_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Action approval is not configured.",
        )
    return dependencies.action_store


def _get_action(dependencies: APIDependencies, action_id: str):
    store = _require_action_store(dependencies)
    try:
        return store.get(action_id)
    except PersistenceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found.",
        ) from exc


def _action_job(dependencies: APIDependencies, job_type: str, managed, principal: str) -> Job:
    return Job(
        job_id=dependencies.job_id_factory(),
        job_type=job_type,
        principal_id=principal,
        payload={"action_id": managed.action_id, "actor": principal},
        # A site that is briefly down should not turn an approval into a
        # failure; the handlers read the page first, so a retry is safe.
        max_attempts=3,
    )


def _save_with_job(dependencies: APIDependencies, managed, job: Job) -> None:
    """The decision and the job that carries it out are recorded together or not at all."""
    try:
        with dependencies.transaction_factory():
            _require_action_store(dependencies).update(managed)
            dependencies.scheduler.store.create(job)
    except PersistenceConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The action changed while this request was being handled; reload and try again.",
        ) from exc


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

