"""One day of Search Console data for one site, as it is stored.

Neutral of how it was fetched. Metrics are additive on purpose: clicks,
impressions, and position_sum (position x impressions), so any number of
rows can be summed and the average position recovered exactly afterwards.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


# Search Console returns at most this many rows per day and search type.
DAILY_ROW_CAP = 50_000


class Device(StrEnum):
    DESKTOP = "DESKTOP"
    MOBILE = "MOBILE"
    TABLET = "TABLET"


class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: Device
    clicks: int = Field(ge=0)
    impressions: int = Field(ge=0)
    position_sum: float = Field(ge=0)


class PageMetrics(Metrics):
    url: str = Field(min_length=1)


class QueryMetrics(Metrics):
    url: str = Field(min_length=1)
    query: str = Field(min_length=1)


class GSCDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str = Field(min_length=1)
    day: date
    property_url: str = Field(min_length=1)
    totals: list[Metrics]
    pages: list[PageMetrics]
    queries: list[QueryMetrics]
    # True when a request reached Search Console's daily row cap, so that
    # breakdown is complete only up to it. Totals are unaffected: they have
    # no page or query and stay far below it.
    page_rows_capped: bool = False
    query_rows_capped: bool = False


class SyncStatus(StrEnum):
    SYNCED = "synced"
    FAILED = "failed"


class DaySyncResult(BaseModel):
    """What one day's sync stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str
    day: date
    totals_rows: int = Field(ge=0)
    page_rows: int = Field(ge=0)
    query_rows: int = Field(ge=0)
    page_rows_capped: bool
    query_rows_capped: bool


class SyncDayStatus(BaseModel):
    """The sync ledger's entry for one site and day.

    ``error`` is the operator's diagnosis and can name internals; whatever
    shows it to a customer must say something generic instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str
    day: date
    property_url: str
    status: SyncStatus
    totals_rows: int = Field(ge=0)
    page_rows: int = Field(ge=0)
    query_rows: int = Field(ge=0)
    page_rows_capped: bool
    query_rows_capped: bool
    fetched_at: datetime
    error: str | None = None
