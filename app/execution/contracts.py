from app.models.actions import Action
from app.models.execution import (
    ExecutionRequest,
    ExecutionTarget,
)
from app.execution.capabilities import (
    required_capabilities_for_action,
)


def build_execution_request(
    *,
    action: Action,
    idempotency_key: str | None = None,
) -> ExecutionRequest:
    required_capabilities = (
        required_capabilities_for_action(
            action.action_type
        )
    )

    resolved_idempotency_key = (
        idempotency_key
        if idempotency_key is not None
        else f"action:{action.action_id}"
    )

    return ExecutionRequest(
        action_id=action.action_id,
        site_id=action.site_id,
        target=ExecutionTarget(
            site_id=action.site_id,
            normalized_url=action.normalized_url,
            normalized_query=action.normalized_query,
        ),
        required_capabilities=required_capabilities,
        parameters=dict(action.parameters),
        idempotency_key=resolved_idempotency_key,
    )