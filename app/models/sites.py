from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SiteStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class SecretProvider(StrEnum):
    ENVIRONMENT = "environment"


class SecretRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: SecretProvider = SecretProvider.ENVIRONMENT
    key: str = Field(min_length=1, max_length=200)


class GSCConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    property_url: HttpUrl
    credential_ref: SecretRef
    auth_mode: str = Field(default="access_token", min_length=1, max_length=50)
    row_limit: int = Field(default=25_000, ge=1, le=25_000)


class SiteAdapterConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_type: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, SecretRef] = Field(default_factory=dict)


class Site(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str = Field(min_length=1, max_length=50)
    principal_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    base_url: HttpUrl
    status: SiteStatus = SiteStatus.ACTIVE
    gsc: GSCConnectionConfig | None = None
    site_adapter: SiteAdapterConnection | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
