"""An Action as the customer sees it: proposed, decided on, applied, undone.

``Action`` is the decision engine's output and knows nothing about who owns
it or what happened to it afterwards. ``ManagedAction`` wraps it with the
owner, the run that produced it, what the page looked like when it was
proposed, and an append-only history of every decision taken on it. That
history is what a customer reads when they ask "who changed my title, and
why".
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.models.actions import Action, ActionStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ActionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime = Field(default_factory=_now)
    status: ActionStatus
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    detail: dict = Field(default_factory=dict)


class AppliedChange(BaseModel):
    """What was written to the site, and what it replaced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_id: str = Field(min_length=1)
    change_id: str | None = None
    previous_value: str
    new_value: str
    applied_at: datetime = Field(default_factory=_now)


class ManagedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Action
    tenant_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    proposal_id: str | None = None

    # The page as it was when the proposal was made. Execution refuses to
    # overwrite a page that has changed since, because the customer approved
    # a replacement for *this* value, not for whatever is there now.
    observed_value: str | None = None

    applied: AppliedChange | None = None
    rolled_back: AppliedChange | None = None
    error: str | None = None

    history: list[ActionEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def action_id(self) -> str:
        return self.action.action_id

    @property
    def site_id(self) -> str:
        return self.action.site_id

    @property
    def status(self) -> ActionStatus:
        return self.action.status

    def record(
        self,
        *,
        action: Action,
        actor: str,
        reason: str,
        detail: dict | None = None,
        **changes,
    ) -> "ManagedAction":
        """Return a copy carrying ``action`` and one more history entry."""
        event = ActionEvent(
            status=action.status, actor=actor, reason=reason, detail=detail or {}
        )
        return self.model_copy(
            update={
                "action": action,
                "history": [*self.history, event],
                "updated_at": _now(),
                **changes,
            }
        )
