"""Asking for a meta description: a job that writes one and puts it in the approval inbox.

It goes the way a title goes: the customer approves it (or one of the other
choices, or their own edit), it is applied by the connector or by hand, the
page is checked for it, and it can be undone. The page's description as the
scan read it is what the change must find there, so a page edited in the
meantime is not overwritten.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.action.approval import SYSTEM_ACTOR
from app.action.fields import DESCRIPTION
from app.action.store import ManagedActionStore
from app.descriptions.writer import DescriptionError, DescriptionWriter, PageForDescription
from app.models.actions import Action, ActionRiskLevel, ActionStatus
from app.models.managed_action import ActionEvent, ManagedAction
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType
from app.models.target_keywords import KeywordStatus
from app.normalization.url import canonicalize_url
from app.scan.crawler import _identity
from app.scan.store import SiteScanStore

log = logging.getLogger(__name__)

DESCRIPTION_JOB_TYPE = "meta_description"
RULE_VERSION = "meta-description-v1"


def scanned_page(scan, url: str):
    if scan is None:
        return None
    wanted = _identity(url)
    return next((page for page in scan.pages if _identity(page.url) == wanted and page.status_code == 200), None)


def propose_description_change(
    *, site_id: str, tenant_id: str, job_id: str, page, keyword: str | None, choices: list[str], provider_id: str, now: datetime
) -> ManagedAction:
    best, *others = choices
    action = Action(
        action_id=f"action:{job_id}",
        strategy_id=f"description:{job_id}",
        opportunity_id=f"description:{site_id}:{_identity(page.url)}",
        site_id=site_id,
        normalized_url=canonicalize_url(page.url),
        normalized_query=keyword or "",
        strategy_type=StrategyType.META_DESCRIPTION_OPTIMIZATION,
        action_type=DESCRIPTION.action_type,
        status=ActionStatus.AWAITING_APPROVAL,
        risk_level=ActionRiskLevel.LOW,
        confidence_score=0.5,
        expected_impact_score=0.4,
        priority_score=0.4,
        requires_approval=True,
        reasons=["meta_description_requested"],
        parameters={
            "field": DESCRIPTION.name,
            f"recommended_{DESCRIPTION.name}": best,
            f"expected_{DESCRIPTION.name}": best,
            "alternative_titles": others,
        },
        evidence={"origin": "meta_description", "provider_id": provider_id},
        snapshot=SnapshotMetadata(
            snapshot_id=f"description:{job_id}",
            data_snapshot_id="site-scan",
            rule_version=RULE_VERSION,
            config_version=RULE_VERSION,
            generated_at=now,
        ),
    )
    return ManagedAction(
        action=action,
        tenant_id=tenant_id,
        run_id=job_id,
        observed_value=page.meta_description or "",
        history=[
            ActionEvent(
                status=ActionStatus.AWAITING_APPROVAL,
                actor=SYSTEM_ACTOR,
                reason="description_proposal_generated",
                detail={"provider_id": provider_id},
            )
        ],
    )


def build_description_handler(
    *,
    scan_store: SiteScanStore,
    action_store: ManagedActionStore,
    writer: DescriptionWriter,
    target_store=None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def keyword_for(site_id: str, url: str) -> str | None:
        keywords = target_store.find(site_id) if target_store is not None else None
        if keywords is None:
            return None
        wanted = _identity(url)
        kept = [item for item in keywords.keywords if item.status == KeywordStatus.CONFIRMED and _identity(item.url) == wanted]
        return kept[0].keyword if kept else None

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload["job_id"])
        if action_store.find(f"action:{job_id}") is not None:
            return {"job_id": job_id, "skipped": True}  # a retry after the proposal was saved
        site_id = str(payload["site_id"])
        page = scanned_page(scan_store.find(site_id), str(payload["url"]))
        if page is None:
            return {"job_id": job_id, "status": "page_not_scanned"}
        keyword = keyword_for(site_id, page.url)
        try:
            choices = writer.write(
                PageForDescription(url=page.url, title=page.title, h1=page.h1, current=page.meta_description, keyword=keyword)
            )
        except DescriptionError as exc:
            log.warning("meta description for %s failed (%s)", site_id, exc)
            return {"job_id": job_id, "status": "model_failed"}
        managed = propose_description_change(
            site_id=site_id, tenant_id=str(payload["tenant_id"]), job_id=job_id, page=page,
            keyword=keyword, choices=choices, provider_id=writer.provider_id, now=clock(),
        )
        action_store.create(managed)
        return {"job_id": job_id, "status": "proposed", "action_id": managed.action_id}

    return handler
