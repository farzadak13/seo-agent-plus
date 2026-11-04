"""One look at a site from the outside: what a search engine would find there.

A scan needs nothing from the customer but the address, and no Search Console
data, so it is where a brand-new site starts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScanStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IssueSeverity(StrEnum):
    # Keeps pages out of Google altogether.
    CRITICAL = "critical"
    # Costs clicks or crawling, but the page can still be found.
    WARNING = "warning"
    INFO = "info"


class ScanIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=60)
    severity: IssueSeverity
    # None: about the site as a whole.
    url: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ScannedPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    # None: no answer at all, or the address may not be read (robots.txt).
    status_code: int | None = None
    # Where the address finally landed, when it redirected.
    redirected_to: str | None = None
    title: str | None = None
    meta_description: str | None = None
    h1: str | None = None
    h1_count: int = 0
    canonical: str | None = None
    noindex: bool = False
    robots_blocked: bool = False
    in_sitemap: bool = False
    word_count: int = 0


class SiteScan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    # Limits count by the site being read, however many records point at it.
    host: str = ""
    status: ScanStatus = ScanStatus.QUEUED
    requested_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    # None: robots.txt could not be read, or the site has none.
    robots_text: str | None = None
    robots_status: int | None = None
    sitemap_urls: list[str] = Field(default_factory=list)
    sitemap_page_count: int = 0
    pages: list[ScannedPage] = Field(default_factory=list)
    # More was found than one scan reads.
    pages_capped: bool = False
    issues: list[ScanIssue] = Field(default_factory=list)
    error: str | None = None
