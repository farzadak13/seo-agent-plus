from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from pydantic import BaseModel

from app.models.persistence import PersistenceRecord

ModelT = TypeVar("ModelT", bound=BaseModel)


def serialize_model(model: BaseModel) -> dict[str, Any]:
    """Serialize a domain model to JSON-compatible data."""
    return model.model_dump(mode="json")


def deserialize_model(model_type: type[ModelT], payload: dict[str, Any]) -> ModelT:
    """Validate and rebuild a domain model from persisted JSON-compatible data."""
    return model_type.model_validate(payload)


def canonical_json(payload: dict[str, Any]) -> str:
    """Return deterministic JSON for reproducible hashing and comparisons."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict[str, Any]) -> str:
    """Return a stable SHA-256 hash of a canonical persisted payload."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def model_hash(model: BaseModel) -> str:
    return payload_hash(serialize_model(model))


def build_record(
    *,
    record_id: str,
    aggregate_type: str,
    aggregate_id: str,
    model: BaseModel,
    schema_version: int = 1,
    version: int = 1,
    snapshot_id: str | None = None,
    data_snapshot_id: str | None = None,
    rule_version: str | None = None,
    config_version: str | None = None,
    tenant_id: str | None = None,
    site_id: str | None = None,
) -> PersistenceRecord:
    """Wrap a domain model in a persistence envelope."""
    resolved_snapshot_id = snapshot_id
    resolved_data_snapshot_id = data_snapshot_id
    resolved_rule_version = rule_version
    resolved_config_version = config_version

    snapshot = getattr(model, "snapshot", None)
    if snapshot is not None:
        resolved_snapshot_id = resolved_snapshot_id or getattr(snapshot, "snapshot_id", None)
        resolved_data_snapshot_id = resolved_data_snapshot_id or getattr(snapshot, "data_snapshot_id", None)
        resolved_rule_version = resolved_rule_version or getattr(snapshot, "rule_version", None)
        resolved_config_version = resolved_config_version or getattr(snapshot, "config_version", None)

    # Owner scope, lifted out of the payload so it can be indexed. principal_id
    # is the fallback because today one API key means one account; when models
    # carry an explicit tenant_id, that wins.
    resolved_tenant_id = (
        tenant_id
        or getattr(model, "tenant_id", None)
        or getattr(model, "principal_id", None)
    )
    resolved_site_id = site_id or getattr(model, "site_id", None)

    return PersistenceRecord(
        record_id=record_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        schema_version=schema_version,
        version=version,
        payload=serialize_model(model),
        snapshot_id=resolved_snapshot_id,
        data_snapshot_id=resolved_data_snapshot_id,
        rule_version=resolved_rule_version,
        config_version=resolved_config_version,
        tenant_id=resolved_tenant_id,
        site_id=resolved_site_id,
    )


def restore_record(*, record: PersistenceRecord, model_type: type[ModelT]) -> ModelT:
    """Restore a domain model while preserving all model validation rules."""
    return deserialize_model(model_type, record.payload)


__all__ = [
    "build_record",
    "canonical_json",
    "deserialize_model",
    "model_hash",
    "payload_hash",
    "restore_record",
    "serialize_model",
]

