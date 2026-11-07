"""The site scan and the two files it proposes, for a site with or without any data."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Response, status

from app.api.account_routes import require_plan
from app.models.jobs import Job
from app.models.site_files import SiteFileChange, SiteFileKind, SiteFileStatus
from app.models.site_scan import ScanStatus, ScanTrigger, SiteScan
from app.scan.handler import SITE_FILE_JOB_TYPE
from app.scan.monthly import host_of as _host_of
from app.scan.monthly import queue_scan

# Long enough that nobody hammers a site from our address by pressing a button;
# short enough to fix something and look again.
SCAN_COOLDOWN = timedelta(minutes=5)
# A job that died with the server must not keep the button off for ever.
SCAN_STALE_AFTER = timedelta(minutes=15)
# The worker runs one job at a time, and customers' approvals wait behind scans.
SCANS_PER_TENANT_PER_DAY = 20
CHECK_COOLDOWN = timedelta(seconds=30)
_WEB_PORTS = frozenset({None, 80, 443})


def register_scan_routes(app: FastAPI, dependencies, *, principal_id: Callable, owned_site: Callable) -> None:
    def scans():
        if dependencies.scan_store is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Site scans are not configured.")
        return dependencies.scan_store

    def files():
        if dependencies.file_store is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Site scans are not configured.")
        return dependencies.file_store

    def get_change(site_id: str, kind: SiteFileKind) -> SiteFileChange:
        change = files().find(site_id, kind)
        if change is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such proposal.")
        return change

    def check_scan_limits(site, host: str, now) -> None:
        mine = scans().for_tenant(site.principal_id)
        for other in mine:
            # A monthly scan still waiting gives way: the customer's own replaces
            # it, and its job finds another job id and stands down. One already
            # reading the site is let finish; it is a minute or two.
            if other.trigger == ScanTrigger.MONTHLY and other.status == ScanStatus.QUEUED:
                continue
            age = now - other.requested_at
            if other.status in {ScanStatus.QUEUED, ScanStatus.RUNNING} and age < SCAN_STALE_AFTER:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A scan of one of your sites is already running.")
            # By the site being read, not the record: ten records for one address
            # are still one address being read.
            if (other.host or "") == host and age < SCAN_COOLDOWN:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="This site was scanned a moment ago.")
        asked = [other for other in mine if other.trigger != ScanTrigger.MONTHLY]
        if sum(1 for other in asked if now - other.requested_at < timedelta(days=1)) >= SCANS_PER_TENANT_PER_DAY:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many sites scanned today.")

    @app.post("/v1/sites/{site_id}/scans", response_model=SiteScan, status_code=status.HTTP_202_ACCEPTED)
    def start_scan(site_id: str, authenticated_principal_id: str = Depends(principal_id)) -> SiteScan:
        site = owned_site(site_id, authenticated_principal_id)
        require_plan(dependencies, authenticated_principal_id)
        base_url = str(site.base_url)
        if urlsplit(base_url).port not in _WEB_PORTS:
            raise HTTPException(status_code=422, detail="Only sites on the usual web ports can be scanned.")
        now = dependencies.clock()
        host = _host_of(base_url)
        check_scan_limits(site, host, now)
        return queue_scan(
            scan_store=scans(), job_store=dependencies.scheduler.store, site=site,
            job_id=dependencies.job_id_factory(), principal_id=authenticated_principal_id, now=now,
            transaction=dependencies.transaction_factory,
        )

    @app.get("/v1/sites/{site_id}/scans/latest", response_model=SiteScan)
    def latest_scan(site_id: str, authenticated_principal_id: str = Depends(principal_id)) -> SiteScan:
        site = owned_site(site_id, authenticated_principal_id)
        scan = scans().find(site_id)
        done = scans().completed(site_id)
        # A scan the customer did not ask for is not theirs to wait on: until it
        # finishes, and if it fails, they see what the last one found.
        if scan is not None and scan.trigger == ScanTrigger.MONTHLY and scan.status != ScanStatus.COMPLETED and done is not None:
            # What went wrong with it is said beside the last finished scan.
            scan = done.model_copy(update={"last_attempt_error": scan.error if scan.status == ScanStatus.FAILED else None})
        if scan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This site has not been scanned yet.")
        billing = dependencies.billing
        # As app.scan.monthly.MonthlyRescan asks it.
        try:
            rescanned = billing is None or billing.entitlement(site.principal_id).active
        except Exception:  # one line of the page is not worth the page
            rescanned = None
        return scan.model_copy(update={"monthly_rescan": rescanned})

    @app.get("/v1/sites/{site_id}/site-files", response_model=list[SiteFileChange])
    def site_files(site_id: str, authenticated_principal_id: str = Depends(principal_id)) -> list[SiteFileChange]:
        owned_site(site_id, authenticated_principal_id)
        return files().for_site(site_id)

    @app.get("/v1/sites/{site_id}/site-files/{kind}/content")
    def site_file_content(
        site_id: str, kind: SiteFileKind, authenticated_principal_id: str = Depends(principal_id)
    ) -> Response:
        """The file itself, to save and put on the site."""
        owned_site(site_id, authenticated_principal_id)
        change = get_change(site_id, kind)
        name, media_type = ("robots.txt", "text/plain") if kind == SiteFileKind.ROBOTS else ("sitemap.xml", "application/xml")
        # Its text came partly from someone else's server, and is served from the
        # dashboard's own origin: a download, never a page or a script.
        return Response(
            content=change.proposed_text,
            media_type=f"{media_type}; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    def save(change: SiteFileChange, job: Job | None = None) -> SiteFileChange:
        with dependencies.transaction_factory():
            files().save(change)
            if job is not None:
                dependencies.scheduler.store.create(job)
        return change

    @app.post("/v1/sites/{site_id}/site-files/{kind}/approve", response_model=SiteFileChange)
    def approve_file(
        site_id: str, kind: SiteFileKind, authenticated_principal_id: str = Depends(principal_id)
    ) -> SiteFileChange:
        """Yes to the file: it is now the customer's to put on their site."""
        owned_site(site_id, authenticated_principal_id)
        change = get_change(site_id, kind)
        if change.status not in {SiteFileStatus.PROPOSED, SiteFileStatus.REJECTED}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file change cannot be approved now.")
        return save(
            change.record(status=SiteFileStatus.WAITING_FOR_CUSTOMER, actor=authenticated_principal_id, reason="approved", note=None)
        )

    @app.post("/v1/sites/{site_id}/site-files/{kind}/check", response_model=SiteFileChange, status_code=status.HTTP_202_ACCEPTED)
    def check_file(
        site_id: str, kind: SiteFileKind, authenticated_principal_id: str = Depends(principal_id)
    ) -> SiteFileChange:
        """The customer says the file is in place: go and look."""
        site = owned_site(site_id, authenticated_principal_id)
        change = get_change(site_id, kind)
        if change.status != SiteFileStatus.WAITING_FOR_CUSTOMER:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file change is not waiting to be checked.")
        reported = [event.at for event in change.history if event.reason == "customer_reported_applied"]
        if reported and dependencies.clock() - reported[-1] < CHECK_COOLDOWN:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="This was checked a moment ago.")
        job = Job(
            job_id=dependencies.job_id_factory(),
            job_type=SITE_FILE_JOB_TYPE,
            principal_id=site.principal_id,
            payload={"site_id": site.site_id, "kind": kind.value},
            max_attempts=2,
        )
        return save(change.record(actor=authenticated_principal_id, reason="customer_reported_applied"), job)

    @app.post("/v1/sites/{site_id}/site-files/{kind}/reject", response_model=SiteFileChange)
    def reject_file(
        site_id: str, kind: SiteFileKind, authenticated_principal_id: str = Depends(principal_id)
    ) -> SiteFileChange:
        owned_site(site_id, authenticated_principal_id)
        change = get_change(site_id, kind)
        if change.status not in {SiteFileStatus.PROPOSED, SiteFileStatus.WAITING_FOR_CUSTOMER}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file change cannot be rejected now.")
        return save(change.record(status=SiteFileStatus.REJECTED, actor=authenticated_principal_id, reason="rejected"))
