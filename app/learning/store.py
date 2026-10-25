"""Stage 39 — what each settled action taught, kept per tenant.

One feedback event per action, under an id derived from the action, so a
round repeated after a crash finds the event it wrote instead of adding a
second sample. The learning context a run sees is built from its own
tenant's events only: one customer's results never steer another's site.
"""
from __future__ import annotations

from hashlib import sha256

from app.learning.engine import build_learning_context
from app.models.learning import FeedbackEvent, LearningContext
from app.models.persistence import PersistenceRecord
from app.persistence.contracts import PersistenceConflictError, Repository


FEEDBACK_AGGREGATE_TYPE = "learning_feedback"
FEEDBACK_SCHEMA_VERSION = 1


def feedback_id_for(action_id: str) -> str:
    return f"feedback:{action_id}"


class FeedbackStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def add(self, event: FeedbackEvent, *, tenant_id: str) -> FeedbackEvent:
        """Store once. The same event again is a no-op; a different one under the same id is refused."""
        record = PersistenceRecord(
            record_id=f"{event.feedback_id}:v1",
            aggregate_type=FEEDBACK_AGGREGATE_TYPE,
            aggregate_id=event.feedback_id,
            schema_version=FEEDBACK_SCHEMA_VERSION,
            payload=event.model_dump(mode="json"),
            tenant_id=tenant_id,
            site_id=event.site_id,
        )
        try:
            self._repository.create(record)
        except PersistenceConflictError:
            existing = self._repository.get(
                aggregate_type=FEEDBACK_AGGREGATE_TYPE, aggregate_id=event.feedback_id
            )
            if existing is None or existing.tenant_id != tenant_id or existing.payload != record.payload:
                raise
        return event

    def list_for_tenant(self, tenant_id: str) -> list[FeedbackEvent]:
        events: list[FeedbackEvent] = []
        cursor = None
        while True:
            page = self._repository.query(
                tenant_id=tenant_id, aggregate_type=FEEDBACK_AGGREGATE_TYPE, limit=500, cursor=cursor
            )
            events.extend(FeedbackEvent.model_validate(record.payload) for record in page.records)
            if page.next_cursor is None:
                return events
            cursor = page.next_cursor

    def learning_context(self, tenant_id: str) -> LearningContext | None:
        """The tenant's learning so far, or None before its first settled action."""
        events = self.list_for_tenant(tenant_id)
        if not events:
            return None
        digest = sha256(
            "\n".join(sorted(event.feedback_id for event in events)).encode("utf-8")
        ).hexdigest()[:16]
        return build_learning_context(
            context_id=f"learning:{tenant_id}:{digest}", feedback_events=events
        )
