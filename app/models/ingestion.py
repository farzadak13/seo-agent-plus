from datetime import date as Date

from pydantic import BaseModel, ConfigDict, Field

from app.models.gsc import NormalizedGSCRow
from app.models.observations import DailyObservation, DataStatus
from app.models.url_metrics import URLDailyMetric


class GSCIngestionResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    site_id: str = Field(min_length=1)
    response_id: str = Field(min_length=1)

    normalized_rows: list[NormalizedGSCRow]
    observations: list[DailyObservation]
    url_daily_metrics: list[URLDailyMetric]
    daily_status: list[tuple[Date, DataStatus]]

    @property
    def daily_query_impressions(self) -> int:
        return sum(
            row.impressions
            for row in self.normalized_rows
        )

    @property
    def daily_query_clicks(self) -> int:
        return sum(
            row.clicks
            for row in self.normalized_rows
        )

    @property
    def daily_url_impressions(self) -> int:
        return sum(
            metric.total_impressions
            for metric in self.url_daily_metrics
        )

    @property
    def daily_url_clicks(self) -> int:
        return sum(
            metric.total_clicks
            for metric in self.url_daily_metrics
        )