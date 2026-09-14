from pydantic import BaseModel, ConfigDict


class ProviderRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    display_name: str

    enabled: bool = True

    priority: int
    model: str

    config_version: str