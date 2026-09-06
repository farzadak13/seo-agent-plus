from pydantic import BaseModel, ConfigDict, Field


class IngestionIntegrityResult(BaseModel):
    """
    Deterministic integrity checks for an ingestion batch.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    response_is_unique: bool
    duplicate_row_count: int = Field(ge=0)
    unique_row_count: int = Field(ge=0)
    query_impressions: int = Field(ge=0)
    url_impressions: int = Field(ge=0)
    query_clicks: int = Field(ge=0)
    url_clicks: int = Field(ge=0)

    @property
    def query_impression_gap(self) -> int:
        return self.url_impressions - self.query_impressions

    @property
    def query_click_gap(self) -> int:
        return self.url_clicks - self.query_clicks