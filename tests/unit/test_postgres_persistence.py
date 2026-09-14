from datetime import datetime, timezone
import types

import pytest

from app.models.persistence import PersistenceRecord


def make_record(version: int = 1) -> PersistenceRecord:
    return PersistenceRecord(
        record_id=f"record-{version}",
        aggregate_type="decision_evidence",
        aggregate_id="evidence-1",
        version=version,
        payload={"value": version},
        created_at=datetime(
            2026,
            9,
            8,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )


def test_postgres_repository_requires_psycopg(monkeypatch):
    import app.persistence.postgres as module

    original = module.psycopg
    original_error = module._PSYCOPG_IMPORT_ERROR

    monkeypatch.setattr(
        module,
        "psycopg",
        None,
    )
    monkeypatch.setattr(
        module,
        "_PSYCOPG_IMPORT_ERROR",
        ImportError("missing psycopg"),
    )

    with pytest.raises(RuntimeError, match="psycopg"):
        module.PostgresRepository("postgresql://example")

    monkeypatch.setattr(module, "psycopg", original)
    monkeypatch.setattr(
        module,
        "_PSYCOPG_IMPORT_ERROR",
        original_error,
    )


def test_postgres_repository_exposes_database_agnostic_contract():
    import app.persistence.postgres as module

    assert hasattr(module.PostgresRepository, "create")
    assert hasattr(module.PostgresRepository, "get")
    assert hasattr(module.PostgresRepository, "list")
    assert hasattr(module.PostgresRepository, "replace")


def test_postgres_repository_serializes_payload_as_json():
    import app.persistence.postgres as module

    assert module._json_dumps(
        {"b": 2, "a": "value"},
    ) == '{"b":2,"a":"value"}'


def test_persistence_record_is_compatible_with_postgres_shape():
    record = make_record()

    payload = record.model_dump(mode="json")

    assert payload["aggregate_type"] == "decision_evidence"
    assert payload["aggregate_id"] == "evidence-1"
    assert payload["version"] == 1
    assert payload["payload"] == {"value": 1}


def test_migration_file_defines_append_only_composite_key():
    from pathlib import Path

    migration = Path(
        "migrations/001_persistence_foundation.sql"
    ).read_text(encoding="utf-8")

    assert "PRIMARY KEY (" in migration
    assert "aggregate_type" in migration
    assert "aggregate_id" in migration
    assert "version" in migration
    assert "JSONB" in migration
