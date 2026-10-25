"""A site's activity log: what the service did and decided, newest first.

Built from records that already exist, so it cannot drift from them: every
event in every action's history (proposed, approved, applied, verified,
measured, undone), and the Search Console sync ledger, folded to one entry
per day of fetching so a backfill of hundreds of days does not bury the rest.
Pure: the caller reads the records.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.managed_action import ManagedAction
from app.warehouse.planner import FAILURE_MESSAGE

# Detail keys worth showing; the rest is for the action's own page.
_ACTION_DETAIL = (
    "apply_title", "restore_title", "found", "rendered_title", "note", "edited_title",
    "chosen_alternative", "outcome_status", "relative_change", "primary_metric",
    "baseline", "current", "data_complete",
)


class ActivityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    kind: str  # "action" | "sync"
    event: str
    action_id: str | None = None
    status: str | None = None
    title: str | None = None
    detail: dict = Field(default_factory=dict)


def build_activity(
    *, actions: Iterable[ManagedAction], sync_entries: Iterable, limit: int = 50
) -> list[ActivityItem]:
    items: list[ActivityItem] = []
    for managed in actions:
        title = managed.action.parameters.get("recommended_title")
        for event in managed.history:
            items.append(
                ActivityItem(
                    at=event.at,
                    kind="action",
                    event=event.reason,
                    action_id=managed.action_id,
                    status=event.status.value,
                    title=title,
                    detail={key: event.detail[key] for key in _ACTION_DETAIL if key in event.detail},
                )
            )

    days: dict = defaultdict(lambda: {"synced": 0, "failed": 0, "at": None})
    for entry in sync_entries:
        bucket = days[entry.fetched_at.date()]
        status = entry.status.value if hasattr(entry.status, "value") else str(entry.status)
        bucket["synced" if status == "synced" else "failed"] += 1
        if bucket["at"] is None or entry.fetched_at > bucket["at"]:
            bucket["at"] = entry.fetched_at
    for bucket in days.values():
        detail = {"days_synced": bucket["synced"], "days_failed": bucket["failed"]}
        if bucket["failed"]:
            # The ledger's own error is the operator's diagnosis and can name
            # internals (SyncDayStatus); the customer gets the generic line.
            detail["message"] = FAILURE_MESSAGE
        items.append(
            ActivityItem(
                at=bucket["at"],
                kind="sync",
                event="search_console_sync" if not bucket["failed"] else "search_console_sync_failed",
                detail=detail,
            )
        )

    items.sort(key=lambda item: (item.at, item.kind, item.event), reverse=True)
    return items[:limit]
