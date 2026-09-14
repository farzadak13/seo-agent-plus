"""Proposal identity must be derived from the run, not from randomness.

Stage 35 made decisions replayable by addressing every record through a
snapshot identity. A uuid4 proposal id breaks that: replaying one run would
write a second proposal instead of addressing the first, and nothing would
report the divergence. These tests pin the derived identity and prove it holds
on every outcome branch.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.fallback import ProviderFallbackPolicy
from app.models.opportunities import OpportunityType
from app.models.providers import ProviderRegistration
from app.models.reasoning import TitleReasoningCandidate
from app.models.serp import SERPQuerySnapshot, SERPResultItem
from app.models.site_adapter import SitePage
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import Strategy, StrategyStatus, StrategyType
from app.models.title_proposal import TitleProposalStatus
from app.persistence.contracts import PersistenceConflictError
from app.persistence.memory import InMemoryRepository
from app.reasoning.registry import ProviderRegistry
from app.reasoning.router import ReasoningRouter
from app.title.workflow import TitleProposalStore, TitleRecommendationWorkflow


class FakeAdapter:
    adapter_id = "fake"
    capabilities = set()

    def read_page(self, *, site_id, normalized_url):
        return SitePage(
            site_id=site_id,
            normalized_url=normalized_url,
            title="عنوان فعلی",
            metadata={"source": "test"},
        )


class FakeSERP:
    provider_id = "fake-serp"

    def search(self, *, query):
        return SERPQuerySnapshot(
            query=query,
            provider=self.provider_id,
            response_id=f"serp:{query}",
            fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            results=[
                SERPResultItem(position=1, url="https://a.example/1", title="خرید کفش مردانه"),
                SERPResultItem(position=2, url="https://b.example/2", title="قیمت کفش مردانه"),
                SERPResultItem(position=3, url="https://c.example/3", title="بهترین کفش مردانه"),
                SERPResultItem(
                    position=5,
                    url="https://example.com/page",
                    title="عنوان فعلی",
                    is_target=True,
                ),
            ],
        )


class WeakSERP(FakeSERP):
    """Too few competitors for the deterministic decision to pass."""

    def search(self, *, query):
        snapshot = super().search(query=query)
        snapshot.results = snapshot.results[:2]
        return snapshot


class FakeReasoner:
    provider_id = "fake-llm"
    model = "test-model"

    def generate_title_candidates(self, reasoning_input):
        return [
            TitleReasoningCandidate(
                candidate_id="candidate-1",
                title="خرید کفش مردانه اصل",
                rationale="بر پایه شواهد SERP",
                confidence_score=0.95,
            )
        ]


class FailingReasoner:
    provider_id = "fake-llm"
    model = "test-model"

    def generate_title_candidates(self, reasoning_input):
        raise RuntimeError("provider failed")


def make_router(reasoner):
    registry = ProviderRegistry()
    registry.register(
        registration=ProviderRegistration(
            provider_id=reasoner.provider_id,
            display_name="Fake LLM",
            priority=1,
            model=reasoner.model,
            config_version="config-v1",
            enabled=True,
        ),
        reasoner=reasoner,
    )
    return ReasoningRouter(registry=registry, fallback_policy=ProviderFallbackPolicy())


def make_strategy():
    return Strategy(
        strategy_id="strategy-1",
        opportunity_id="opportunity-1",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type=OpportunityType.VISIBILITY_GROWTH,
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=1.0,
        expected_impact_score=0.8,
        effort_score=0.2,
        risk_score=0.1,
        priority_score=0.9,
        reasons=["test"],
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-1",
            data_snapshot_id="data-1",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
    )


def make_workflow(repository, *, serp=None, reasoner=None):
    """Built exactly as the composition root builds it: no id factories."""
    return TitleRecommendationWorkflow(
        adapter_resolver=lambda site: FakeAdapter(),
        serp_provider=serp or FakeSERP(),
        reasoning_router=make_router(reasoner or FakeReasoner()),
        proposal_store=TitleProposalStore(repository),
    )


def test_completed_proposal_id_is_derived_from_the_run():
    repository = InMemoryRepository()
    proposal = make_workflow(repository).run(
        run_id="run-123", site=object(), strategy=make_strategy()
    )

    assert proposal.status == TitleProposalStatus.COMPLETED
    assert proposal.proposal_id == "proposal:run-123"
    assert TitleProposalStore(repository).get("proposal:run-123").run_id == "run-123"


def test_blocked_proposal_uses_the_same_derived_identity():
    repository = InMemoryRepository()
    proposal = make_workflow(repository, serp=WeakSERP()).run(
        run_id="run-123", site=object(), strategy=make_strategy()
    )

    assert proposal.status == TitleProposalStatus.BLOCKED
    assert proposal.proposal_id == "proposal:run-123"


def test_failed_proposal_uses_the_same_derived_identity():
    repository = InMemoryRepository()
    proposal = make_workflow(repository, reasoner=FailingReasoner()).run(
        run_id="run-123", site=object(), strategy=make_strategy()
    )

    assert proposal.status == TitleProposalStatus.FAILED
    assert proposal.proposal_id == "proposal:run-123"


def test_replaying_a_run_collides_instead_of_writing_a_second_proposal():
    """A duplicate is a conflict to resolve, never a silently forked history."""
    repository = InMemoryRepository()
    workflow = make_workflow(repository)
    strategy = make_strategy()

    workflow.run(run_id="run-123", site=object(), strategy=strategy)

    with pytest.raises(PersistenceConflictError):
        workflow.run(run_id="run-123", site=object(), strategy=strategy)


def test_distinct_runs_keep_distinct_proposals():
    repository = InMemoryRepository()
    workflow = make_workflow(repository)
    strategy = make_strategy()

    first = workflow.run(run_id="run-a", site=object(), strategy=strategy)
    second = workflow.run(run_id="run-b", site=object(), strategy=strategy)

    assert first.proposal_id == "proposal:run-a"
    assert second.proposal_id == "proposal:run-b"


def test_an_injected_factory_still_wins():
    """Tests and controlled replays may still pin an explicit identity."""
    repository = InMemoryRepository()
    workflow = TitleRecommendationWorkflow(
        adapter_resolver=lambda site: FakeAdapter(),
        serp_provider=FakeSERP(),
        reasoning_router=make_router(FakeReasoner()),
        proposal_store=TitleProposalStore(repository),
        proposal_id_factory=lambda: "explicit-proposal",
    )

    proposal = workflow.run(run_id="run-123", site=object(), strategy=make_strategy())

    assert proposal.proposal_id == "explicit-proposal"
