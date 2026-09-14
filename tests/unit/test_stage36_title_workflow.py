from datetime import datetime, timezone

from app.models.fallback import ProviderFallbackPolicy
from app.models.opportunities import OpportunityType
from app.models.providers import ProviderRegistration
from app.models.reasoning import TitleReasoningCandidate
from app.models.serp import SERPQuerySnapshot, SERPResultItem
from app.models.site_adapter import SitePage
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import Strategy, StrategyStatus, StrategyType
from app.models.title_proposal import TitleProposalStatus
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
            fetched_at=datetime.now(timezone.utc),
            results=[
                SERPResultItem(position=1, url="https://example.com/a", title="خرید کفش مردانه"),
                SERPResultItem(position=2, url="https://example.com/b", title="قیمت کفش مردانه"),
                SERPResultItem(position=3, url="https://example.com/c", title="بهترین کفش مردانه"),
                SERPResultItem(position=5, url="https://example.com/page", title="عنوان فعلی", is_target=True),
            ],
        )


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


def make_strategy():
    snapshot = SnapshotMetadata(
        snapshot_id="snapshot-1",
        data_snapshot_id="data-1",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime.now(timezone.utc),
    )
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
        snapshot=snapshot,
    )


def make_router():
    registry = ProviderRegistry()
    registry.register(
        registration=ProviderRegistration(
            provider_id="fake-llm",
            display_name="Fake LLM",
            priority=1,
            model="test-model",
            config_version="config-v1",
            enabled=True,
        ),
        reasoner=FakeReasoner(),
    )
    return ReasoningRouter(
        registry=registry,
        fallback_policy=ProviderFallbackPolicy(),
    )


def make_workflow(repository):
    return TitleRecommendationWorkflow(
        adapter_resolver=lambda site: FakeAdapter(),
        serp_provider=FakeSERP(),
        reasoning_router=make_router(),
        proposal_store=TitleProposalStore(repository),
        proposal_id_factory=lambda: "proposal-1",
        orchestration_id_factory=lambda: "orchestration-1",
    )


def test_full_page_serp_llm_and_persistence_path():
    repository = InMemoryRepository()
    workflow = make_workflow(repository)

    proposal = workflow.run(
        run_id="run-123",
        site=object(),
        strategy=make_strategy(),
    )

    assert proposal.status == TitleProposalStatus.COMPLETED
    assert proposal.run_id == "run-123"
    assert proposal.selected_title == "خرید کفش مردانه اصل"
    assert proposal.provider_id == "fake-llm"

    stored = TitleProposalStore(repository).get("proposal-1")
    assert stored.run_id == "run-123"
    assert stored.recommendation.recommended_title is None
    assert stored.selected_title == "خرید کفش مردانه اصل"


def test_blocked_serp_never_calls_llm_and_is_persisted():
    repository = InMemoryRepository()

    class WeakSERP(FakeSERP):
        def search(self, *, query):
            snapshot = super().search(query=query)
            snapshot.results = snapshot.results[:2]
            return snapshot

    workflow = TitleRecommendationWorkflow(
        adapter_resolver=lambda site: FakeAdapter(),
        serp_provider=WeakSERP(),
        reasoning_router=make_router(),
        proposal_store=TitleProposalStore(repository),
        proposal_id_factory=lambda: "proposal-2",
    )

    proposal = workflow.run(run_id="run-456", site=object(), strategy=make_strategy())
    assert proposal.status == TitleProposalStatus.BLOCKED
    assert proposal.reasoning is None
    assert "serp_decision_not_pass" in proposal.reasons
    assert TitleProposalStore(repository).get("proposal-2").run_id == "run-456"


class FailingReasoner:
    provider_id = "fake-llm-failing"
    model = "test-model"

    def generate_title_candidates(self, reasoning_input):
        raise RuntimeError("provider failed")


def make_failing_router():
    registry = ProviderRegistry()
    registry.register(
        registration=ProviderRegistration(
            provider_id="fake-llm-failing",
            display_name="Failing LLM",
            priority=1,
            model="test-model",
            config_version="config-v1",
            enabled=True,
        ),
        reasoner=FailingReasoner(),
    )
    return ReasoningRouter(
        registry=registry,
        fallback_policy=ProviderFallbackPolicy(),
    )


def test_provider_failure_is_persisted_with_explicit_status():
    repository = InMemoryRepository()
    workflow = TitleRecommendationWorkflow(
        adapter_resolver=lambda site: FakeAdapter(),
        serp_provider=FakeSERP(),
        reasoning_router=make_failing_router(),
        proposal_store=TitleProposalStore(repository),
        proposal_id_factory=lambda: "proposal-failed",
        orchestration_id_factory=lambda: "orchestration-failed",
    )

    proposal = workflow.run(run_id="run-failed", site=object(), strategy=make_strategy())

    assert proposal.status == TitleProposalStatus.FAILED
    assert proposal.run_id == "run-failed"
    assert proposal.selected_title is None
    assert proposal.reasoning is None
    assert "strategic_reasoning_failed" in proposal.reasons
    assert TitleProposalStore(repository).get("proposal-failed").status == TitleProposalStatus.FAILED
