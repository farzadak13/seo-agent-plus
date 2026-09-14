from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.actions import Action, ActionStatus
from app.models.outcomes import OutcomeEvaluationResult, OutcomeStatus


class ActionLifecycleResultStatus(StrEnum):
    TRANSITIONED = "transitioned"
    REJECTED = "rejected"


class ActionLifecycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    status: ActionLifecycleResultStatus
    previous_status: ActionStatus
    current_status: ActionStatus
    reasons: list[str]
    evidence: dict


_OUTCOME_TO_ACTION_STATUS = {
    OutcomeStatus.SUCCESS: ActionStatus.SUCCEEDED,
    OutcomeStatus.FAILURE: ActionStatus.FAILED,
    OutcomeStatus.INCONCLUSIVE: ActionStatus.INCONCLUSIVE,
}


def transition_action(
    *,
    action: Action,
    target_status: ActionStatus,
    reason: str,
    evidence: dict | None = None,
) -> ActionLifecycleResult:
    from app.action.state_machine import transition

    previous_status = action.status
    new_status = transition(previous_status, target_status)

    updated_action = action.model_copy(
        update={
            "status": new_status,
            "reasons": [*action.reasons, reason],
            "evidence": {
                **action.evidence,
                **(evidence or {}),
                "previous_status": previous_status.value,
                "current_status": new_status.value,
                "transition_reason": reason,
            },
        }
    )

    return ActionLifecycleResult(
        action=updated_action,
        status=ActionLifecycleResultStatus.TRANSITIONED,
        previous_status=previous_status,
        current_status=new_status,
        reasons=[reason],
        evidence=evidence or {},
    )


def apply_outcome(
    *,
    action: Action,
    outcome: OutcomeEvaluationResult,
) -> ActionLifecycleResult:
    if action.status != ActionStatus.EVALUATING:
        raise ValueError(
            "Outcome can only be applied when action is in EVALUATING state."
        )

    if outcome.action_id != action.action_id:
        raise ValueError("Outcome action_id does not match action.")

    target_status = _OUTCOME_TO_ACTION_STATUS[outcome.status]

    return transition_action(
        action=action,
        target_status=target_status,
        reason=f"outcome_{outcome.status.value}",
        evidence={
            "outcome_id": outcome.outcome_id,
            "outcome_status": outcome.status.value,
            "primary_metric": outcome.primary_metric.value,
        },
    )
