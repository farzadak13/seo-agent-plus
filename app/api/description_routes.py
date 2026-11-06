"""Asking for a meta description for one scanned page."""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.account_routes import require_plan
from app.descriptions.handler import DESCRIPTION_JOB_TYPE, scanned_page
from app.models.actions import ActionStatus, ActionType
from app.models.execution import ExecutionCapability
from app.models.jobs import Job
from app.models.site_scan import ScanStatus
from app.scan.crawler import _identity

# Each is a model call: a ceiling a day per tenant.
DESCRIPTIONS_PER_TENANT_PER_DAY = 30
# A request whose proposal has not come back yet still holds its page.
IN_FLIGHT = timedelta(minutes=15)
_OPEN = frozenset({ActionStatus.AWAITING_APPROVAL, ActionStatus.APPROVED, ActionStatus.EXECUTING})


def _can_write_descriptions(dependencies, site) -> bool:
    """A site applied by hand always can; a connected one only if its connector says so."""
    try:
        adapter = dependencies.adapter_factory.build(site)
    except Exception:
        return False
    return getattr(adapter, "manual_apply", False) or ExecutionCapability.UPDATE_META_DESCRIPTION in set(adapter.capabilities)


class DescriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000)


def register_description_routes(app: FastAPI, dependencies, *, principal_id: Callable, owned_site: Callable) -> None:
    # Requests as they are made, before their job runs or if its model call
    # fails: a quick second click or a failure must not escape the limits.
    # The service is one process (the worker's lease), so memory is all of it;
    # a restart forgets, which only favours the customer for a day.
    requested: dict[str, list[tuple]] = {}
    lock = threading.Lock()

    @app.post("/v1/sites/{site_id}/meta-descriptions", status_code=status.HTTP_202_ACCEPTED)
    def ask_for_description(
        site_id: str, request: DescriptionRequest, authenticated_principal_id: str = Depends(principal_id)
    ) -> dict:
        site = owned_site(site_id, authenticated_principal_id)
        require_plan(dependencies, authenticated_principal_id)
        if dependencies.action_store is None or dependencies.scan_store is None or not dependencies.descriptions_enabled:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Meta descriptions are not configured.")
        if site.site_adapter is not None and not _can_write_descriptions(dependencies, site):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Update the HoshyarSEO plugin on your site to a version that can change meta descriptions.",
            )
        scan = dependencies.scan_store.find(site_id)
        page = scanned_page(scan if scan is not None and scan.status == ScanStatus.COMPLETED else None, request.url)
        if page is None:
            raise HTTPException(status_code=422, detail="That page is not one the scan read on this site.")
        now = dependencies.clock()
        mine, _ = dependencies.action_store.query(tenant_id=authenticated_principal_id, limit=200)
        descriptions = [item for item in mine if item.action.action_type == ActionType.OPTIMIZE_META_DESCRIPTION]
        wanted = _identity(page.url)
        if any(item.status in _OPEN and _identity(item.action.normalized_url) == wanted for item in descriptions):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A description for this page is already waiting in the inbox.")
        job_id = dependencies.job_id_factory()
        with lock:
            recent = [entry for entry in requested.get(authenticated_principal_id, []) if now - entry[0] < timedelta(days=1)]
            if any(url == wanted and now - at < IN_FLIGHT for at, url, _job in recent):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A description for this page is already waiting in the inbox.")
            made_today = sum(1 for item in descriptions if now - item.created_at < timedelta(days=1))
            if max(len(recent), made_today) >= DESCRIPTIONS_PER_TENANT_PER_DAY:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many descriptions asked for today.")
            requested[authenticated_principal_id] = [*recent, (now, wanted, job_id)]
        dependencies.scheduler.store.create(
            Job(
                job_id=job_id,
                job_type=DESCRIPTION_JOB_TYPE,
                principal_id=authenticated_principal_id,
                payload={"site_id": site.site_id, "url": page.url, "job_id": job_id, "tenant_id": site.principal_id},
                max_attempts=2,
            )
        )
        return {"job_id": job_id, "action_id": f"action:{job_id}"}
