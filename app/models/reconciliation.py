from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.url_metrics import URLDailyMetric


class ReconciliationStatus(StrEnum):
    MATCH = "match"
    PARTIAL = "partial"
    INVALID = "invalid"


class ReconciliationInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    daily_query_impressions: int = Field(ge=0)
    daily_query_clicks: int = Field(ge=0)
    url_daily_metrics: list[URLDailyMetric]


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    impression_status: ReconciliationStatus
    click_status: ReconciliationStatus

    query_impressions: int = Field(ge=0)
    url_impressions: int = Field(ge=0)

    query_clicks: int = Field(ge=0)
    url_clicks: int = Field(ge=0)

    impression_gap: int = Field(ge=0)
    click_gap: int = Field(ge=0)

    is_valid: bool
    is_partial: bool