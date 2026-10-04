"""Keyword demand, in our terms rather than any provider's.

Nothing here knows which service supplied it. A provider adapter translates
its own JSON into these models at the boundary (see app.keyword_intel), so the
rest of the system is unchanged when the provider is.

Rankings are not here on purpose: they come from Search Console, which every
connected site already has, rather than from a tracker that needs a project
set up by hand per site.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Competition(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    # Seen live from SEO Signal as "خیلی سخت", beside "سخت". Kept apart from
    # HIGH: the hardest keywords are exactly the ones worth ranking lower.
    VERY_HIGH = "very_high"
    UNKNOWN = "unknown"


class KeywordVolume(BaseModel):
    """Monthly search demand for one keyword.

    ``search_volume`` is None when the provider has no data for the keyword,
    which is an answer worth remembering, not a failure. ``confirmed`` is
    False when that answer is less certain (the provider found nothing for
    the whole request, rather than reporting on each keyword), and is then
    kept for a day rather than a month.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword: str = Field(min_length=1)
    search_volume: int | None = Field(default=None, ge=0)
    competition: Competition = Competition.UNKNOWN
    provider_id: str = Field(min_length=1)
    fetched_at: datetime
    confirmed: bool = True


class VolumeLookup(BaseModel):
    """The answer to "how much demand do these keywords have".

    ``pending`` lists keywords that could not be asked about today because the
    daily request budget is spent; they are answered from cache next time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    volumes: list[KeywordVolume]
    pending: list[str] = Field(default_factory=list)
    requests_made: int = Field(default=0, ge=0)
