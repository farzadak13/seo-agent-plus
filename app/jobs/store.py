from __future__ import annotations

from app.models.jobs import Job, JobStatus
from app.models.persistence import PersistenceRecord
from app.persistence.contracts import (
    PersistenceNotFoundError,
    Repository,
)


JOB_AGGREGATE_TYPE = "job"
JOB_SCHEMA_VERSION = 1


class JobStore:
    """
    Persistence-backed job repository.

    Jobs are stored through the same repository abstraction already used by
    the SEO Agent persistence layer. No SQL or database details live here.
    """

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, job: Job) -> Job:
        record = self._to_record(job, version=1)
        self._repository.create(record)
        return job

    def get(self, job_id: str) -> Job:
        record = self._repository.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job_id,
        )
        if record is None:
            raise PersistenceNotFoundError(
                f"job not found: {job_id}"
            )
        return self._from_record(record)

    def update(self, job: Job) -> Job:
        current = self._repository.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.job_id,
        )
        if current is None:
            raise PersistenceNotFoundError(
                f"job not found: {job.job_id}"
            )
        replacement = self._to_record(
            job,
            version=current.version + 1,
        )
        self._repository.replace(
            replacement,
            expected_version=current.version,
        )
        return job

    def list_queued(self, *, limit: int = 1) -> list[Job]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        records = self._repository.list(
            aggregate_type=JOB_AGGREGATE_TYPE,
        )
        jobs = [self._from_record(record) for record in records]
        queued = [
            job
            for job in jobs
            if job.status == JobStatus.QUEUED
        ]
        queued.sort(key=lambda job: job.created_at)
        return queued[:limit]

    def recover_running_jobs(self) -> list[Job]:
        records = self._repository.list(
            aggregate_type=JOB_AGGREGATE_TYPE,
        )
        recovered: list[Job] = []

        for record in records:
            job = self._from_record(record)
            if job.status != JobStatus.RUNNING:
                continue

            recovered_job = job.retry_or_fail(
                "Recovered after worker restart."
                if job.attempt_count < job.max_attempts
                else "Worker restarted after maximum attempts were exhausted."
            )

            self.update(recovered_job)
            recovered.append(recovered_job)

        return recovered

    @staticmethod
    def _to_record(job: Job, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"job:{job.job_id}:v{version}",
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.job_id,
            schema_version=JOB_SCHEMA_VERSION,
            version=version,
            payload=job.model_dump(mode="json"),
            tenant_id=job.principal_id,
            site_id=job.payload.get("site_id"),
        )

    @staticmethod
    def _from_record(record: PersistenceRecord) -> Job:
        return Job.model_validate(record.payload)
