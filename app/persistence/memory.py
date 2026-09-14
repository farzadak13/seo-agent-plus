from __future__ import annotations

from app.models.persistence import PersistenceRecord
from app.persistence.contracts import PersistenceConflictError, PersistenceNotFoundError, Repository


class InMemoryRepository:
    """Deterministic repository for tests and local development only."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], PersistenceRecord] = {}

    def create(self, record: PersistenceRecord) -> PersistenceRecord:
        key = (record.aggregate_type, record.aggregate_id)
        if key in self._records:
            raise PersistenceConflictError(
                f"aggregate already exists: {record.aggregate_type}:{record.aggregate_id}"
            )
        self._records[key] = record
        return record

    def get(self, *, aggregate_type: str, aggregate_id: str) -> PersistenceRecord | None:
        return self._records.get((aggregate_type, aggregate_id))

    def list(self, *, aggregate_type: str | None = None, limit: int | None = None) -> list[PersistenceRecord]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")

        records = [
            record
            for record in self._records.values()
            if aggregate_type is None or record.aggregate_type == aggregate_type
        ]
        records.sort(key=lambda item: (item.aggregate_type, item.aggregate_id, item.version))
        return records if limit is None else records[:limit]

    def replace(self, record: PersistenceRecord, *, expected_version: int) -> PersistenceRecord:
        key = (record.aggregate_type, record.aggregate_id)
        current = self._records.get(key)
        if current is None:
            raise PersistenceNotFoundError(
                f"aggregate not found: {record.aggregate_type}:{record.aggregate_id}"
            )
        if current.version != expected_version:
            raise PersistenceConflictError(
                f"optimistic concurrency conflict: expected version {expected_version}, found {current.version}"
            )
        if record.version != expected_version + 1:
            raise PersistenceConflictError(
                f"replacement version must increment by exactly one: expected {expected_version + 1}, found {record.version}"
            )
        self._records[key] = record
        return record


def repository_as_protocol(repository: InMemoryRepository) -> Repository:
    return repository


__all__ = ["InMemoryRepository", "repository_as_protocol"]
