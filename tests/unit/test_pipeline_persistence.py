from datetime import date, datetime, timezone

import pytest

from app.models.gsc import RawGSCResponse
from app.models.pipeline import DecisionPipelineResult, PipelineStatus
from app.models.runs import PipelineRun, RunStatus
from app.models.snapshots import SnapshotMetadata
from app.persistence import (
    InMemoryRepository,
    PersistenceConflictError,
    PersistenceNotFoundError,
    PipelinePersistenceStore,
)


def snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-runtime-001",
        data_snapshot_id="data-runtime-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            8,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )


def raw_response() -> RawGSCResponse:
    return RawGSCResponse(
        response_id="response-runtime-001",
        site_id="site-1",
        fetch_date=date(2026, 9, 8),
        raw_payload={
            "startDate": "2026-09-07",
            "endDate": "2026-09-07",
            "rows": [],
        },
        created_at=datetime(
            2026,
            9,
            8,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )


def pipeline_result() -> DecisionPipelineResult:
    return DecisionPipelineResult(
        status=PipelineStatus.REJECTED_DATA_QUALITY,
        ingestion={
            "site_id": "site-1",
            "response_id": "response-runtime-001",
            "normalized_rows": [],
            "observations": [],
            "url_daily_metrics": [],
            "daily_status": [
                (
                    date(2026, 9, 7),
                    "MISSING_UNKNOWN",
                )
            ],
        },
        reconciliation={
            "impression_status": "match",
            "click_status": "match",
            "query_impressions": 0,
            "url_impressions": 0,
            "query_clicks": 0,
            "url_clicks": 0,
            "impression_gap": 0,
            "click_gap": 0,
            "is_valid": True,
            "is_partial": False,
        },
        evidence=None,
        features=None,
        signals=[],
        classification={
            "status": "REJECT",
            "confidence": 0,
            "reasons": ["insufficient_data_quality"],
            "signal_count": 0,
            "risk_count": 0,
        },
        candidate=None,
        opportunity=None,
        strategy=None,
        action=None,
        investigation_queue=[],
    )


def test_start_run_persists_identity():
    store = PipelinePersistenceStore(
        InMemoryRepository()
    )

    run = store.start_run(
        run_id="run-001",
        site_id="site-1",
        snapshot=snapshot(),
    )

    assert isinstance(run, PipelineRun)
    assert run.status == RunStatus.STARTED
    assert run.snapshot.snapshot_id == "snapshot-runtime-001"


def test_duplicate_run_is_rejected():
    repo = InMemoryRepository()
    store = PipelinePersistenceStore(repo)

    store.start_run(
        run_id="run-002",
        site_id="site-1",
        snapshot=snapshot(),
    )

    with pytest.raises(PersistenceConflictError):
        store.start_run(
            run_id="run-002",
            site_id="site-1",
            snapshot=snapshot(),
        )


def test_raw_gsc_payload_is_persisted():
    repo = InMemoryRepository()
    store = PipelinePersistenceStore(repo)

    store.start_run(
        run_id="run-003",
        site_id="site-1",
        snapshot=snapshot(),
    )

    record = store.persist_gsc_response(
        run_id="run-003",
        response=raw_response(),
    )

    assert record.aggregate_type == "raw_gsc_response"
    assert record.aggregate_id == "response-runtime-001"
    assert record.payload["raw_payload"]["rows"] == []


def test_pipeline_result_is_persisted():
    repo = InMemoryRepository()
    store = PipelinePersistenceStore(repo)

    record = store.persist_pipeline_result(
        run_id="run-004",
        result=pipeline_result(),
        snapshot=snapshot(),
    )

    assert record.aggregate_type == "decision_pipeline_result"
    assert record.aggregate_id == "run-004"
    assert record.snapshot_id == "snapshot-runtime-001"
    assert record.payload["_run_id"] == "run-004"


def test_complete_run_is_version_two():
    repo = InMemoryRepository()
    store = PipelinePersistenceStore(repo)

    store.start_run(
        run_id="run-005",
        site_id="site-1",
        snapshot=snapshot(),
    )

    result = store.complete_run(
        run_id="run-005",
        site_id="site-1",
        snapshot=snapshot(),
        raw_gsc_record_id="response-runtime-001",
        decision_record_id="run-005",
    )

    persisted = repo.get(
        aggregate_type="pipeline_run",
        aggregate_id="run-005",
    )

    assert result.status == RunStatus.COMPLETED
    assert persisted is not None
    assert persisted.version == 2


def test_failed_run_is_versioned():
    repo = InMemoryRepository()
    store = PipelinePersistenceStore(repo)

    store.start_run(
        run_id="run-006",
        site_id="site-1",
        snapshot=snapshot(),
    )

    result = store.fail_run(
        run_id="run-006",
        site_id="site-1",
        snapshot=snapshot(),
        error="temporary acquisition failure",
    )

    persisted = repo.get(
        aggregate_type="pipeline_run",
        aggregate_id="run-006",
    )

    assert result.status == RunStatus.FAILED
    assert result.error == "temporary acquisition failure"
    assert persisted is not None
    assert persisted.version == 2


def test_fail_run_requires_error():
    repo = InMemoryRepository()
    store = PipelinePersistenceStore(repo)

    store.start_run(
        run_id="run-007",
        site_id="site-1",
        snapshot=snapshot(),
    )

    with pytest.raises(ValueError):
        store.fail_run(
            run_id="run-007",
            site_id="site-1",
            snapshot=snapshot(),
            error=" ",
        )


def test_restart_recovery_reads_persisted_state():
    repo = InMemoryRepository()

    PipelinePersistenceStore(repo).start_run(
        run_id="run-008",
        site_id="site-1",
        snapshot=snapshot(),
    )

    recovered = PipelinePersistenceStore(repo).recover_run(
        run_id="run-008"
    )

    assert recovered.run_id == "run-008"
    assert recovered.status == RunStatus.STARTED


def test_pipeline_result_round_trip():
    repo = InMemoryRepository()
    store = PipelinePersistenceStore(repo)

    result = pipeline_result()

    store.persist_pipeline_result(
        run_id="run-009",
        result=result,
        snapshot=snapshot(),
    )

    assert (
        store.load_pipeline_result(run_id="run-009")
        == result
    )


def test_missing_run_is_explicit():
    with pytest.raises(PersistenceNotFoundError):
        PipelinePersistenceStore(
            InMemoryRepository()
        ).recover_run(run_id="missing")


def test_missing_result_is_explicit():
    with pytest.raises(PersistenceNotFoundError):
        PipelinePersistenceStore(
            InMemoryRepository()
        ).load_pipeline_result(run_id="missing")