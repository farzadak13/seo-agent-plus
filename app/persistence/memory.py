from __future__ import annotations

from app.models.persistence import PersistenceRecord
from app.persistence.contracts import (
    PersistenceConflictError,
    PersistenceNotFoundError,
    RecordPage,
    Repository,
)
from app.persistence.cursor import decode_cursor, encode_cursor


class InMemoryRepository:
    """Deterministic repository for tests and local development only."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], PersistenceRecord] = {}
        # migrations/001 declares UNIQUE (record_id). A double that allows what
        # the real schema forbids is how a reused record_id survived the whole
        # unit suite and only failed against PostgreSQL.
        self._record_ids: dict[str, tuple[str, str, int]] = {}

    def _claim_record_id(self, record: PersistenceRecord) -> None:
        identity = (record.aggregate_type, record.aggregate_id, record.version)
        owner = self._record_ids.get(record.record_id)
        if owner is not None and owner != identity:
            raise PersistenceConflictError(
                f"record_id is already used by another row: {record.record_id}. "
                "Every version needs its own record_id."
            )
        self._record_ids[record.record_id] = identity

    def create(self, record: PersistenceRecord) -> PersistenceRecord:
        key = (record.aggregate_type, record.aggregate_id)
        if key in self._records:
            raise PersistenceConflictError(
                f"aggregate already exists: {record.aggregate_type}:{record.aggregate_id}"
            )
        self._claim_record_id(record)
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

    def query(
        self,
        *,
        tenant_id: str,
        aggregate_type: str | None = None,
        site_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> RecordPage:
        """Mirrors the PostgreSQL ordering and keyset so tests match production."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required for a scoped query")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")

        records = [
            record
            for record in self._records.values()
            if record.tenant_id == tenant_id
            and (aggregate_type is None or record.aggregate_type == aggregate_type)
            and (site_id is None or record.site_id == site_id)
        ]
        records.sort(key=lambda item: (item.created_at, item.aggregate_id), reverse=True)

        if cursor is not None:
            cursor_created_at, cursor_aggregate_id = decode_cursor(cursor)
            records = [
                record
                for record in records
                if (record.created_at, record.aggregate_id) < (cursor_created_at, cursor_aggregate_id)
            ]

        page = records[:limit]
        next_cursor = None
        if len(records) > limit and page:
            last = page[-1]
            next_cursor = encode_cursor(
                created_at=last.created_at,
                aggregate_id=last.aggregate_id,
            )
        return RecordPage(records=page, next_cursor=next_cursor)

    def ping(self) -> None:
        return None

    def replace(self, record: PersistenceRecord, *, expected_version: int) -> PersistenceRecord:
        key = (record.aggregate_type, record.aggregate_id)
        current = self._records.get(key)
        if current is None:
            raise PersistenceNotFoundError(
                f"aggregate not found: {record.aggregate_type}:{record.aggregate_id}"
            )
        if current.tenant_id != record.tenant_id:
            raise PersistenceConflictError(
                f"aggregate tenant is immutable: {record.aggregate_type}:{record.aggregate_id}"
            )
        if current.version != expected_version:
            raise PersistenceConflictError(
                f"optimistic concurrency conflict: expected version {expected_version}, found {current.version}"
            )
        if record.version != expected_version + 1:
            raise PersistenceConflictError(
                f"replacement version must increment by exactly one: expected {expected_version + 1}, found {record.version}"
            )
        self._claim_record_id(record)
        self._records[key] = record
        return record


def repository_as_protocol(repository: InMemoryRepository) -> Repository:
    return repository


__all__ = ["InMemoryRepository", "repository_as_protocol"]
