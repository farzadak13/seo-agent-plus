"""Stage 38 — carry out an approved title change, check it, and undo it on request.

Both handlers run as jobs, so either can be delivered more than once: after a
crash, or after a timeout on a write the site did in fact accept. They are
built so that a second delivery finishes the job instead of doing it twice or
losing track of it:

* Before touching the site, the action is recorded as EXECUTING. That claim
  is what stops a rejection arriving mid-write from being silently lost:
  rejection is not allowed from EXECUTING, and a claim that loses the race to
  a rejection fails instead of overwriting it.
* The write carries an idempotency key derived from the action, so repeating
  it after a timeout returns the original change rather than making a new one.
* The change is recorded as EXECUTED before it is verified, so a failure while
  reading the page back cannot erase the record that the site was changed.

Neither handler overwrites a page someone else has changed in the meantime.
The customer approved replacing one specific title; if the title is no longer
that, the approval no longer describes the page.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.action.approval import SYSTEM_ACTOR, check_rollback
from app.action.store import ManagedActionStore
from app.execution.capabilities import required_capabilities_for_action
from app.models.action_lifecycle import transition_action
from app.models.actions import ActionStatus
from app.models.managed_action import AppliedChange, ManagedAction
from app.models.site_adapter import AdapterRollbackRequest
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
        action_id = str(payload["action_id"])
        managed = action_store.get(action_id)
        if managed.status == ActionStatus.APPROVED:
            managed = action_store.update(
                _move(managed, ActionStatus.EXECUTING, "execution_started")
            )
        if managed.status not in {ActionStatus.EXECUTING, ActionStatus.EXECUTED}:
            # Already finished, or rejected before the job ran.
            return _summary(managed, skipped=True)

        try:
            adapter = adapter_factory.build(site_store.get(managed.site_id))
            if managed.status == ActionStatus.EXECUTED:
                # Recorded as written, then interrupted before the check.
                return _summary(action_store.update(_verify(managed, adapter)))
            return _summary(_carry_out(managed, adapter, action_store))
        except Exception as exc:
            _note_error(action_store, action_id, "execution_error", exc)
            raise

    return handler


def _carry_out(managed: ManagedAction, adapter, action_store: ManagedActionStore) -> ManagedAction:
    action = managed.action
    target = action.parameters["recommended_title"]

    missing = set(required_capabilities_for_action(action.action_type)) - set(adapter.capabilities)
    if missing:
        return action_store.update(
            _move(
                managed,
                ActionStatus.FAILED,
                "adapter_cannot_apply",
                detail={"missing": sorted(item.value for item in missing)},
                error="This site connection cannot change titles.",
            )
        )

    page = _read(adapter, managed)
    current = _norm(page.title)
    already_there = current == _norm(target)
    if (
        not already_there
        and managed.observed_value is not None
        and current != _norm(managed.observed_value)
    ):
        return action_store.update(
            _move(
                managed,
                ActionStatus.FAILED,
                "page_changed_since_proposal",
                detail={"expected_current": managed.observed_value, "found": page.title},
                error="The page title changed after this proposal was made; nothing was written.",
            )
        )

    # When the page already shows the target, an earlier delivery wrote it (or
    # someone made the same edit by hand). Writing again with the same key is
    # still done: it is a no-op for the site and it returns the original
    # change id, without which an exact rollback is impossible.
    operation = adapter.update_title(
        site_id=managed.site_id,
        normalized_url=action.normalized_url,
        new_title=target,
        idempotency_key=f"action:{managed.action_id}",
    )
    if not operation.success:
        return action_store.update(
            _move(
                managed,
                ActionStatus.FAILED,
                "execution_failed",
                detail={"adapter_id": adapter.adapter_id},
                error=operation.message or "The site refused the change.",
            )
        )

    previous = (managed.observed_value or page.title) if already_there else page.title
    managed = action_store.update(
        _move(
            managed,
            ActionStatus.EXECUTED,
            "already_applied" if already_there else "execution_succeeded",
            detail={"adapter_id": adapter.adapter_id},
            applied=AppliedChange(
                adapter_id=adapter.adapter_id,
                change_id=operation.change_id,
                previous_value=previous,
                new_value=target,
            ),
            error=None,
        )
    )
    return action_store.update(_verify(managed, adapter))


def build_rollback_action_handler(
    *,
    action_store: ManagedActionStore,
    site_store: SiteStore,
    adapter_factory,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        action_id = str(payload["action_id"])
        managed = action_store.get(action_id)
        if managed.rolled_back is not None:
            return _summary(managed, skipped=True)
        actor = str(payload.get("actor") or SYSTEM_ACTOR)
        try:
            check_rollback(managed)
            adapter = adapter_factory.build(site_store.get(managed.site_id))
            if getattr(adapter, "exact_rollback", False) and managed.applied.change_id:
                return _summary(_rollback_exactly(managed, adapter, actor, action_store))
            return _summary(_rollback_by_rewriting(managed, adapter, actor, action_store))
        except Exception as exc:
            _note_error(action_store, action_id, "rollback_error", exc)
            raise

    return handler


def _rollback_exactly(managed, adapter, actor, action_store) -> ManagedAction:
    """Undo through the site's own record of the change.

    The site, not the rendered page, decides: a page cache can keep showing
    either title for a while, and the plugin refuses by itself when a later
    change has superseded this one.
    """
    applied = managed.applied
    operation = adapter.rollback_change(
        request=AdapterRollbackRequest(
            site_id=managed.site_id,
            normalized_url=managed.action.normalized_url,
            change_id=applied.change_id,
            idempotency_key=f"rollback:{managed.action_id}",
        )
    )
    if not operation.success:
        return action_store.update(
            managed.record(
                action=managed.action,
                actor=actor,
                reason="rollback_refused",
                error=operation.message or "The site refused the rollback.",
            )
        )
    page = _read(adapter, managed)
    return action_store.update(
        _rolled_back(
            managed,
            adapter,
            actor,
            "rolled_back",
            change_id=operation.change_id,
            detail={
                "rendered_title": page.title,
                # False while a page cache still serves the old HTML.
                "rendered_matches": _norm(page.title) == _norm(applied.previous_value),
            },
        )
    )


def _rollback_by_rewriting(managed, adapter, actor, action_store) -> ManagedAction:
    """Write the previous title back, for sites that keep no change history."""
    applied = managed.applied
    page = _read(adapter, managed)
    current = _norm(page.title)

    if current == _norm(applied.previous_value):
        return action_store.update(_rolled_back(managed, adapter, actor, "already_rolled_back"))
    if current != _norm(applied.new_value):
        # Someone edited the title after we did. Restoring ours would throw
        # their edit away, so stop and say so.
        return action_store.update(
            managed.record(
                action=managed.action,
                actor=actor,
                reason="rollback_refused_page_changed",
                detail={"expected_current": applied.new_value, "found": page.title},
                error="The page title was changed after this action; rollback did not overwrite it.",
            )
        )
    operation = adapter.update_title(
        site_id=managed.site_id,
        normalized_url=managed.action.normalized_url,
        new_title=applied.previous_value,
        idempotency_key=f"rollback:{managed.action_id}",
    )
    if not operation.success:
        return action_store.update(
            managed.record(
                action=managed.action,
                actor=actor,
                reason="rollback_failed",
                error=operation.message or "The site refused the rollback.",
            )
        )
    restored = _read(adapter, managed)
    if _norm(restored.title) != _norm(applied.previous_value):
        return action_store.update(
            managed.record(
                action=managed.action,
                actor=actor,
                reason="rollback_not_verified",
                detail={"expected": applied.previous_value, "found": restored.title},
                error="The previous title was written but the page does not show it.",
            )
        )
    return action_store.update(
        _rolled_back(managed, adapter, actor, "rolled_back", change_id=operation.change_id)
    )


def _rolled_back(managed, adapter, actor, reason, *, change_id=None, detail=None) -> ManagedAction:
    applied = managed.applied
    moved = transition_action(
        action=managed.action, target_status=ActionStatus.ROLLED_BACK, reason=reason
    ).action
    return managed.record(
        action=moved,
        actor=actor,
        reason=reason,
        detail=detail,
        rolled_back=AppliedChange(
            adapter_id=adapter.adapter_id,
            change_id=change_id,
            previous_value=applied.new_value,
            new_value=applied.previous_value,
        ),
        error=None,
    )


def _read(adapter, managed: ManagedAction):
    return adapter.read_page(
        site_id=managed.site_id, normalized_url=managed.action.normalized_url
    )


def _norm(title: str | None) -> str:
    """Titles compare by their words: the proposal side is whitespace-normalised."""
    return " ".join((title or "").split())


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
    """Read the page back. A write the site accepted but does not show is a failure.

    Where the site reports its own stored value (the connector's override),
    that counts too: a page cache serving the old HTML for a few minutes is
    not a failed change.
    """
    managed = _move(managed, ActionStatus.WAITING_FOR_RECRAWL, "waiting_for_recrawl")
    page = _read(adapter, managed)
    target = managed.action.parameters["expected_title"]
    stored = page.metadata.get("override_title") if page.metadata else None
    rendered_ok = _norm(page.title) == _norm(target)
    stored_ok = stored is not None and _norm(stored) == _norm(target)
    observed = target if (rendered_ok or stored_ok) else page.title
    verification = verify_action_state(
        action=managed.action,
        verification_id=f"verification:{managed.action_id}",
        observed_state={"title": observed},
    )
    verified = apply_verification(action=managed.action, verification=verification)
    changes = {}
    if verified.status == ActionStatus.FAILED:
        changes["error"] = "The title was written but the page does not show it."
    detail = {"rendered_title": page.title}
    if stored_ok and not rendered_ok:
        detail["note"] = "stored on the site; the public page is still cached"
    return managed.record(
        action=verified,
        actor=SYSTEM_ACTOR,
        reason=f"verification_{verification.status.value}",
        detail=detail,
        **changes,
    )


def _note_error(action_store: ManagedActionStore, action_id: str, reason: str, exc: Exception) -> None:
    """Leave the failure on the action, where the customer looks, then let the job retry."""
    try:
        current = action_store.get(action_id)
        action_store.update(
            current.record(
                action=current.action,
                actor=SYSTEM_ACTOR,
                reason=reason,
                detail={"type": type(exc).__name__},
                error=str(exc)[:500],
            )
        )
    except Exception:
        # Recording the error must never hide the error itself.
        pass


def _summary(managed: ManagedAction, *, skipped: bool = False) -> dict[str, Any]:
    summary = {"action_id": managed.action_id, "status": managed.status.value}
    if skipped:
        summary["skipped"] = True
    if managed.error:
        summary["error"] = managed.error
    return summary
