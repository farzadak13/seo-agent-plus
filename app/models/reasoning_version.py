from pydantic import BaseModel, ConfigDict, Field


class ReasoningVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_version: str = Field(
        min_length=1,
    )

    rule_version: str = Field(
        min_length=1,
    )

    config_version: str = Field(
        min_length=1,
    )