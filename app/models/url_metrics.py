from datetime import date as Date

from pydantic import BaseModel, ConfigDict, Field

from app.models.observations import DataStatus


class URLDailyMetric(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    site_id: str = Field(min_length=1)
    normalized_url: str = Field(min_length=1)
    date: Date
    total_impressions: int = Field(ge=0)
    total_clicks: int = Field(ge=0)
    data_status: DataStatus = DataStatus.OBSERVED

    @staticmethod
    def _validate_clicks(impressions: int, clicks: int) -> None:
        if clicks > impressions:
            raise ValueError("total_clicks cannot exceed total_impressions")

    def model_post_init(self, __context: object) -> None:
        self._validate_clicks(
            self.total_impressions,
            self.total_clicks,
        )