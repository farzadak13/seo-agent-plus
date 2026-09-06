from app.models.actions import ActionStatus


ALLOWED_TRANSITIONS: dict[
    ActionStatus,
    set[ActionStatus],
] = {
    ActionStatus.PLANNED: {
        ActionStatus.AWAITING_APPROVAL,
        ActionStatus.REJECTED,
    },
    ActionStatus.AWAITING_APPROVAL: {
        ActionStatus.APPROVED,
        ActionStatus.REJECTED,
    },
    ActionStatus.APPROVED: {
        ActionStatus.EXECUTING,
        ActionStatus.REJECTED,
    },
    ActionStatus.EXECUTING: {
        ActionStatus.EXECUTED,
        ActionStatus.FAILED,
    },
    ActionStatus.EXECUTED: {
        ActionStatus.WAITING_FOR_RECRAWL,
        ActionStatus.FAILED,
        ActionStatus.ROLLED_BACK,
    },
    ActionStatus.WAITING_FOR_RECRAWL: {
        ActionStatus.MEASUREMENT_WINDOW_ACTIVE,
        ActionStatus.FAILED,
    },
    ActionStatus.MEASUREMENT_WINDOW_ACTIVE: {
        ActionStatus.EVALUATING,
        ActionStatus.FAILED,
    },
    ActionStatus.EVALUATING: {
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED,
        ActionStatus.INCONCLUSIVE,
    },
    ActionStatus.FAILED: {
        ActionStatus.ROLLED_BACK,
    },
    ActionStatus.SUCCEEDED: set(),
    ActionStatus.INCONCLUSIVE: set(),
    ActionStatus.ROLLED_BACK: set(),
    ActionStatus.REJECTED: set(),
}


def can_transition(
    current: ActionStatus,
    target: ActionStatus,
) -> bool:
    return target in ALLOWED_TRANSITIONS.get(
        current,
        set(),
    )


def transition(
    current: ActionStatus,
    target: ActionStatus,
) -> ActionStatus:
    if not can_transition(current, target):
        raise ValueError(
            f"Invalid action transition: "
            f"{current.value} -> {target.value}"
        )

    return target