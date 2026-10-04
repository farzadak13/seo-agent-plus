"""Keyword demand and rank data, in our terms rather than any provider's.

Nothing here knows which service supplied it. A provider adapter translates
its own JSON into these models at the boundary (see app.keyword_intel), so the
rest of the system is unchanged when the provider is.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Competition(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class KeywordVolume(BaseModel):
    """Monthly search demand for one keyword.

    ``search_volume`` is None when the provider has no data for the keyword,
    which is an answer worth remembering, not a failure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword: str = Field(min_length=1)
    search_volume: int | None = Field(default=None, ge=0)
    competition: Competition = Competition.UNKNOWN
    provider_id: str = Field(min_length=1)
    fetched_at: datetime


class VolumeLookup(BaseModel):
    """The answer to "how much demand do these keywords have".

    ``pending`` lists keywords that could not be asked about today because the
    daily request budget is spent; they are answered from cache next time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    volumes: list[KeywordVolume]
    pending: list[str] = Field(default_factory=list)
    requests_made: int = Field(default=0, ge=0)


class Device(StrEnum):
    MOBILE = "mobile"
    DESKTOP = "desktop"


class RankProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    name: str
    domain: str
    active: bool = True


class RankProject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    name: str
    domain: str
    keywords: list[str] = Field(default_factory=list)
    competitor_domains: list[str] = Field(default_factory=list)
    location: str | None = None
    devices: list[Device] = Field(default_factory=list)


class RankPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    date: date
    # None: checked that day and not found in the tracked results.
    rank: int | None = Field(default=None, ge=1)


class KeywordRankHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword: str = Field(min_length=1)
    search_volume: int | None = Field(default=None, ge=0)
    target_url: str | None = None
    points: list[RankPoint] = Field(default_factory=list)


class RankHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    domain: str
    device: Device
    start: date
    end: date
    keywords: list[KeywordRankHistory]
    provider_id: str
    fetched_at: datetime
