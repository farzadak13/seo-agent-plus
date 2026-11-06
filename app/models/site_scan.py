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


class PageSpeedSignals(BaseModel):
    """What the page's own HTML and its answer say about speed, without a browser.

    Not a Lighthouse score: the signals a page's markup gives away, read on the
    same visit the scan makes anyway, so a site is not asked for anything more.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # From the request to the last byte of the HTML, as this server saw it.
    fetch_ms: int = Field(ge=0)
    html_kb: int = Field(ge=0)
    # <script src> in <head> with neither async nor defer, nor type=module.
    blocking_scripts: int = Field(default=0, ge=0)
    stylesheets: int = Field(default=0, ge=0)
    images: int = Field(default=0, ge=0)
    # Without width and height: the page jumps as they load (layout shift).
    images_unsized: int = Field(default=0, ge=0)
    # Past the first few, without loading="lazy".
    images_not_lazy: int = Field(default=0, ge=0)
    # .jpg, .jpeg, .png, .gif where WebP or AVIF would be a fraction of the size.
    images_old_format: int = Field(default=0, ge=0)


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
    speed: PageSpeedSignals | None = None


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
    # 0-100 (app.scan.score); None until the scan is done, or when the site did not answer.
    health_score: int | None = Field(default=None, ge=0, le=100)
    error: str | None = None
