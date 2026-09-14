from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from app.models.persistence import PersistenceRecord
from app.models.snapshots import SnapshotMetadata
from app.persistence.serialization import build_record


def _snapshot_values(snapshot: SnapshotMetadata) -> dict[str, str]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "data_snapshot_id": snapshot.data_snapshot_id,
        "rule_version": snapshot.rule_version,
        "config_version": snapshot.config_version,
    }


def build_raw_gsc_record(
    *,
    run_id: str,
    raw_response: BaseModel,
) -> PersistenceRecord:
    return build_record(
        record_id=f"gsc-response:{run_id}",
        aggregate_type="raw_gsc_response",
        aggregate_id=raw_response.response_id,
        model=raw_response,
    )


def build_decision_pipeline_record(
    *,
    run_id: str,
    result: BaseModel,
    snapshot: SnapshotMetadata,
) -> PersistenceRecord:
    payload: dict[str, Any] = result.model_dump(mode="json")

    payload["_run_id"] = run_id
    payload["_persisted_at"] = datetime.now(timezone.utc).isoformat()

    return PersistenceRecord(
        record_id=f"decision-pipeline:{run_id}",
        aggregate_type="decision_pipeline_result",
        aggregate_id=run_id,
        version=1,
        schema_version=1,
        payload=payload,
        **_snapshot_values(snapshot),
    )


def build_run_record(
    *,
    run_id: str,
    site_id: str,
    snapshot: SnapshotMetadata,
    raw_gsc_record_id: str | None = None,
    decision_record_id: str | None = None,
    completed: bool = False,
    error: str | None = None,
) -> PersistenceRecord:
    from app.models.runs import PipelineRun, RunStatus

    status = (
        RunStatus.FAILED
        if error
        else (RunStatus.COMPLETED if completed else RunStatus.STARTED)
    )

    run = PipelineRun(
        run_id=run_id,
        site_id=site_id,
        status=status,
        snapshot=snapshot,
        completed_at=(
            datetime.now(timezone.utc)
            if completed or error
            else None
        ),
        raw_gsc_record_id=raw_gsc_record_id,
        decision_record_id=decision_record_id,
        error=error,
    )

    return PersistenceRecord(
        record_id=f"pipeline-run:{run_id}",
        aggregate_type="pipeline_run",
        aggregate_id=run_id,
        version=1,
        schema_version=1,
        payload=run.model_dump(mode="json"),
        **_snapshot_values(snapshot),
    )