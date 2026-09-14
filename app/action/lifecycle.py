from app.action.state_machine import transition
from app.models.action_lifecycle import ActionLifecycleResult
from app.models.actions import Action, ActionStatus
from app.models.measurement import MeasurementResult
from app.models.outcomes import OutcomeEvaluationResult


def move_action(
    *,
    action: Action,
    target_status: ActionStatus,
    reason: str,
    evidence: dict | None = None,
) -> ActionLifecycleResult:
    from app.models.action_lifecycle import transition_action

    return transition_action(
        action=action,
        target_status=target_status,
        reason=reason,
        evidence=evidence,
    )


def mark_executing(action: Action) -> ActionLifecycleResult:
    return move_action(
        action=action,
        target_status=ActionStatus.EXECUTING,
        reason="execution_started",
    )


def mark_execution_succeeded(
    *,
    action: Action,
    execution_id: str,
    adapter_id: str,
) -> ActionLifecycleResult:
    return move_action(
        action=action,
        target_status=ActionStatus.EXECUTED,
        reason="execution_succeeded",
        evidence={
            "execution_id": execution_id,
            "adapter_id": adapter_id,
        },
    )


def mark_execution_failed(
    *,
    action: Action,
    execution_id: str,
    reason: str,
) -> ActionLifecycleResult:
    return move_action(
        action=action,
        target_status=ActionStatus.FAILED,
        reason="execution_failed",
        evidence={
            "execution_id": execution_id,
            "failure_reason": reason,
        },
    )


def mark_waiting_for_recrawl(
    *,
    action: Action,
    execution_id: str,
) -> ActionLifecycleResult:
    return move_action(
        action=action,
        target_status=ActionStatus.WAITING_FOR_RECRAWL,
        reason="waiting_for_recrawl",
        evidence={"execution_id": execution_id},
    )


def mark_measurement_window_active(
    *,
    action: Action,
    verification_id: str,
) -> ActionLifecycleResult:
    return move_action(
        action=action,
        target_status=ActionStatus.MEASUREMENT_WINDOW_ACTIVE,
        reason="post_execution_change_verified",
        evidence={"verification_id": verification_id},
    )


def mark_evaluating(
    *,
    action: Action,
    measurement: MeasurementResult,
) -> ActionLifecycleResult:
    if measurement.action_id != action.action_id:
        raise ValueError("Measurement action_id does not match action.")

    return move_action(
        action=action,
        target_status=ActionStatus.EVALUATING,
        reason="measurement_ready_for_evaluation",
        evidence={
            "measurement_id": measurement.measurement_id,
            "measurement_status": measurement.status.value,
        },
    )


def mark_outcome(
    *,
    action: Action,
    outcome: OutcomeEvaluationResult,
) -> ActionLifecycleResult:
    from app.models.action_lifecycle import apply_outcome

    return apply_outcome(action=action, outcome=outcome)
