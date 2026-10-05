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


class SyncState(StrEnum):
    NOT_CONNECTED = "not_connected"  # no Search Console property on the site
    NOT_ELIGIBLE = "not_eligible"    # connected, but ownership not proven
    BACKFILLING = "backfilling"      # days in the window still to fetch
    UP_TO_DATE = "up_to_date"        # every day in the window is stored
    FAILING = "failing"              # the newest settled days keep failing


class SyncFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    # Generic: what the customer may read. The ledger keeps the operator's detail.
    message: str


class SyncSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str
    state: SyncState
    property_url: str | None = None
    window_start: date | None = None
    window_end: date | None = None
    days_in_window: int = Field(default=0, ge=0)
    synced_days: int = Field(default=0, ge=0)
    failed_days: int = Field(default=0, ge=0)
    progress: float = Field(default=0.0, ge=0, le=1)
    newest_synced_day: date | None = None
    oldest_synced_day: date | None = None
    capped_days: list[date] = Field(default_factory=list)
    recent_failures: list[SyncFailure] = Field(default_factory=list)


# --- what the dashboard reads -------------------------------------------------------


class Interval(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class SortKey(StrEnum):
    CLICKS = "clicks"
    IMPRESSIONS = "impressions"
    CTR = "ctr"
    POSITION = "position"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class Compare(StrEnum):
    PREVIOUS = "previous"  # the period of the same length just before


class MetricValues(BaseModel):
    """Clicks and impressions summed; CTR and position recomputed from the sums."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clicks: int = Field(ge=0)
    impressions: int = Field(ge=0)
    ctr: float | None = None       # None where there were no impressions
    position: float | None = None  # impression-weighted average


class SeriesPoint(MetricValues):
    period_start: date
    # Synced days inside this period: fewer than its length means a partial
    # period (the backfill has not reached it, or a day failed).
    days_covered: int = Field(ge=0)


class PerformanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str
    start: date
    end: date
    interval: Interval
    device: Device | None = None
    totals: MetricValues
    days_covered: int = Field(ge=0)
    days_in_range: int = Field(ge=1)
    series: list[SeriesPoint]


class TableRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str  # the page URL or the query
    current: MetricValues
    previous: MetricValues | None = None  # the same metrics for the period before
    # Monthly searches, from the keyword cache only (queries table); never fetched here.
    search_volume: int | None = None


class TableReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str
    start: date
    end: date
    device: Device | None = None
    sort: SortKey
    order: SortOrder
    rows: list[TableRow]
    next_offset: int | None = None
    compared_with: tuple[date, date] | None = None
