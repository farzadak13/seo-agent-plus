from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.models.persistence import PersistenceRecord


class PersistenceConflictError(RuntimeError):
    """Raised when a persistence operation violates repository state."""


class PersistenceNotFoundError(LookupError):
    """Raised when an explicitly requested record does not exist."""


class InvalidCursorError(ValueError):
    """Raised when a pagination cursor cannot be decoded."""


@dataclass(frozen=True)
class RecordPage:
    """One page of records, newest first.

    ``next_cursor`` is None when the page is the last one. Ordering is by last
    write time, so a record updated during paging can move between pages; the
    cursor guarantees no page is skipped, not that the set is frozen.
    """

    records: Sequence[PersistenceRecord]
    next_cursor: str | None = None


class Repository(Protocol):
    """Database-agnostic persistence boundary for immutable records."""

    def create(self, record: PersistenceRecord) -> PersistenceRecord:
        ...

    def get(self, *, aggregate_type: str, aggregate_id: str) -> PersistenceRecord | None:
        ...

    def list(self, *, aggregate_type: str | None = None, limit: int | None = None) -> Sequence[PersistenceRecord]:
        """Unscoped listing. Operational use only — never serve tenant data from this."""
        ...

    def query(
        self,
        *,
        tenant_id: str,
        aggregate_type: str | None = None,
        site_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> RecordPage:
        """Tenant-scoped, paginated listing.

        ``tenant_id`` is required and has no default so no caller can read
        across tenants by omission.
        """
        ...

    def replace(self, record: PersistenceRecord, *, expected_version: int) -> PersistenceRecord:
        ...

    def ping(self) -> None:
        """Raise when the store is unreachable. Cheap enough for a health check."""
        ...


__all__ = [
    "InvalidCursorError",
    "PersistenceConflictError",
    "PersistenceNotFoundError",
    "RecordPage",
    "Repository",
]
