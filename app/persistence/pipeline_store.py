from __future__ import annotations

from app.models.gsc import RawGSCResponse
from app.models.persistence import PersistenceRecord
from app.models.pipeline import DecisionPipelineResult
from app.models.runs import PipelineRun
from app.models.snapshots import SnapshotMetadata
from app.persistence.aggregates import (
    build_decision_pipeline_record,
    build_raw_gsc_record,
    build_run_record,
)
from app.persistence.contracts import (
    PersistenceConflictError,
    PersistenceNotFoundError,
    Repository,
)


class PipelinePersistenceStore:
    """
    Application-level coordinator for pipeline persistence.

    Database-specific concerns stay inside the repository implementation.
    """

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def start_run(
        self,
        *,
        run_id: str,
        site_id: str,
        snapshot: SnapshotMetadata,
    ) -> PipelineRun:
        record = build_run_record(
            run_id=run_id,
            site_id=site_id,
            snapshot=snapshot,
        )

        self._repository.create(record)

        return self._run_from_record(record)

    def persist_gsc_response(
        self,
        *,
        run_id: str,
        response: RawGSCResponse,
    ) -> PersistenceRecord:
        record = build_raw_gsc_record(
            run_id=run_id,
            raw_response=response,
        )

        self._repository.create(record)

        return record

    def persist_pipeline_result(
        self,
        *,
        run_id: str,
        result: DecisionPipelineResult,
        snapshot: SnapshotMetadata,
    ) -> PersistenceRecord:
        record = build_decision_pipeline_record(
            run_id=run_id,
            result=result,
            snapshot=snapshot,
        )

        self._repository.create(record)

        return record

    def complete_run(
        self,
        *,
        run_id: str,
        site_id: str,
        snapshot: SnapshotMetadata,
        raw_gsc_record_id: str,
        decision_record_id: str,
    ) -> PipelineRun:
        current = self._require_run_record(run_id)

        if current.version != 1:
            raise PersistenceConflictError(
                "unexpected pipeline run version"
            )

        next_record = build_run_record(
            run_id=run_id,
            site_id=site_id,
            snapshot=snapshot,
            raw_gsc_record_id=raw_gsc_record_id,
            decision_record_id=decision_record_id,
            completed=True,
        ).model_copy(
            update={"version": 2}
        )

        self._repository.replace(
            next_record,
            expected_version=current.version,
        )

        return self._run_from_record(next_record)

    def fail_run(
        self,
        *,
        run_id: str,
        site_id: str,
        snapshot: SnapshotMetadata,
        error: str,
    ) -> PipelineRun:
        if not error.strip():
            raise ValueError("error must not be empty")

        current = self._require_run_record(run_id)

        next_record = build_run_record(
            run_id=run_id,
            site_id=site_id,
            snapshot=snapshot,
            error=error,
        ).model_copy(
            update={"version": current.version + 1}
        )

        self._repository.replace(
            next_record,
            expected_version=current.version,
        )

        return self._run_from_record(next_record)

    def recover_run(
        self,
        *,
        run_id: str,
    ) -> PipelineRun:
        return self._run_from_record(
            self._require_run_record(run_id)
        )

    def load_pipeline_result(
        self,
        *,
        run_id: str,
    ) -> DecisionPipelineResult:
        record = self._repository.get(
            aggregate_type="decision_pipeline_result",
            aggregate_id=run_id,
        )

        if record is None:
            raise PersistenceNotFoundError(
                f"decision pipeline result not found: {run_id}"
            )

        payload = dict(record.payload)

        payload.pop("_run_id", None)
        payload.pop("_persisted_at", None)

        return DecisionPipelineResult.model_validate(payload)

    def _require_run_record(
        self,
        run_id: str,
    ) -> PersistenceRecord:
        record = self._repository.get(
            aggregate_type="pipeline_run",
            aggregate_id=run_id,
        )

        if record is None:
            raise PersistenceNotFoundError(
                f"pipeline run not found: {run_id}"
            )

        return record

    @staticmethod
    def _run_from_record(
        record: PersistenceRecord,
    ) -> PipelineRun:
        return PipelineRun.model_validate(record.payload)