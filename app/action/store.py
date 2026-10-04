from __future__ import annotations

from app.models.managed_action import ManagedAction
from app.models.persistence import PersistenceRecord
from app.persistence.contracts import (
    PersistenceConflictError,
    PersistenceNotFoundError,
    RecordPage,
    Repository,
)


class ActionChangedError(PersistenceConflictError):
    """The action was changed by someone else since it was read."""


ACTION_AGGREGATE_TYPE = "managed_action"
ACTION_SCHEMA_VERSION = 1


class ManagedActionStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, managed: ManagedAction) -> ManagedAction:
        self._repository.create(self._to_record(managed, version=1))
        return managed

    def find(self, action_id: str) -> ManagedAction | None:
        record = self._repository.get(
            aggregate_type=ACTION_AGGREGATE_TYPE, aggregate_id=action_id
        )
        if record is None:
            return None
        return ManagedAction.model_validate(record.payload)

    def get(self, action_id: str) -> ManagedAction:
        managed = self.find(action_id)
        if managed is None:
            raise PersistenceNotFoundError(f"action not found: {action_id}")
        return managed

    def update(self, managed: ManagedAction) -> ManagedAction:
        current = self._repository.get(
            aggregate_type=ACTION_AGGREGATE_TYPE, aggregate_id=managed.action_id
        )
        if current is None:
            raise PersistenceNotFoundError(f"action not found: {managed.action_id}")
        # History is append-only, so it doubles as the version the caller
        # read: a write that does not extend the stored history was built from
        # a stale copy and would silently undo someone else's decision (a
        # rejection landing while an execution was starting, for example).
        stored = ManagedAction.model_validate(current.payload).history
        if managed.history[: len(stored)] != stored:
            raise ActionChangedError(f"action changed since it was read: {managed.action_id}")
        self._repository.replace(
            self._to_record(managed, version=current.version + 1),
            expected_version=current.version,
        )
        return managed

    def query(
        self,
        *,
        tenant_id: str,
        site_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[ManagedAction], str | None]:
        page: RecordPage = self._repository.query(
            tenant_id=tenant_id,
            aggregate_type=ACTION_AGGREGATE_TYPE,
            site_id=site_id,
            limit=limit,
            cursor=cursor,
        )
        return (
            [ManagedAction.model_validate(record.payload) for record in page.records],
            page.next_cursor,
        )

    @staticmethod
    def _to_record(managed: ManagedAction, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"action:{managed.action_id}:v{version}",
            aggregate_type=ACTION_AGGREGATE_TYPE,
            aggregate_id=managed.action_id,
            schema_version=ACTION_SCHEMA_VERSION,
            version=version,
            payload=managed.model_dump(mode="json"),
            tenant_id=managed.tenant_id,
            site_id=managed.site_id,
        )
