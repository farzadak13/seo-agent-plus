"""The two jobs: scan a site, and look whether an approved file is on it now."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.models.site_files import SiteFileChange, SiteFileKind, SiteFileStatus
from app.models.site_scan import ScanStatus
from app.onboarding.site_store import SiteStore
from app.scan.crawler import (
    MAX_ROBOTS_BYTES,
    MAX_SITEMAP_BYTES,
    Fetcher,
    SiteCrawler,
    _identity,
    default_fetcher,
    parse_sitemap,
    read_robots_answer,
)
from app.scan.files import FilePlan, plan_site_files, robots_can_be_judged, sitemap_can_be_judged
from app.scan.issues import build_issues
from app.scan.store import SiteFileStore, SiteScanStore

log = logging.getLogger(__name__)

SITE_SCAN_JOB_TYPE = "site_scan"
SITE_FILE_JOB_TYPE = "site_file"
SYSTEM_ACTOR = "system"

# Proposals a later scan may find out of date. REJECTED too: it can still be
# approved, and a file built on an old robots.txt would lose what came after.
_OPEN = frozenset({SiteFileStatus.PROPOSED, SiteFileStatus.WAITING_FOR_CUSTOMER, SiteFileStatus.REJECTED})
# Nothing new to say when the plan is the same: asking again would be nagging.
# Not APPLIED: a plan for a file that is meant to be there means it no longer is.
_SETTLED_ON_SAME_PLAN = frozenset({SiteFileStatus.PROPOSED, SiteFileStatus.WAITING_FOR_CUSTOMER, SiteFileStatus.REJECTED})


def _is_removed(site) -> bool:
    return getattr(site.status, "value", site.status) == "removed"


def build_site_scan_handler(
    *,
    site_store: SiteStore,
    scan_store: SiteScanStore,
    file_store: SiteFileStore,
    crawler_factory: Callable[[], SiteCrawler] = SiteCrawler,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        site_id = str(payload["site_id"])
        scan = scan_store.find(site_id)
        if scan is None or scan.status == ScanStatus.COMPLETED or scan.job_id != payload.get("job_id"):
            return {"site_id": site_id, "skipped": True}
        site = site_store.get(site_id)
        if _is_removed(site):
            # Over, not left queued, or it would count as running against the limits.
            scan_store.save(scan.model_copy(update={"status": ScanStatus.FAILED, "error": "site_removed"}))
            return {"site_id": site_id, "skipped": True}
        running = scan_store.save(scan.model_copy(update={"status": ScanStatus.RUNNING, "error": None}))
        base_url = str(site.base_url)
        try:
            result = crawler_factory().crawl(base_url)
            issues = build_issues(result)
            completed = scan_store.save(
                running.model_copy(
                    update={
                        "status": ScanStatus.COMPLETED,
                        "completed_at": clock(),
                        "robots_text": result.robots_text,
                        "robots_status": result.robots_status,
                        "sitemap_urls": result.sitemap_urls,
                        "sitemap_page_count": result.sitemap_page_count,
                        "pages": result.pages,
                        "pages_capped": result.pages_capped,
                        "issues": issues,
                    }
                )
            )
        except Exception as exc:
            scan_store.save(running.model_copy(update={"status": ScanStatus.FAILED, "error": type(exc).__name__}))
            raise
        _refresh_proposals(file_store, site, base_url, result, issues)
        return {"site_id": site_id, "pages": len(completed.pages), "issues": len(issues)}

    return handler


def _refresh_proposals(file_store: SiteFileStore, site, base_url: str, result, issues) -> None:
    sitemap = file_store.find(site.site_id, SiteFileKind.SITEMAP)
    declined = sitemap is not None and sitemap.status == SiteFileStatus.REJECTED
    # A robots.txt must not name a sitemap the customer said no to.
    plans = plan_site_files(base_url=base_url, result=result, issues=issues, sitemap_declined=declined)
    planned = {plan.kind for plan in plans}
    for plan in plans:
        _merge_proposal(file_store, site, plan, result)
    # The scan could see the site and asks for nothing here, so a proposal still
    # open is out of date; left standing, it could be put up over a newer file.
    if result.home_status != 200:
        return
    judged = {
        SiteFileKind.ROBOTS: robots_can_be_judged(result),
        # A refused sitemap was not planned, so nothing was found out about it:
        # left as it is, unless it was a first one and the site now has its own.
        # A refused improvement stays refused, or two scans later it is back.
        SiteFileKind.SITEMAP: sitemap_can_be_judged(result, issues)
        and (not declined or ("created" in sitemap.changes and bool(result.sitemap_urls))),
    }
    for kind in SiteFileKind:
        if kind in planned or not judged[kind]:
            continue
        existing = file_store.find(site.site_id, kind)
        if existing is not None and existing.status in _OPEN:
            file_store.save(existing.record(status=SiteFileStatus.NOT_NEEDED, actor=SYSTEM_ACTOR, reason="not_needed"))


def _merge_proposal(file_store: SiteFileStore, site, plan: FilePlan, result) -> None:
    existing = file_store.find(site.site_id, plan.kind)
    previous = result.robots_text if plan.kind == SiteFileKind.ROBOTS else None
    if existing is not None and existing.status in _SETTLED_ON_SAME_PLAN and _same_plan(existing, plan, previous):
        if plan.kind == SiteFileKind.SITEMAP:
            _refresh_sitemap_text(file_store, existing, plan)
        return
    proposal = SiteFileChange(
        site_id=site.site_id,
        tenant_id=site.principal_id,
        kind=plan.kind,
        public_url=plan.public_url,
        proposed_text=plan.text,
        previous_text=previous,
        changes=list(plan.changes),
        dropped=list(plan.dropped),
        history=list(existing.history) if existing else [],
    )
    file_store.save(proposal.record(actor=SYSTEM_ACTOR, reason="proposed", detail={"changes": list(plan.changes)}))


def _same_plan(existing: SiteFileChange, plan: FilePlan, current_robots: str | None) -> bool:
    """The same answer to the same problem. A sitemap's list moves with every new page,
    so for it the problem alone counts; robots.txt is compared by its words, and by
    the file it was built from, which the customer may have changed since."""
    if existing.changes != list(plan.changes) or existing.public_url != plan.public_url:
        return False
    if plan.kind == SiteFileKind.SITEMAP:
        return True
    return existing.proposed_text == plan.text and existing.previous_text == current_robots


def _locations(text: str) -> set[str]:
    return {_identity(url) for url in parse_sitemap(text.encode("utf-8"))[0]}


def _refresh_sitemap_text(file_store: SiteFileStore, existing: SiteFileChange, plan: FilePlan) -> None:
    """Keep a standing sitemap proposal's list current, or a later approval puts up an old one."""
    if existing.proposed_text == plan.text:
        return
    if existing.status == SiteFileStatus.WAITING_FOR_CUSTOMER:
        # The customer has the file. Pages added since make it short: ask again.
        if _locations(plan.text) - _locations(existing.proposed_text):
            file_store.save(
                existing.record(
                    status=SiteFileStatus.PROPOSED,
                    actor=SYSTEM_ACTOR,
                    reason="proposed",
                    detail={"changes": list(plan.changes), "why": "new_pages"},
                    proposed_text=plan.text,
                    dropped=list(plan.dropped),
                )
            )
        return
    file_store.save(existing.model_copy(update={"proposed_text": plan.text, "dropped": list(plan.dropped)}))


def public_file_matches(change: SiteFileChange, fetcher: Fetcher) -> tuple[bool, str | None]:
    """Does the public address serve this file? And if not, what was seen there."""
    limit = MAX_ROBOTS_BYTES if change.kind == SiteFileKind.ROBOTS else MAX_SITEMAP_BYTES
    answer = fetcher(change.public_url, limit)
    if answer.status != 200:
        return False, f"http_{answer.status}"
    if change.kind == SiteFileKind.SITEMAP:
        listed, children = parse_sitemap(answer.body)
        if not listed and not children:
            return False, "not_a_sitemap"
        # The new list, not any sitemap: an old file left in place answers too. So
        # every address it has must be there, and none of those it drops.
        served = {_identity(url) for url in listed}
        wanted = _locations(change.proposed_text)
        gone = {_identity(url) for url in change.dropped}
        return (True, None) if wanted <= served and not (gone & served) else (False, "different_content")
    served, unreadable = read_robots_answer(answer)
    if unreadable or served is None:
        return False, "different_content"
    return (True, None) if _same_text(served, change.proposed_text) else (False, "different_content")


def _same_text(first: str | None, second: str | None) -> bool:
    def lines(text: str | None) -> list[str]:
        return [line.strip() for line in (text or "").splitlines() if line.strip()]

    return lines(first) == lines(second)


def build_site_file_handler(
    *,
    site_store: SiteStore,
    file_store: SiteFileStore,
    fetcher: Fetcher = default_fetcher,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The customer says the file is in place: look at the public address."""

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        site_id = str(payload["site_id"])
        kind = SiteFileKind(str(payload["kind"]))
        change = file_store.find(site_id, kind)
        if change is None or change.status != SiteFileStatus.WAITING_FOR_CUSTOMER or _is_removed(site_store.get(site_id)):
            return {"site_id": site_id, "skipped": True}
        shown, seen = public_file_matches(change, fetcher)
        if shown:
            change = file_store.save(
                change.record(status=SiteFileStatus.APPLIED, actor=SYSTEM_ACTOR, reason="seen_on_site", note=None)
            )
        else:
            change = file_store.save(change.record(actor=SYSTEM_ACTOR, reason="not_seen_yet", detail={"seen": seen}, note=seen))
        return {"site_id": site_id, "kind": kind.value, "status": change.status.value}

    return handler
