from datetime import date as Date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RawGSCResponse(BaseModel):
    """
    Immutable representation of a raw Google Search Console API response.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    response_id: str = Field(min_length=1)

    site_id: str = Field(min_length=1)

    fetch_date: Date

    raw_payload: dict[str, Any]

    created_at: datetime


class NormalizedGSCRow(BaseModel):
    """
    Normalized row extracted from a GSC response.

    This model represents query-level performance data before
    conversion into domain observations.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    site_id: str = Field(min_length=1)

    date: Date

    normalized_url: str = Field(min_length=1)

    normalized_query: str = Field(min_length=1)

    impressions: int = Field(ge=0)

    clicks: int = Field(ge=0)

    avg_position: float | None = Field(
        default=None,
        ge=0,
    )