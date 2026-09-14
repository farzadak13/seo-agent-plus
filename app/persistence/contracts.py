from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.models.persistence import PersistenceRecord


class PersistenceConflictError(RuntimeError):
    """Raised when a persistence operation violates repository state."""


class PersistenceNotFoundError(LookupError):
    """Raised when an explicitly requested record does not exist."""


class Repository(Protocol):
    """Database-agnostic persistence boundary for immutable records."""

    def create(self, record: PersistenceRecord) -> PersistenceRecord:
        ...

    def get(self, *, aggregate_type: str, aggregate_id: str) -> PersistenceRecord | None:
        ...

    def list(self, *, aggregate_type: str | None = None, limit: int | None = None) -> Sequence[PersistenceRecord]:
        ...

    def replace(self, record: PersistenceRecord, *, expected_version: int) -> PersistenceRecord:
        ...


__all__ = ["PersistenceConflictError", "PersistenceNotFoundError", "Repository"]
