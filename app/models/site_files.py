"""A proposed robots.txt or sitemap, from proposal to the file being on the site.

HoshyarSEO writes neither file. It makes the file, the customer approves it and
puts it on their site, and the public address is read to see that it is there.
A wrong robots.txt can take a whole site out of Google, so that last step stays
with the person who owns the site, and what was there before is kept for them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SiteFileKind(StrEnum):
    ROBOTS = "robots"
    SITEMAP = "sitemap"


class SiteFileStatus(StrEnum):
    PROPOSED = "proposed"
    # Approved: the file is the customer's to put in place, and then we look.
    WAITING_FOR_CUSTOMER = "waiting_for_customer"
    APPLIED = "applied"
    REJECTED = "rejected"
    # A later scan found the site no longer needs it.
    NOT_NEEDED = "not_needed"


class SiteFileEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime = Field(default_factory=_now)
    actor: str
    reason: str
    detail: dict[str, Any] = Field(default_factory=dict)


class SiteFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    kind: SiteFileKind
    status: SiteFileStatus = SiteFileStatus.PROPOSED
    # Where a search engine will look for it.
    public_url: str
    proposed_text: str
    # What the site served when this was proposed; None: it had no such file.
    previous_text: str | None = None
    # "created", "added_sitemap", "removed_block_all", "improved", "partial".
    changes: list[str] = Field(default_factory=list)
    # Addresses an improved sitemap leaves out: the old file still has them.
    dropped: list[str] = Field(default_factory=list)
    # What the last look at the public address saw, when it was not the file.
    note: str | None = None
    history: list[SiteFileEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def record(self, *, status: SiteFileStatus | None = None, actor: str, reason: str, detail: dict | None = None, **changes):
        event = SiteFileEvent(actor=actor, reason=reason, detail=detail or {})
        return self.model_copy(
            update={
                **changes,
                "status": status or self.status,
                "history": [*self.history, event],
                "updated_at": event.at,
            }
        )
