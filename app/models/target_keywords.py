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


class Rival(BaseModel):
    """Another site in the same results: where it stood for the word that week."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = Field(min_length=1, max_length=253)
    # None: not in the results looked at (the first ten).
    position: int | None = Field(default=None, ge=1)


class RankCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_at: datetime = Field(default_factory=_now)
    # None: not in the results looked at (the first ten).
    position: int | None = Field(default=None, ge=1)
    # The site's own page that ranked, which may not be the one targeted.
    url: str | None = None
    # The competitors the customer had named when this look was made, and those
    # of them found, for a trend: one looked for and not found was not in the
    # first ten; one named since has not been looked for yet.
    watched: list[str] = Field(default_factory=list, max_length=3)
    rivals: list[Rival] = Field(default_factory=list)
    # The first three other sites; kept on the latest check only (see RankTracker).
    leaders: list[Rival] = Field(default_factory=list)


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
    # Competitors' sites the customer follows (hosts, without www.).
    competitors: list[str] = Field(default_factory=list, max_length=3)
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
