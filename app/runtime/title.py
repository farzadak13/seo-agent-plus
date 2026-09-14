"""Title recommendation workflow construction.

Stage 36 closes here: the workflow is either built with real collaborators or
the application refuses to start. A silently missing workflow would let runs
complete without ever producing a proposal, which reads as success.
"""
from __future__ import annotations

from app.runtime.reasoning import build_reasoning_router
from app.runtime.serp import build_serp_provider
from app.title.workflow import TitleProposalStore, TitleRecommendationWorkflow


def build_title_workflow(config, *, repository, adapter_factory, secret_resolver):
    """Build the title workflow, or return None when it is explicitly disabled.

    When ``title_workflow_enabled`` is true, every missing collaborator raises
    instead of degrading: configuration validation already guarantees the modes
    are set, so a failure here means a credential or snapshot file is missing.
    """
    if not config.title_workflow_enabled:
        return None

    return TitleRecommendationWorkflow(
        adapter_resolver=adapter_factory.build,
        serp_provider=build_serp_provider(config),
        reasoning_router=build_reasoning_router(config, secret_resolver=secret_resolver),
        proposal_store=TitleProposalStore(repository),
    )
