"""Queuing a scan, by the customer's button or once a month on its own.

A site changes without anyone telling us: a plugin update, a new theme, a
page removed. Each site that has been scanned is scanned again a month after
its last finished scan, and the new scan knows what the last one found, so
what is new can be said.

The worker runs one job at a time in the order they were asked for, so a
monthly scan does delay whatever is queued after it by a minute or two. A
round queues few, one per tenant, and never for a tenant with a scan already
on its way; and the customer's button never waits for one: their own scan
takes its place.
While a monthly scan is on its way, and if it fails, the last finished scan
is what the customer sees and what everything else is built from
(SiteScanStore.completed).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from app.models.jobs import Job
from app.models.site_scan import ScanStatus, ScanTrigger, SiteScan
from app.scan.handler import SITE_SCAN_JOB_TYPE, issue_keys_of
from app.scan.store import SiteScanStore

log = logging.getLogger(__name__)

RESCAN_EVERY = timedelta(days=30)
RESCAN_ROUND_SECONDS = 3 * 3600
PER_ROUND = 6
# A scan that died with the server must not hold its site for ever.
STALE_AFTER = timedelta(minutes=15)
# A monthly scan that failed (the site was down, say) is tried again a day later,
# then two, four and eight days later: a site gone for good is not knocked on daily.
RETRY_FAILED_AFTER = timedelta(days=1)
MAX_RETRY_WAIT = timedelta(days=8)


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def queue_scan(
    *, scan_store: SiteScanStore, job_store, site, job_id: str, principal_id: str, now: datetime,
    trigger: ScanTrigger = ScanTrigger.CUSTOMER, transaction=nullcontext,
) -> SiteScan:
    done = scan_store.completed(site.site_id)
    job = Job(
        job_id=job_id,
        job_type=SITE_SCAN_JOB_TYPE,
        principal_id=principal_id,
        payload={"site_id": site.site_id, "job_id": job_id},
        max_attempts=2,
    )
    attempt = scan_store.find(site.site_id)
    failures = 0
    if trigger == ScanTrigger.MONTHLY and attempt is not None and attempt.trigger == ScanTrigger.MONTHLY and attempt.status == ScanStatus.FAILED:
        failures = attempt.monthly_failures + 1
    scan = SiteScan(
        site_id=site.site_id, tenant_id=site.principal_id, job_id=job_id, host=host_of(str(site.base_url)),
        requested_at=now, trigger=trigger, monthly_failures=failures,
        # None: nothing finished before, so nothing in this one is "new".
        previous_issues=issue_keys_of(done.issues) if done is not None else None,
    )
    with transaction():
        scan_store.save(scan)
        job_store.create(job)
    return scan


def _is_removed(site) -> bool:
    return getattr(site.status, "value", site.status) == "removed"


def _on_its_way(attempt: SiteScan | None, now: datetime) -> bool:
    return attempt is not None and attempt.status in {ScanStatus.QUEUED, ScanStatus.RUNNING} and now - attempt.requested_at < STALE_AFTER


class MonthlyRescan:
    def __init__(
        self,
        *,
        site_store,
        scan_store: SiteScanStore,
        job_store,
        job_id_factory: Callable[[], str],
        transaction=nullcontext,
        active: Callable[[str], bool] = lambda tenant_id: True,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        per_round: int = PER_ROUND,
        every: Callable[[str], timedelta] = lambda tenant_id: RESCAN_EVERY,
    ) -> None:
        self._sites = site_store
        self._scans = scan_store
        self._jobs = job_store
        self._job_id = job_id_factory
        self._transaction = transaction
        self._active = active
        self._clock = clock
        self._per_round = per_round
        self._every = every

    def due(self, site, now: datetime) -> datetime | None:
        """When the site's last finished scan was, if it is time for another; else None."""
        if _is_removed(site):
            return None
        done = self._scans.completed(site.site_id)
        if done is None:
            return None  # never finished one: the customer starts the first
        attempt = self._scans.find(site.site_id)
        if _on_its_way(attempt, now):
            return None
        if attempt is not None and attempt.status == ScanStatus.FAILED and attempt.trigger == ScanTrigger.MONTHLY:
            wait = min(RETRY_FAILED_AFTER * (2 ** min(attempt.monthly_failures, 3)), MAX_RETRY_WAIT)
            if now - attempt.requested_at < wait:
                return None
            return done.completed_at or done.requested_at
        last = done.completed_at or done.requested_at
        return last if now - last >= self._every(site.principal_id) else None

    def run_round(self) -> dict[str, int]:
        now = self._clock()
        due: list[tuple[datetime, object]] = []
        for site in self._sites.list_all():
            last = self.due(site, now)
            if last is not None:
                due.append((last, site))
        queued = 0
        tenants: set[str] = set()
        busy: dict[str, bool] = {}
        # The longest waiting first; one per tenant a round.
        for _, site in sorted(due, key=lambda item: item[0]):
            if queued >= self._per_round:
                break
            tenant = site.principal_id
            if tenant in tenants:
                continue
            if tenant not in busy:
                busy[tenant] = any(_on_its_way(scan, now) for scan in self._scans.for_tenant(tenant))
            if busy[tenant] or not self._active(tenant):
                continue
            # Read again: the customer may have pressed the button since the list was made.
            if self.due(site, now) is None:
                continue
            try:
                queue_scan(
                    scan_store=self._scans, job_store=self._jobs, site=site, job_id=self._job_id(),
                    principal_id=tenant, now=now, trigger=ScanTrigger.MONTHLY, transaction=self._transaction,
                )
            except Exception:
                log.exception("monthly rescan: could not queue %s", site.site_id)
                continue
            tenants.add(tenant)
            queued += 1
        return {"queued": queued, "due": len(due)}
