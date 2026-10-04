"""Stage 37 — a person decides before anything touches a customer's site.

Every function here is pure: it takes a ManagedAction and returns the next
one, or raises ApprovalError. Persistence and job creation happen in the
caller, inside one transaction, so an approval is never recorded without the
job that carries it out.
"""
from __future__ import annotations

from app.models.action_lifecycle import transition_action
from app.models.actions import Action, ActionStatus, ActionType
from app.models.managed_action import ActionEvent, ManagedAction


SYSTEM_ACTOR = "system"
MAX_TITLE_LENGTH = 300

# A change that has reached the site can be undone until its outcome is
# settled. After SUCCEEDED or INCONCLUSIVE it is history, and undoing it is a
# new decision rather than a rollback.
ROLLBACKABLE_STATUSES = frozenset(
    {
        ActionStatus.EXECUTED,
        ActionStatus.WAITING_FOR_RECRAWL,
        ActionStatus.MEASUREMENT_WINDOW_ACTIVE,
        ActionStatus.EVALUATING,
        ActionStatus.FAILED,
    }
)


class ApprovalError(ValueError):
    """The requested decision is not valid for this action's current state."""


def propose_title_change(
    *,
    action: Action,
    run_id: str,
    tenant_id: str,
    proposal_id: str,
    proposed_title: str,
    current_title: str | None,
    provider_id: str | None = None,
) -> ManagedAction:
    """Turn the engine's title action and the LLM's proposal into a pending decision."""
    if action.action_type != ActionType.OPTIMIZE_TITLE:
        raise ApprovalError(
            f"A title proposal needs an optimize_title action, got {action.action_type.value}."
        )
    title = _clean_title(proposed_title)

    # The id comes from the run, not the candidate: the same candidate is
    # proposed again by every later run, and each proposal is its own decision.
    pending = action.model_copy(
        update={
            "action_id": f"action:{run_id}",
            "status": ActionStatus.AWAITING_APPROVAL,
            "requires_approval": True,
            "parameters": {
                **action.parameters,
                "recommended_title": title,
                "expected_title": title,
                "proposal_id": proposal_id,
            },
        }
    )
    return ManagedAction(
        action=pending,
        tenant_id=tenant_id,
        run_id=run_id,
        proposal_id=proposal_id,
        observed_value=current_title,
        history=[
            ActionEvent(
                status=ActionStatus.AWAITING_APPROVAL,
                actor=SYSTEM_ACTOR,
                reason="title_proposal_generated",
                detail={"proposal_id": proposal_id, "provider_id": provider_id},
            )
        ],
    )


def approve(
    managed: ManagedAction,
    *,
    actor: str,
    title: str | None = None,
    note: str | None = None,
) -> ManagedAction:
    """Approve as proposed, or approve an edited title in its place."""
    _require_status(managed, {ActionStatus.AWAITING_APPROVAL}, "approved")
    action = managed.action
    detail: dict = {}
    if note:
        detail["note"] = note
    if title is not None:
        edited = _clean_title(title)
        if edited != action.parameters.get("recommended_title"):
            detail["proposed_title"] = action.parameters.get("recommended_title")
            detail["edited_title"] = edited
            action = action.model_copy(
                update={
                    "parameters": {
                        **action.parameters,
                        "recommended_title": edited,
                        "expected_title": edited,
                        "edited_by_reviewer": True,
                    }
                }
            )
    moved = transition_action(
        action=action, target_status=ActionStatus.APPROVED, reason="approved"
    ).action
    return managed.record(action=moved, actor=actor, reason="approved", detail=detail)


def reject(managed: ManagedAction, *, actor: str, reason: str | None = None) -> ManagedAction:
    _require_status(
        managed, {ActionStatus.AWAITING_APPROVAL, ActionStatus.APPROVED}, "rejected"
    )
    moved = transition_action(
        action=managed.action, target_status=ActionStatus.REJECTED, reason="rejected"
    ).action
    return managed.record(
        action=moved,
        actor=actor,
        reason="rejected",
        detail={"note": reason} if reason else {},
    )


def check_rollback(managed: ManagedAction) -> None:
    """Raise unless there is an applied change that can still be undone."""
    if managed.applied is None:
        raise ApprovalError("Nothing was applied to the site, so there is nothing to roll back.")
    if managed.rolled_back is not None:
        raise ApprovalError("This change has already been rolled back.")
    if managed.status not in ROLLBACKABLE_STATUSES:
        raise ApprovalError(
            f"An action in status {managed.status.value} cannot be rolled back."
        )


def _require_status(managed: ManagedAction, allowed: set[ActionStatus], verb: str) -> None:
    if managed.status not in allowed:
        raise ApprovalError(
            f"An action in status {managed.status.value} cannot be {verb}."
        )


def _clean_title(title: str) -> str:
    cleaned = " ".join(str(title).split())
    if not cleaned:
        raise ApprovalError("The title must not be empty.")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise ApprovalError(f"The title must be at most {MAX_TITLE_LENGTH} characters.")
    return cleaned
