
from __future__ import annotations

from datetime import date
from app.observability.context import get_current_context
from app.observability.lifecycle import lifecycle_span
from app.models.observability import ObservabilityEventType
from typing import Any, Callable

from app.action.approval import propose_title_change
from app.action.store import ManagedActionStore
from app.models.runs import SEORunStatus
from app.models.title_proposal import TitleProposalStatus
from app.onboarding.site_store import SiteStore
from app.runs.service import SEORunService
from app.runs.store import RunStore
from app.title.workflow import TitleRecommendationWorkflow


SEO_RUN_JOB_TYPE = "seo_run"


def build_seo_run_handler(
    *,
    site_store: SiteStore,
    run_store: RunStore,
    run_service: SEORunService,
    title_workflow: TitleRecommendationWorkflow | None = None,
    action_store: ManagedActionStore | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the scheduler handler that connects Jobs to the SEO run service."""

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(payload["run_id"])
        run = run_store.get(run_id)

        site = site_store.get(run.site_id)
        if run.status == SEORunStatus.COMPLETED:
            return {"run_id": run.run_id, "status": run.status.value,
                    "pipeline_status": (run.result or {}).get("status")}
        if run.status in {SEORunStatus.FAILED, SEORunStatus.RUNNING}:
            run = run.model_copy(update={"status": SEORunStatus.QUEUED,
                                         "completed_at": None, "error": None})
        running = run.start()
        run_store.update(running)

        try:
            result = run_service.run(
                site=site,
                start_date=date.fromisoformat(run.start_date),
                end_date=date.fromisoformat(run.end_date),
                normalized_url=run.normalized_url,
                normalized_query=run.normalized_query,
                candidate_id=run.candidate_id,
            )

            title_proposal = None
            if title_workflow is not None and result.strategy is not None:
                title_proposal = title_workflow.run(
                    run_id=run.run_id,
                    site=site,
                    strategy=result.strategy,
                )

            pending_action = None
            if action_store is not None and title_proposal is not None:
                pending_action = _queue_for_approval(
                    action_store=action_store,
                    run=run,
                    action=getattr(result, "action", None),
                    proposal=title_proposal,
                )

            run_result = result.model_dump(mode="json")
            if title_proposal is not None:
                run_result["title_proposal_id"] = title_proposal.proposal_id
                run_result["title_proposal_status"] = title_proposal.status.value
                run_result["title_selected_title"] = title_proposal.selected_title
            if pending_action is not None:
                run_result["action_id"] = pending_action.action_id

            completed = running.complete(
                result=run_result
            )
            run_store.update(completed)
            response = {
                "run_id": completed.run_id,
                "status": completed.status.value,
                "pipeline_status": result.status.value,
            }
            if title_proposal is not None:
                response["title_proposal_id"] = title_proposal.proposal_id
                response["title_proposal_status"] = title_proposal.status.value
                response["title_selected_title"] = title_proposal.selected_title
            if pending_action is not None:
                response["action_id"] = pending_action.action_id
            return response
        except Exception as exc:
            failed = running.fail(str(exc))
            run_store.update(failed)
            raise

    def observed_handler(payload):
        context = get_current_context()
        if context is None:
            return handler(payload)
        run = run_store.get(str(payload["run_id"]))
        context = context.derive(run_id=run.run_id, site_id=run.site_id, job_id=run.job_id)
        with context.activate():
            with lifecycle_span(context, event_type=ObservabilityEventType.RUN,
                                operation="run.execute"):
                return handler(payload)

    return observed_handler


def _queue_for_approval(*, action_store, run, action, proposal):
    """Stage 37: a completed title proposal waits for a person, never for nothing.

    Idempotent: a run delivered twice proposes once.
    """
    if action is None or proposal.status != TitleProposalStatus.COMPLETED:
        return None
    managed = propose_title_change(
        action=action,
        run_id=run.run_id,
        tenant_id=run.principal_id,
        proposal_id=proposal.proposal_id,
        proposed_title=proposal.selected_title,
        current_title=proposal.investigation.target_title,
        provider_id=proposal.provider_id,
    )
    existing = action_store.find(managed.action_id)
    if existing is not None:
        return existing
    return action_store.create(managed)
