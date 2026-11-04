"""The words a page is meant to be found by, for a site that has no Search Console data yet.

A site with history is steered by what people already search to reach it. A new
site has none, so the customer says what the business is, a model suggests a
word per page from what the site shows, the customer keeps the right ones, and
where the page stands in Google for each word is looked up week by week.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


# A title strategy built for one of these words says so in its evidence; the
# SERP check then does not ask that a new page already rank.
TARGET_KEYWORD_ORIGIN = "target_keyword"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KeywordStatus(StrEnum):
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class KeywordSource(StrEnum):
    MODEL = "model"
    CUSTOMER = "customer"


class KeywordIntent(StrEnum):
    TRANSACTIONAL = "transactional"
    COMMERCIAL = "commercial"
    INFORMATIONAL = "informational"
    NAVIGATIONAL = "navigational"


class SuggestionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    FAILED = "failed"


class RankCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_at: datetime = Field(default_factory=_now)
    # None: not in the results looked at (the first ten).
    position: int | None = Field(default=None, ge=1)
    # The site's own page that ranked, which may not be the one targeted.
    url: str | None = None


class TargetKeyword(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)
    status: KeywordStatus = KeywordStatus.SUGGESTED
    source: KeywordSource = KeywordSource.MODEL
    intent: KeywordIntent | None = None
    reason: str | None = Field(default=None, max_length=300)
    checks: list[RankCheck] = Field(default_factory=list)
    added_at: datetime = Field(default_factory=_now)
    # The latest title asked for this word, so a second one waits for it.
    title_run_id: str | None = None
    title_requested_at: datetime | None = None


class TargetKeywordSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    description: str = Field(default="", max_length=1000)
    seeds: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[TargetKeyword] = Field(default_factory=list)
    suggestion_status: SuggestionStatus = SuggestionStatus.IDLE
    suggestion_job_id: str | None = None
    suggested_at: datetime | None = None
    suggestion_error: str | None = None
    # When suggestions and titles were asked for, recent ones only: each costs
    # a model call, so the tenant's daily limits count them.
    suggest_requests: list[datetime] = Field(default_factory=list)
    title_requests: list[datetime] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_now)
