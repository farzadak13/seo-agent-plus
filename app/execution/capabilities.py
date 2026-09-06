from app.models.actions import ActionType
from app.models.execution import ExecutionCapability


ACTION_REQUIRED_CAPABILITIES: dict[
    ActionType,
    list[ExecutionCapability],
] = {
    ActionType.OPTIMIZE_TITLE: [
        ExecutionCapability.UPDATE_TITLE,
    ],
    ActionType.INVESTIGATE_CANNIBALIZATION: [
        ExecutionCapability.READ_PAGE,
    ],
    ActionType.IMPROVE_INTERNAL_LINKING: [
        ExecutionCapability.READ_PAGE,
        ExecutionCapability.UPDATE_INTERNAL_LINKS,
    ],
    ActionType.IMPROVE_CONTENT_DEPTH: [
        ExecutionCapability.READ_PAGE,
        ExecutionCapability.UPDATE_CONTENT,
    ],
    ActionType.MONITOR: [
        ExecutionCapability.READ_PAGE,
    ],
}


def required_capabilities_for_action(
    action_type: ActionType,
) -> list[ExecutionCapability]:
    try:
        return list(ACTION_REQUIRED_CAPABILITIES[action_type])
    except KeyError as exc:
        raise ValueError(
            f"No execution capabilities defined for action: "
            f"{action_type.value}"
        ) from exc