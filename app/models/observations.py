from __future__ import annotations

from datetime import date as Date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataStatus(str, Enum):
    """
    Describes whether the observation came directly from GSC
    or was created during calendar expansion / data-quality processing.
    """

    OBSERVED = "OBSERVED"
    MISSING_EXPECTED_ZERO = "MISSING_EXPECTED_ZERO"
    MISSING_UNKNOWN = "MISSING_UNKNOWN"


class CalendarContext(BaseModel):
    """
    Calendar-related context attached to one daily observation.

    These fields are contextual signals. They do not directly
    determine whether an SEO issue exists.
    """

    model_config = ConfigDict(extra="forbid")

    is_weekend: bool = False
    holiday_overlap: bool = False
    event_type: Optional[str] = None


class DailyObservation(BaseModel):
    """
    Canonical daily observation for a URL + Query pair.

    This is a domain model. It intentionally does not contain
    database-specific or API-specific logic.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    site_id: str = Field(
        min_length=1,
        max_length=50,
        description="Stable identifier of the connected website.",
    )

    normalized_url: str = Field(
        min_length=1,
        description="Canonicalized URL used by the SEO data pipeline.",
    )

    normalized_query: str = Field(
        min_length=1,
        description="Normalized search query used for aggregation and identity.",
    )

    date: Date = Field(
    description="Observation date in the site's reporting timezone.",
)

    impressions: int = Field(
        default=0,
        ge=0,
        description="Number of search impressions.",
    )

    clicks: int = Field(
        default=0,
        ge=0,
        description="Number of search clicks.",
    )

    avg_position: Optional[float] = Field(
        default=None,
        ge=0,
        description="GSC average position. Null when no valid position exists.",
    )

    data_status: DataStatus = Field(
        description="Origin / quality state of this daily observation.",
    )

    calendar_context: CalendarContext = Field(
        default_factory=CalendarContext,
    )

    retrieved_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when this observation was retrieved/generated.",
    )

    @field_validator("site_id", "normalized_url", "normalized_query")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        """
        Prevent values that technically satisfy min_length but
        contain only whitespace.
        """
        value = value.strip()

        if not value:
            raise ValueError("value must not be blank")

        return value

    @field_validator("clicks")
    @classmethod
    def clicks_cannot_exceed_impressions(
        cls,
        value: int,
        info,
    ) -> int:
        """
        In GSC, clicks cannot exceed impressions for the same
        observation. Validation is only applied when impressions
        are already available in the validation context.
        """
        impressions = info.data.get("impressions")

        if impressions is not None and value > impressions:
            raise ValueError("clicks cannot exceed impressions")

        return value

    def ctr(self) -> float:
        """
        Return CTR as a ratio.

        Example:
            10 clicks / 100 impressions = 0.10
        """
        if self.impressions == 0:
            return 0.0

        return self.clicks / self.impressions