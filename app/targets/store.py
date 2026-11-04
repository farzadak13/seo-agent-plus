"""One record per site: its brief, its target words and how each has ranked.

The customer, the suggestion job and the weekly rank check all change the same
record, so every write names the version it was read at: a stale copy is a
conflict, never a silent undo of someone else's change.
"""
from __future__ import annotations

from collections.abc import Callable

from app.models.persistence import PersistenceRecord
from app.models.target_keywords import TargetKeywordSet
from app.persistence.contracts import PersistenceConflictError, Repository

TARGET_AGGREGATE_TYPE = "target_keywords"
SCHEMA_VERSION = 1
UPDATE_ATTEMPTS = 3


class TargetKeywordStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def read(self, site_id: str) -> tuple[TargetKeywordSet | None, int]:
        """The set and the version it was read at; version 0 when there is none yet."""
        record = self._repository.get(aggregate_type=TARGET_AGGREGATE_TYPE, aggregate_id=site_id)
        return (TargetKeywordSet.model_validate(record.payload), record.version) if record else (None, 0)

    def find(self, site_id: str) -> TargetKeywordSet | None:
        return self.read(site_id)[0]

    def read_or_new(self, site) -> tuple[TargetKeywordSet, int]:
        keywords, version = self.read(site.site_id)
        return keywords or TargetKeywordSet(site_id=site.site_id, tenant_id=site.principal_id), version

    def save(self, keywords: TargetKeywordSet, *, expected_version: int) -> TargetKeywordSet:
        """Raises PersistenceConflictError when the set changed since it was read."""
        record = PersistenceRecord(
            record_id=f"{TARGET_AGGREGATE_TYPE}:{keywords.site_id}:v{expected_version + 1}",
            aggregate_type=TARGET_AGGREGATE_TYPE,
            aggregate_id=keywords.site_id,
            schema_version=SCHEMA_VERSION,
            version=expected_version + 1,
            payload=keywords.model_dump(mode="json"),
            tenant_id=keywords.tenant_id,
            site_id=keywords.site_id,
        )
        if expected_version:
            self._repository.replace(record, expected_version=expected_version)
        else:
            self._repository.create(record)
        return keywords

    def update(
        self, site_id: str, change: Callable[[TargetKeywordSet], TargetKeywordSet | None]
    ) -> TargetKeywordSet | None:
        """Read, change, write; read again and redo the change if someone wrote in between.

        ``change`` returns None to write nothing. For jobs, whose change is
        worked out from what is there now, not from what a page showed.
        """
        for attempt in range(UPDATE_ATTEMPTS):
            current, version = self.read(site_id)
            if current is None:
                return None
            changed = change(current)
            if changed is None:
                return current
            try:
                return self.save(changed, expected_version=version)
            except PersistenceConflictError:
                if attempt == UPDATE_ATTEMPTS - 1:
                    raise
        return None

    def for_tenant(self, tenant_id: str) -> list[TargetKeywordSet]:
        """Each of a tenant's sites' sets, for its daily limits."""
        sets: list[TargetKeywordSet] = []
        cursor = None
        while True:
            page = self._repository.query(tenant_id=tenant_id, aggregate_type=TARGET_AGGREGATE_TYPE, limit=200, cursor=cursor)
            sets.extend(TargetKeywordSet.model_validate(record.payload) for record in page.records)
            if page.next_cursor is None:
                return sets
            cursor = page.next_cursor

    def list_all(self) -> list[TargetKeywordSet]:
        """Every site's set. Operational use only (the weekly rank check)."""
        return [
            TargetKeywordSet.model_validate(record.payload)
            for record in self._repository.list(aggregate_type=TARGET_AGGREGATE_TYPE)
        ]
