from __future__ import annotations

from collections.abc import Callable

from app.execution.site_adapter import SiteAdapter
from app.models.persistence import PersistenceRecord
from app.models.serp import SERPQuerySnapshot
from app.models.serp_decision import SERPDecisionStatus
from app.models.strategies import Strategy, StrategyType
from app.models.title_proposal import TitleProposal, TitleProposalStatus
from app.persistence.contracts import Repository
from app.persistence.serialization import build_record
from app.reasoning.router import ReasoningRouter
from app.serp.decision import build_serp_decision
from app.serp.engine import build_serp_investigation
from app.serp.provider import SERPProvider
from app.title.engine import build_title_recommendation


TITLE_PROPOSAL_AGGREGATE_TYPE = "title_proposal"
TITLE_PROPOSAL_SCHEMA_VERSION = 1


class TitleWorkflowError(RuntimeError):
    pass


class TitleProposalStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, proposal: TitleProposal) -> TitleProposal:
        record = self._to_record(proposal, version=1)
        self._repository.create(record)
        return proposal

    def get(self, proposal_id: str) -> TitleProposal:
        record = self._repository.get(
            aggregate_type=TITLE_PROPOSAL_AGGREGATE_TYPE,
            aggregate_id=proposal_id,
        )
        if record is None:
            raise KeyError(f"title proposal not found: {proposal_id}")
        return TitleProposal.model_validate(record.payload)

    @staticmethod
    def _to_record(proposal: TitleProposal, *, version: int) -> PersistenceRecord:
        return build_record(
            record_id=f"title-proposal:{proposal.proposal_id}:v{version}",
            aggregate_type=TITLE_PROPOSAL_AGGREGATE_TYPE,
            aggregate_id=proposal.proposal_id,
            model=proposal,
            schema_version=TITLE_PROPOSAL_SCHEMA_VERSION,
            version=version,
        )


class TitleRecommendationWorkflow:
    """Connects page data -> SERP -> deterministic decision -> LLM -> persistence."""

    def __init__(
        self,
        *,
        adapter_resolver: Callable[[object], SiteAdapter],
        serp_provider: SERPProvider,
        reasoning_router: ReasoningRouter,
        proposal_store: TitleProposalStore,
        proposal_id_factory: Callable[[], str] | None = None,
        orchestration_id_factory: Callable[[], str] | None = None,
        provider_ids: list[str] | None = None,
    ) -> None:
        self._adapter_resolver = adapter_resolver
        self._serp_provider = serp_provider
        self._reasoning_router = reasoning_router
        self._proposal_store = proposal_store
        # No random default: identifiers are derived from the run so a replay of
        # the same run addresses the same records instead of creating new ones.
        self._proposal_id_factory = proposal_id_factory
        self._orchestration_id_factory = orchestration_id_factory
        self._provider_ids = provider_ids

    def _proposal_id(self, run_id: str) -> str:
        if self._proposal_id_factory is not None:
            return self._proposal_id_factory()
        return f"proposal:{run_id}"

    def _orchestration_id(self, run_id: str) -> str:
        if self._orchestration_id_factory is not None:
            return self._orchestration_id_factory()
        return f"orchestration:{run_id}"

    def run(self, *, run_id: str, site: object, strategy: Strategy) -> TitleProposal:
        if strategy.strategy_type != StrategyType.SERP_TITLE_OPTIMIZATION:
            raise TitleWorkflowError("Title proposal requires SERP title optimization strategy.")

        # Resolved once so every outcome branch persists under the same identity
        # and the same owner.
        proposal_id = self._proposal_id(run_id)
        tenant_id = getattr(site, "principal_id", None)

        adapter = self._adapter_resolver(site)
        page = adapter.read_page(
            site_id=strategy.site_id,
            normalized_url=strategy.normalized_url,
        )

        primary_queries = [strategy.normalized_query]
        snapshots: list[SERPQuerySnapshot] = [
            self._serp_provider.search(query=strategy.normalized_query)
        ]

        investigation = build_serp_investigation(
            strategy=strategy,
            primary_queries=primary_queries,
            query_snapshots=snapshots,
            investigation_id=f"investigation:{run_id}",
            target_title=page.title,
        )
        decision = build_serp_decision(
            strategy=strategy,
            investigation=investigation,
            decision_id=f"serp-decision:{run_id}",
        )
        recommendation = build_title_recommendation(
            investigation=investigation,
            decision=decision,
            recommendation_id=f"recommendation:{run_id}",
        )

        if decision.status != SERPDecisionStatus.PASS:
            proposal = TitleProposal(
                proposal_id=proposal_id,
                run_id=run_id,
                tenant_id=tenant_id,
                site_id=strategy.site_id,
                normalized_url=strategy.normalized_url,
                primary_query=strategy.normalized_query,
                status=TitleProposalStatus.BLOCKED,
                recommendation=recommendation,
                investigation=investigation,
                decision=decision,
                reasoning=None,
                selected_title=None,
                provider_id=None,
                reasons=["serp_decision_not_pass", *decision.reasons],
                evidence={"serp_provider": self._serp_provider.provider_id},
                snapshot=decision.snapshot,
            )
            return self._proposal_store.create(proposal)

        from app.reasoning.orchestrator import run_title_reasoning_orchestration

        orchestration = run_title_reasoning_orchestration(
            orchestration_id=self._orchestration_id(run_id),
            investigation=investigation,
            decision=decision,
            recommendation=recommendation,
            router=self._reasoning_router,
            provider_ids=self._provider_ids,
        )

        if not orchestration.reasoning or not orchestration.reasoning.candidates:
            proposal = TitleProposal(
                proposal_id=proposal_id,
                run_id=run_id,
                tenant_id=tenant_id,
                site_id=strategy.site_id,
                normalized_url=strategy.normalized_url,
                primary_query=strategy.normalized_query,
                status=TitleProposalStatus.FAILED,
                recommendation=recommendation,
                investigation=investigation,
                decision=decision,
                reasoning=orchestration.reasoning,
                selected_title=None,
                provider_id=orchestration.provider_id,
                reasons=[*orchestration.reasons],
                evidence={
                    "serp_provider": self._serp_provider.provider_id,
                    "reasoning_status": orchestration.status.value,
                },
                snapshot=decision.snapshot,
            )
            return self._proposal_store.create(proposal)

        selected_id = orchestration.reasoning.selected_candidate_id
        selected = next(
            (candidate for candidate in orchestration.reasoning.candidates if candidate.candidate_id == selected_id),
            None,
        )
        if selected is None:
            raise TitleWorkflowError("Reasoning selected candidate is not present in validated candidates.")

        proposal = TitleProposal(
            proposal_id=proposal_id,
            run_id=run_id,
            tenant_id=tenant_id,
            site_id=strategy.site_id,
            normalized_url=strategy.normalized_url,
            primary_query=strategy.normalized_query,
            status=TitleProposalStatus.COMPLETED,
            recommendation=recommendation,
            investigation=investigation,
            decision=decision,
            reasoning=orchestration.reasoning,
            selected_title=selected.title,
            provider_id=orchestration.provider_id,
            reasons=[
                "title_proposal_generated",
                *orchestration.reasons,
            ],
            evidence={
                "serp_provider": self._serp_provider.provider_id,
                "selected_candidate_id": selected.candidate_id,
                "adapter_id": adapter.adapter_id,
                "page_metadata": dict(page.metadata),
            },
            snapshot=decision.snapshot,
        )
        return self._proposal_store.create(proposal)
