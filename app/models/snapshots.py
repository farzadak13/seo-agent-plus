from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SnapshotMetadata(BaseModel):
    """
    Immutable metadata describing the evidence/configuration context
    used to produce a candidate.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    snapshot_id: str = Field(min_length=1)

    data_snapshot_id: str = Field(min_length=1)

    rule_version: str = Field(min_length=1)

    config_version: str = Field(min_length=1)

    generated_at: datetime