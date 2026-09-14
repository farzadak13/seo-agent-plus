from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PersistenceRecord(BaseModel):
    """Database-agnostic, versioned persistence envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    aggregate_type: str = Field(min_length=1)
    aggregate_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    version: int = Field(default=1, ge=1)
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_id: str | None = None
    data_snapshot_id: str | None = None
    rule_version: str | None = None
    config_version: str | None = None

    # Owner scope. Denormalized out of the payload so records can be found by
    # owner without scanning: a record with no tenant is never returned by a
    # tenant-scoped query.
    tenant_id: str | None = None
    site_id: str | None = None
