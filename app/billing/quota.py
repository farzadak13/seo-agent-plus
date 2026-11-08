"""How many changes a tenant has had approved in the last 30 days, and when one frees up.

Counted from the approvals on record, so nothing extra is kept. An approval is
one change whether the site took it, the customer applied it by hand, or it
was later undone: the work was done all the same. One that came to nothing
(it failed, or was called off before anything was written) is not counted:
the customer does not pay for what we could not do.

A soft limit: two approvals at the same moment may both pass the check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.billing.plans import QUOTA_WINDOW
from app.models.actions import ActionStatus

# The reason approval.approve records; the other events of an approved action
# (applied, waiting for the customer, a retry) are not approvals.
_APPROVED = "approved"
_CAME_TO_NOTHING = {ActionStatus.FAILED, ActionStatus.REJECTED}


@dataclass(frozen=True)
class Quota:
    used: int
    limit: int | None
    # When enough counted approvals have left the window for one change to free
    # up; None when there is room already.
    frees_at: datetime | None

    @property
    def left(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used, 0)


def approvals_since(action_store, tenant_id: str, since: datetime) -> list[datetime]:
    found: list[datetime] = []
    cursor = None
    while True:
        page, cursor = action_store.query(tenant_id=tenant_id, limit=200, cursor=cursor)
        for managed in page:
            if managed.applied is None and managed.status in _CAME_TO_NOTHING:
                continue
            found.extend(
                event.at for event in managed.history
                if event.reason == _APPROVED and event.at >= since
            )
        # Newest first: once a whole page was last touched before the window,
        # nothing further on was approved inside it.
        if cursor is None or (page and max(managed.updated_at for managed in page) < since):
            return sorted(found)


def quota(action_store, tenant_id: str, limit: int | None, now: datetime) -> Quota:
    if limit is None or action_store is None:
        return Quota(used=0, limit=None, frees_at=None)
    approved = approvals_since(action_store, tenant_id, now - QUOTA_WINDOW)
    used = len(approved)
    # Over the limit (after a smaller plan, say), more than one must leave first.
    frees_at = approved[used - limit] + QUOTA_WINDOW if used >= limit and limit > 0 else None
    return Quota(used=used, limit=limit, frees_at=frees_at)
