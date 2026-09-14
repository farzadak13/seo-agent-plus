from __future__ import annotations

from collections.abc import Sequence

from app.observability.lifecycle import observe
from app.models.observability import ObservabilityEventType
from app.action.state_machine import transition
from app.execution.contracts import build_execution_request
from app.execution.resolver import (
    ExecutionAdapter,
    resolve_execution_adapter,
)
from app.execution.site_adapter import SiteAdapter
from app.models.actions import (
    Action,
    ActionStatus,
    ActionType,
)
from app.models.execution_run import (
    ExecutionRunResult,
    ExecutionRunStatus,
)
from app.models.site_adapter import (
    AdapterOperationResult,
    AdapterRollbackRequest,
    SitePage,
)


class ExecutionOrchestratorError(RuntimeError):
    """
    Raised when an approved action cannot be executed.
    """


def approve_action(action: Action) -> Action:
    """
    Move an action into the APPROVED state.

    Approval remains an explicit caller decision.
    The orchestrator never silently approves an action.
    """

    if action.status not in {
        ActionStatus.AWAITING_APPROVAL,
        ActionStatus.PLANNED,
    }:
        raise ExecutionOrchestratorError(
            (
                "Action cannot be approved from status "
                f"{action.status.value}."
            )
        )

    return action.model_copy(
        update={
            "status": ActionStatus.APPROVED,
        }
    )


@observe(ObservabilityEventType.EXECUTION, "execution.execute")
def execute_approved_action(
    *,
    action: Action,
    adapters: Sequence[ExecutionAdapter],
) -> ExecutionRunResult:
    """
    Execute one already-approved Action through the resolved SiteAdapter.

    No SEO decision is made here.
    This layer only translates the canonical Action into an execution
    request, resolves a capable adapter, and invokes the appropriate
    adapter operation.
    """

    if action.status != ActionStatus.APPROVED:
        raise ExecutionOrchestratorError(
            (
                "Only approved actions can be executed. "
                f"Current status: {action.status.value}."
            )
        )

    request = build_execution_request(
        action=action,
    )

    try:
        resolution = resolve_execution_adapter(
            request=request,
            adapters=list(adapters),
        )

        adapter = _find_adapter(
            adapter_id=resolution.adapter_id,
            adapters=adapters,
        )

        executing_action = action.model_copy(
            update={
                "status": transition(
                    action.status,
                    ActionStatus.EXECUTING,
                ),
            }
        )

        operation_result, page = _dispatch(
            action=executing_action,
            adapter=adapter,
            request=request,
        )

        if operation_result is not None and not operation_result.success:
            return ExecutionRunResult(
                action=executing_action.model_copy(update={"status": ActionStatus.FAILED}),
                adapter_id=adapter.adapter_id, request=request,
                status=ExecutionRunStatus.FAILED, operation_result=operation_result,
                page=page, error="Adapter reported an unsuccessful operation.",
            )

        executed_action = executing_action.model_copy(
            update={
                "status": transition(
                    executing_action.status,
                    ActionStatus.EXECUTED,
                ),
            }
        )

        return ExecutionRunResult(
            action=executed_action,
            adapter_id=adapter.adapter_id,
            request=request,
            status=ExecutionRunStatus.EXECUTED,
            operation_result=operation_result,
            page=page,
        )

    except Exception as exc:
        failed_action = action.model_copy(
            update={
                "status": ActionStatus.FAILED,
            }
        )

        adapter_id = (
            resolution.adapter_id
            if "resolution" in locals()
            else "unresolved"
        )

        return ExecutionRunResult(
            action=failed_action,
            adapter_id=adapter_id,
            request=request,
            status=ExecutionRunStatus.FAILED,
            error=str(exc),
        )


def _find_adapter(
    *,
    adapter_id: str,
    adapters: Sequence[ExecutionAdapter],
) -> SiteAdapter:
    for adapter in adapters:
        if adapter.adapter_id == adapter_id:
            return adapter

    raise ExecutionOrchestratorError(
        f"Resolved adapter not found: {adapter_id}"
    )


def _dispatch(
    *,
    action: Action,
    adapter: SiteAdapter,
    request,
) -> tuple[
    AdapterOperationResult | None,
    SitePage | None,
]:
    target = request.target

    if action.action_type == ActionType.OPTIMIZE_TITLE:
        new_title = _required_string_parameter(
            action,
            "recommended_title",
        )

        return (
            adapter.update_title(
                site_id=target.site_id,
                normalized_url=target.normalized_url,
                new_title=new_title,
                idempotency_key=request.idempotency_key,
            ),
            None,
        )

    if (
        action.action_type
        == ActionType.INVESTIGATE_CANNIBALIZATION
    ):
        return (
            None,
            adapter.read_page(
                site_id=target.site_id,
                normalized_url=target.normalized_url,
            ),
        )

    if action.action_type == ActionType.IMPROVE_INTERNAL_LINKING:
        new_content = _required_string_parameter(
            action,
            "new_content",
        )

        return (
            adapter.update_internal_links(
                site_id=target.site_id,
                normalized_url=target.normalized_url,
                new_content=new_content,
                idempotency_key=request.idempotency_key,
            ),
            None,
        )

    if action.action_type == ActionType.IMPROVE_CONTENT_DEPTH:
        new_content = _required_string_parameter(
            action,
            "new_content",
        )

        return (
            adapter.update_content(
                site_id=target.site_id,
                normalized_url=target.normalized_url,
                new_content=new_content,
                idempotency_key=request.idempotency_key,
            ),
            None,
        )

    if action.action_type == ActionType.MONITOR:
        return (
            None,
            adapter.read_page(
                site_id=target.site_id,
                normalized_url=target.normalized_url,
            ),
        )

    raise ExecutionOrchestratorError(
        (
            "Unsupported action type for execution: "
            f"{action.action_type.value}"
        )
    )


def _required_string_parameter(
    action: Action,
    key: str,
) -> str:
    value = action.parameters.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ExecutionOrchestratorError(
            (
                f"Action parameter '{key}' must be a "
                "non-empty string."
            )
        )

    return value
