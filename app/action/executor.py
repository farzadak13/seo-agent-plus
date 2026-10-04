"""Stage 38 — carry out an approved title change, check it, and undo it on request.

Both handlers run as jobs, so either can be delivered twice: once normally and
once more after a crash between writing to the site and recording that it was
written. Each one therefore reads the page first and decides from what is
actually there, rather than from what the record says should be there.

Neither handler overwrites a page that someone else has changed in the
meantime. The customer approved replacing one specific title; if the title is
no longer that, the approval no longer describes the page.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.action.approval import SYSTEM_ACTOR, check_rollback
from app.action.store import ManagedActionStore
from app.execution.orchestrator import execute_approved_action
from app.models.action_lifecycle import transition_action
from app.models.actions import ActionStatus
from app.models.execution_run import ExecutionRunStatus
from app.models.managed_action import AppliedChange, ManagedAction
from app.models.site_adapter import AdapterOperationResult, AdapterRollbackRequest
from app.onboarding.site_store import SiteStore
from app.verification.engine import apply_verification, verify_action_state


EXECUTE_ACTION_JOB_TYPE = "execute_action"
ROLLBACK_ACTION_JOB_TYPE = "rollback_action"


def build_execute_action_handler(
    *,
    action_store: ManagedActionStore,
    site_store: SiteStore,
    adapter_factory,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        managed = action_store.get(str(payload["action_id"]))
        if managed.status != ActionStatus.APPROVED:
            # Already carried out, or rejected after the job was queued.
            return _summary(managed, skipped=True)

        site = site_store.get(managed.site_id)
        adapter = adapter_factory.build(site)
        action = managed.action
        target = action.parameters["recommended_title"]

        page = _read(adapter, managed)

        if page.title == target:
            # A previous delivery wrote it and crashed before recording it, or
            # someone made the same edit by hand. Either way the page is
            # already in the approved state; record that instead of writing.
            managed = _move(managed, ActionStatus.EXECUTING, "execution_started")
            managed = _move(
                managed,
                ActionStatus.EXECUTED,
                "already_applied",
                applied=AppliedChange(
                    adapter_id=adapter.adapter_id,
                    previous_value=managed.observed_value or "",
                    new_value=target,
                ),
            )
        elif managed.observed_value is not None and page.title != managed.observed_value:
            managed = _move(managed, ActionStatus.EXECUTING, "execution_started")
            managed = _move(
                managed,
                ActionStatus.FAILED,
                "page_changed_since_proposal",
                detail={"expected_current": managed.observed_value, "found": page.title},
                error="The page title changed after this proposal was made; nothing was written.",
            )
            return _summary(action_store.update(managed))
        else:
            result = execute_approved_action(action=action, adapters=[adapter])
            if result.status != ExecutionRunStatus.EXECUTED:
                managed = _move(managed, ActionStatus.EXECUTING, "execution_started")
                managed = _move(
                    managed,
                    ActionStatus.FAILED,
                    "execution_failed",
                    detail={"adapter_id": result.adapter_id},
                    error=result.error or "The site adapter reported a failure.",
                )
                return _summary(action_store.update(managed))
            operation: AdapterOperationResult | None = result.operation_result
            managed = _move(managed, ActionStatus.EXECUTING, "execution_started")
            managed = _move(
                managed,
                ActionStatus.EXECUTED,
                "execution_succeeded",
                detail={"adapter_id": result.adapter_id},
                applied=AppliedChange(
                    adapter_id=result.adapter_id,
                    change_id=operation.change_id if operation else None,
                    previous_value=page.title,
                    new_value=target,
                ),
            )

        managed = _verify(managed, adapter)
        return _summary(action_store.update(managed))

    return handler


def build_rollback_action_handler(
    *,
    action_store: ManagedActionStore,
    site_store: SiteStore,
    adapter_factory,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        managed = action_store.get(str(payload["action_id"]))
        if managed.rolled_back is not None:
            return _summary(managed, skipped=True)
        check_rollback(managed)
        actor = str(payload.get("actor") or SYSTEM_ACTOR)

        site = site_store.get(managed.site_id)
        adapter = adapter_factory.build(site)
        applied = managed.applied
        page = _read(adapter, managed)

        if page.title == applied.previous_value:
            change_id = None
            reason = "already_rolled_back"
        elif page.title != applied.new_value:
            # Someone edited the title after we did. Restoring ours would throw
            # their edit away, so stop and say so.
            managed = managed.record(
                action=managed.action,
                actor=actor,
                reason="rollback_refused_page_changed",
                detail={"expected_current": applied.new_value, "found": page.title},
                error="The page title was changed after this action; rollback did not overwrite it.",
            )
            return _summary(action_store.update(managed))
        else:
            operation = _restore(adapter, managed, applied)
            if not operation.success:
                managed = managed.record(
                    action=managed.action,
                    actor=actor,
                    reason="rollback_failed",
                    error=operation.message or "The site adapter reported a failure.",
                )
                return _summary(action_store.update(managed))
            change_id = operation.change_id
            reason = "rolled_back"

        restored = _read(adapter, managed)
        if restored.title != applied.previous_value:
            managed = managed.record(
                action=managed.action,
                actor=actor,
                reason="rollback_not_verified",
                detail={"expected": applied.previous_value, "found": restored.title},
                error="The previous title was written but the page does not show it.",
            )
            return _summary(action_store.update(managed))

        moved = transition_action(
            action=managed.action, target_status=ActionStatus.ROLLED_BACK, reason=reason
        ).action
        managed = managed.record(
            action=moved,
            actor=actor,
            reason=reason,
            rolled_back=AppliedChange(
                adapter_id=adapter.adapter_id,
                change_id=change_id,
                previous_value=applied.new_value,
                new_value=applied.previous_value,
            ),
            error=None,
        )
        return _summary(action_store.update(managed))

    return handler


def _restore(adapter, managed: ManagedAction, applied: AppliedChange) -> AdapterOperationResult:
    """Undo through the site's own record of the change when it keeps one.

    Otherwise write the previous title back. For an adapter whose title is an
    override on top of a template, that would pin the old rendered text in
    place for good, so such adapters declare exact_rollback.
    """
    if getattr(adapter, "exact_rollback", False) and applied.change_id:
        return adapter.rollback_change(
            request=AdapterRollbackRequest(
                site_id=managed.site_id,
                normalized_url=managed.action.normalized_url,
                change_id=applied.change_id,
                idempotency_key=f"rollback:{managed.action_id}",
            )
        )
    return adapter.update_title(
        site_id=managed.site_id,
        normalized_url=managed.action.normalized_url,
        new_title=applied.previous_value,
        idempotency_key=f"rollback:{managed.action_id}",
    )


def _read(adapter, managed: ManagedAction):
    return adapter.read_page(
        site_id=managed.site_id, normalized_url=managed.action.normalized_url
    )


def _move(
    managed: ManagedAction,
    target: ActionStatus,
    reason: str,
    *,
    detail: dict | None = None,
    **changes,
) -> ManagedAction:
    moved = transition_action(
        action=managed.action, target_status=target, reason=reason, evidence=detail
    ).action
    return managed.record(
        action=moved, actor=SYSTEM_ACTOR, reason=reason, detail=detail, **changes
    )


def _verify(managed: ManagedAction, adapter) -> ManagedAction:
    """Read the page back. A write the site accepted but does not show is a failure."""
    managed = _move(managed, ActionStatus.WAITING_FOR_RECRAWL, "waiting_for_recrawl")
    page = _read(adapter, managed)
    verification = verify_action_state(
        action=managed.action,
        verification_id=f"verification:{managed.action_id}",
        observed_state={"title": page.title},
    )
    verified = apply_verification(action=managed.action, verification=verification)
    changes = {}
    if verified.status == ActionStatus.FAILED:
        changes["error"] = "The title was written but the page does not show it."
    return managed.record(
        action=verified,
        actor=SYSTEM_ACTOR,
        reason=f"verification_{verification.status.value}",
        detail={"observed_title": page.title},
        **changes,
    )


def _summary(managed: ManagedAction, *, skipped: bool = False) -> dict[str, Any]:
    summary = {"action_id": managed.action_id, "status": managed.status.value}
    if skipped:
        summary["skipped"] = True
    if managed.error:
        summary["error"] = managed.error
    return summary
