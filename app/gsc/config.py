from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class GSCClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_state: Literal["final", "all"] = "final"
    max_pages: int = Field(default=100, ge=1, le=1000)
    oauth_access_token: str | None = None
    service_account_json: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    page_size: int = Field(default=25000, ge=1, le=25000)

    def auth_mode(self) -> str:
        if self.oauth_access_token:
            return "access_token"
        if self.service_account_json:
            return "service_account"
        return "none"

