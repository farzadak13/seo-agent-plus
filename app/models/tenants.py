"""A tenant and the keys that speak for it.

Until now every request authenticated as the same principal: one key in the
environment, hard-coded to the principal id "api-key". Ownership checks ran on
every endpoint and always compared that constant against itself, so they passed
without meaning anything. One customer's key could read another customer's
sites, and the code looked correct while doing it.

A key belongs to a tenant. The tenant id is what ownership is checked against,
and it is also what the persistence layer already indexes as ``tenant_id``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Tenant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    status: TenantStatus = TenantStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class APIKeyRecord(BaseModel):
    """What is stored for a key. Never the key itself.

    ``digest`` is a plain SHA-256 rather than a stretched hash, and that is
    deliberate. Stretching exists to make guessing a human-chosen password
    expensive. These keys are 256 bits from ``secrets``, so no offline search
    is feasible whatever the hash — while 310,000 PBKDF2 iterations would be
    paid on every single request, which is over a tenth of a second of CPU per
    call to protect against an attack that cannot happen.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    digest: str = Field(min_length=64, max_length=64, repr=False)
    label: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None
