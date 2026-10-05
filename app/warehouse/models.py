"""One day of Search Console data for one site, as it is stored.

Neutral of how it was fetched. Metrics are additive on purpose: clicks,
impressions, and position_sum (position x impressions), so any number of
rows can be summed and the average position recovered exactly afterwards.
"""
from __future__ import annotations

from datetime import date
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
    # True when the query rows reached Search Console's daily cap, so the day
    # is complete only up to it. Totals are unaffected: they have no query.
    query_rows_capped: bool = False
