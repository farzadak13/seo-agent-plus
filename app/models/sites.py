from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class SiteStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class SecretProvider(StrEnum):
    ENVIRONMENT = "environment"
    # Encrypted in the database, one value per site and field. The reference
    # key is "<site_id>/<field>"; the value never appears in the site record.
    DATABASE = "database"


class SecretRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: SecretProvider = SecretProvider.ENVIRONMENT
    key: str = Field(min_length=1, max_length=200)


DOMAIN_PROPERTY_PREFIX = "sc-domain:"


def validate_property_url(value: str) -> str:
    """Accept a Search Console property exactly as Google spells it.

    Deliberately not ``HttpUrl``. Two reasons, both found the hard way:

    * A domain property is ``sc-domain:pama.shop`` — not a URL at all, so
      ``HttpUrl`` rejected it outright and domain properties, which is what
      Google now recommends, could not be connected.
    * ``HttpUrl`` normalises. ``https://pama.shop`` becomes
      ``https://pama.shop/``. Whether that matches depends on our normaliser
      agreeing with Google's, and a property string that is nearly right
      fails later as a 403, which reads as a missing grant.

    So the string is stored byte for byte and only checked for shape.
    """
    value = value.strip()
    if not value:
        raise ValueError("A Search Console property is required.")
    if value.startswith(DOMAIN_PROPERTY_PREFIX):
        host = value[len(DOMAIN_PROPERTY_PREFIX):]
        if not host or "/" in host or ":" in host or host != host.strip():
            raise ValueError(
                "A domain property looks like 'sc-domain:example.com' — "
                f"no scheme and no path. Got: {value}"
            )
        return value
    if not value.startswith(("http://", "https://")):
        raise ValueError(
            "A Search Console property is either a URL prefix "
            "('https://example.com/') or a domain property "
            f"('sc-domain:example.com'). Got: {value}"
        )
    return value


class GSCConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    property_url: str = Field(min_length=1, max_length=2000)
    credential_ref: SecretRef
    auth_mode: str = Field(default="access_token", min_length=1, max_length=50)
    row_limit: int = Field(default=25_000, ge=1, le=25_000)

    @field_validator("property_url")
    @classmethod
    def property_is_spelled_the_way_google_spells_it(cls, value: str) -> str:
        return validate_property_url(value)


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
    # Proof that this tenant controls the site. Required before a shared
    # Search Console credential may be pointed at the site's property: the
    # shared service account can read every customer's property, so without
    # this anyone could connect anyone's data. See app.onboarding.ownership.
    verification_token: str | None = Field(default=None, max_length=100)
    ownership_method: str | None = Field(default=None, max_length=50)
    ownership_verified_at: datetime | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
