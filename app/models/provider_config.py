from pydantic import BaseModel, ConfigDict, Field


class ArvanAIProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(
        min_length=1,
    )

    api_key: str = Field(
        min_length=1,
    )

    model: str = Field(
        min_length=1,
    )

    max_tokens: int = Field(
        default=3000,
        gt=0,
    )

    temperature: float = Field(
        default=0.7,
        ge=0,
        le=2,
    )

    max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
    )

    timeout_seconds: float = Field(
        default=60.0,
        gt=0,
    )