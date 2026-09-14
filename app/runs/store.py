from __future__ import annotations

from app.models.persistence import PersistenceRecord
from app.models.runs import SEORun
from app.persistence.contracts import PersistenceNotFoundError, Repository


RUN_AGGREGATE_TYPE = "seo_run"
RUN_SCHEMA_VERSION = 1


class RunStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, run: SEORun) -> SEORun:
        self._repository.create(self._to_record(run, version=1))
        return run

    def get(self, run_id: str) -> SEORun:
        record = self._repository.get(
            aggregate_type=RUN_AGGREGATE_TYPE,
            aggregate_id=run_id,
        )
        if record is None:
            raise PersistenceNotFoundError(f"run not found: {run_id}")
        return SEORun.model_validate(record.payload)

    def update(self, run: SEORun) -> SEORun:
        current = self._repository.get(
            aggregate_type=RUN_AGGREGATE_TYPE,
            aggregate_id=run.run_id,
        )
        if current is None:
            raise PersistenceNotFoundError(f"run not found: {run.run_id}")
        self._repository.replace(
            self._to_record(run, version=current.version + 1),
            expected_version=current.version,
        )
        return run

    @staticmethod
    def _to_record(run: SEORun, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"run:{run.run_id}:v{version}",
            aggregate_type=RUN_AGGREGATE_TYPE,
            aggregate_id=run.run_id,
            schema_version=RUN_SCHEMA_VERSION,
            version=version,
            payload=run.model_dump(mode="json"),
            tenant_id=run.principal_id,
            site_id=run.site_id,
        )
