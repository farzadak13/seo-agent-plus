from datetime import date, datetime, timezone

import pytest

from app.models.evidence import DecisionEvidence
from app.models.observations import DataStatus, DailyObservation
from app.models.persistence import PersistenceRecord
from app.models.reconciliation import ReconciliationResult, ReconciliationStatus
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric
from app.persistence import (
    InMemoryRepository,
    PersistenceConflictError,
    PersistenceNotFoundError,
    build_record,
    model_hash,
    restore_record,
)


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-persistence-001",
        data_snapshot_id="data-persistence-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc),
    )


def make_observation() -> DailyObservation:
    return DailyObservation(
        date=date(2026, 9, 1),
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="seo",
        impressions=100,
        clicks=10,
        avg_position=5.0,
        data_status=DataStatus.OBSERVED,
    )


def make_url_metric() -> URLDailyMetric:
    return URLDailyMetric(
        date=date(2026, 9, 1),
        site_id="site-1",
        normalized_url="https://example.com/page",
        total_impressions=100,
        total_clicks=10,
        data_status=DataStatus.OBSERVED,
    )


def make_evidence() -> DecisionEvidence:
    observation = make_observation()
    url_metric = make_url_metric()
    return DecisionEvidence(
        site_id="site-1",
        normalized_url=observation.normalized_url,
        normalized_query=observation.normalized_query,
        baseline_observations=[observation],
        current_observations=[observation],
        baseline_url_metrics=[url_metric],
        current_url_metrics=[url_metric],
        baseline_daily_status=[(observation.date, DataStatus.OBSERVED)],
        current_daily_status=[(observation.date, DataStatus.OBSERVED)],
        reconciliation=ReconciliationResult(
            impression_status=ReconciliationStatus.MATCH,
            click_status=ReconciliationStatus.MATCH,
            query_impressions=100,
            url_impressions=100,
            query_clicks=10,
            url_clicks=10,
            impression_gap=0,
            click_gap=0,
            is_valid=True,
            is_partial=False,
        ),
        snapshot=make_snapshot(),
    )


def test_persistence_record_is_strict_and_frozen():
    record = PersistenceRecord(
        record_id="record-1",
        aggregate_type="decision_evidence",
        aggregate_id="evidence-1",
        payload={"value": 1},
    )
    with pytest.raises(ValueError):
        record.version = 2  # type: ignore[misc]
    with pytest.raises(ValueError):
        PersistenceRecord(
            record_id="record-2",
            aggregate_type="decision_evidence",
            aggregate_id="evidence-2",
            payload={},
            unexpected_field=True,
        )


def test_build_record_extracts_snapshot_metadata():
    record = build_record(
        record_id="record-evidence-1",
        aggregate_type="decision_evidence",
        aggregate_id="evidence-1",
        model=make_evidence(),
    )
    assert record.snapshot_id == "snapshot-persistence-001"
    assert record.data_snapshot_id == "data-persistence-001"
    assert record.rule_version == "rules-v1"
    assert record.config_version == "config-v1"
    assert record.payload["site_id"] == "site-1"


def test_decision_evidence_round_trip():
    evidence = make_evidence()
    record = build_record(
        record_id="record-evidence-2",
        aggregate_type="decision_evidence",
        aggregate_id="evidence-2",
        model=evidence,
    )
    restored = restore_record(record=record, model_type=DecisionEvidence)
    assert restored == evidence


def test_model_hash_is_stable():
    first = make_evidence()
    second = DecisionEvidence.model_validate(first.model_dump())
    assert model_hash(first) == model_hash(second)


def test_repository_create_get_and_list():
    repository = InMemoryRepository()
    record = build_record(
        record_id="record-3",
        aggregate_type="decision_evidence",
        aggregate_id="evidence-3",
        model=make_evidence(),
    )
    repository.create(record)
    assert repository.get(aggregate_type="decision_evidence", aggregate_id="evidence-3") == record
    assert repository.list(aggregate_type="decision_evidence") == [record]


def test_repository_rejects_duplicate_aggregate():
    repository = InMemoryRepository()
    record = build_record(
        record_id="record-4",
        aggregate_type="decision_evidence",
        aggregate_id="evidence-4",
        model=make_evidence(),
    )
    repository.create(record)
    with pytest.raises(PersistenceConflictError):
        repository.create(record)


def test_repository_replace_uses_optimistic_concurrency():
    repository = InMemoryRepository()
    first = build_record(
        record_id="record-5-v1",
        aggregate_type="decision_evidence",
        aggregate_id="evidence-5",
        model=make_evidence(),
        version=1,
    )
    repository.create(first)
    second = PersistenceRecord(
        record_id="record-5-v2",
        aggregate_type=first.aggregate_type,
        aggregate_id=first.aggregate_id,
        schema_version=first.schema_version,
        version=2,
        payload={**first.payload, "normalized_query": "seo updated"},
        snapshot_id=first.snapshot_id,
        data_snapshot_id=first.data_snapshot_id,
        rule_version=first.rule_version,
        config_version=first.config_version,
    )
    repository.replace(second, expected_version=1)
    assert repository.get(aggregate_type="decision_evidence", aggregate_id="evidence-5") == second


def test_repository_rejects_stale_expected_version():
    repository = InMemoryRepository()
    first = PersistenceRecord(
        record_id="record-6-v1",
        aggregate_type="test",
        aggregate_id="aggregate-6",
        payload={"state": "v1"},
    )
    repository.create(first)
    second = PersistenceRecord(
        record_id="record-6-v2",
        aggregate_type="test",
        aggregate_id="aggregate-6",
        payload={"state": "v2"},
        version=2,
    )
    repository.replace(second, expected_version=1)
    third = PersistenceRecord(
        record_id="record-6-v3",
        aggregate_type="test",
        aggregate_id="aggregate-6",
        payload={"state": "v3"},
        version=3,
    )
    with pytest.raises(PersistenceConflictError):
        repository.replace(third, expected_version=1)


def test_repository_replace_requires_existing_record():
    repository = InMemoryRepository()
    missing = PersistenceRecord(
        record_id="record-missing-v2",
        aggregate_type="test",
        aggregate_id="missing",
        payload={"state": "v2"},
        version=2,
    )
    with pytest.raises(PersistenceNotFoundError):
        repository.replace(missing, expected_version=1)


def test_repository_limit_is_validated():
    repository = InMemoryRepository()
    with pytest.raises(ValueError):
        repository.list(limit=0)


def test_repository_is_database_agnostic():
    record = PersistenceRecord(
        record_id="record-7",
        aggregate_type="learning_context",
        aggregate_id="learning-7",
        payload={"status": "established", "confidence": 0.8},
    )
    repository = InMemoryRepository()
    repository.create(record)
    loaded = repository.get(aggregate_type="learning_context", aggregate_id="learning-7")
    assert loaded is not None
    assert loaded.payload["status"] == "established"
    assert loaded.version == 1
