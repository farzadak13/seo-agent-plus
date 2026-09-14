from datetime import datetime, timezone

from app.models.fallback import ProviderFallbackPolicy
from app.models.providers import ProviderRegistration
from app.models.reasoning import TitleReasoningCandidate
from app.models.reasoning_orchestration import (
    ReasoningOrchestrationStatus,
)
from app.models.serp import (
    SERPInvestigation,
    SERPInvestigationStatus,
    SERPQueryEvidence,
)
from app.models.serp_decision import (
    SERPDecision,
    SERPDecisionStatus,
)
from app.models.snapshots import SnapshotMetadata
from app.models.title_recommendation import (
    TitleRecommendation,
    TitleRecommendationStatus,
)
from app.reasoning.orchestrator import (
    run_title_reasoning_orchestration,
)
from app.reasoning.registry import ProviderRegistry
from app.reasoning.router import ReasoningRouter


def make_snapshot():
    return SnapshotMetadata(
        snapshot_id="snapshot-orch",
        data_snapshot_id="data-orch",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            7,
            9,
            0,
            tzinfo=timezone.utc,
        ),
    )


def make_investigation():
    evidence = SERPQueryEvidence(
        query="کفش مردانه",
        target_found=True,
        target_position=5,
        top_results_count=5,
        competitor_count=4,
        competitor_titles=[
            "خرید کفش مردانه",
            "قیمت کفش مردانه",
            "بهترین کفش مردانه",
            "کفش مردانه اصل",
        ],
        target_title="عنوان فعلی",
        target_title_length=10,
        competitor_title_lengths=[
            18,
            20,
            19,
            17,
        ],
        competitor_title_length_median=18.5,
        competitor_title_length_average=18.5,
        title_length_gap_vs_median=-8.5,
    )

    return SERPInvestigation(
        investigation_id="investigation-orch",
        strategy_id="strategy-orch",
        opportunity_id="opportunity-orch",
        site_id="site-1",
        normalized_url="https://example.com/page",
        status=SERPInvestigationStatus.COMPLETED,
        primary_queries=["کفش مردانه"],
        query_evidence=[evidence],
        target_title="عنوان فعلی",
        target_found_in_any_query=True,
        target_best_position=5,
        evidence={},
    )


def make_decision(
    status=SERPDecisionStatus.PASS,
):
    return SERPDecision(
        decision_id="decision-orch",
        investigation_id="investigation-orch",
        strategy_id="strategy-orch",
        opportunity_id="opportunity-orch",
        status=status,
        confidence_score=1.0,
        reasons=["test"],
        evidence={},
        snapshot=make_snapshot(),
    )


def make_recommendation():
    return TitleRecommendation(
        recommendation_id="recommendation-orch",
        investigation_id="investigation-orch",
        decision_id="decision-orch",
        strategy_id="strategy-orch",
        opportunity_id="opportunity-orch",
        site_id="site-1",
        normalized_url="https://example.com/page",
        primary_query="کفش مردانه",
        status=TitleRecommendationStatus.RECOMMENDED,
        current_title="عنوان فعلی",
        recommended_title=None,
        current_title_length=10,
        recommended_title_length=None,
        competitor_title_length_median=18.5,
        title_length_gap_vs_competitors=-8.5,
        confidence_score=1.0,
        reasons=["serp_evidence_passed"],
        constraints=[
            "do_not_copy_competitor_titles",
            "preserve_primary_query_relevance",
        ],
        evidence={},
        snapshot=make_snapshot(),
    )


class FakeReasoner:
    @property
    def provider_id(self):
        return "arvan_aiaas"

    def generate_title_candidates(
        self,
        reasoning_input,
    ):
        return [
            TitleReasoningCandidate(
                candidate_id="candidate-1",
                title="خرید کفش مردانه اصل",
                rationale="evidence based",
                confidence_score=0.9,
            )
        ]


class FailingReasoner:
    @property
    def provider_id(self):
        return "arvan_aiaas"

    def generate_title_candidates(
        self,
        reasoning_input,
    ):
        raise RuntimeError("provider failed")


def make_router(reasoner):
    registry = ProviderRegistry()

    registry.register(
        registration=ProviderRegistration(
            provider_id=reasoner.provider_id,
            display_name=reasoner.provider_id,
            enabled=True,
            priority=1,
            model="test-model",
            config_version="config-v1",
        ),
        reasoner=reasoner,
    )

    return ReasoningRouter(
        registry=registry,
        fallback_policy=ProviderFallbackPolicy(),
    )


def test_pass_runs_complete_orchestration():
    router = make_router(
        FakeReasoner()
    )

    result = run_title_reasoning_orchestration(
        orchestration_id="orchestration-001",
        investigation=make_investigation(),
        decision=make_decision(),
        recommendation=make_recommendation(),
        router=router,
    )

    assert result.status == (
        ReasoningOrchestrationStatus.COMPLETED
    )

    assert result.reasoning is not None
    assert len(result.reasoning.candidates) == 1

    assert result.provider_id == (
        router.last_provider_id
    )

    assert result.provider_id == (
        "arvan_aiaas"
    )


def test_non_pass_decision_blocks_llm():
    router = make_router(
        FakeReasoner()
    )

    result = run_title_reasoning_orchestration(
        orchestration_id="orchestration-002",
        investigation=make_investigation(),
        decision=make_decision(
            SERPDecisionStatus.INVESTIGATE
        ),
        recommendation=make_recommendation(),
        router=router,
    )

    assert result.status == (
        ReasoningOrchestrationStatus.BLOCKED
    )

    assert result.reasoning is None
    assert result.provider_id is None


def test_provider_failure_returns_failed_result():
    router = make_router(
        FailingReasoner()
    )

    result = run_title_reasoning_orchestration(
        orchestration_id="orchestration-003",
        investigation=make_investigation(),
        decision=make_decision(),
        recommendation=make_recommendation(),
        router=router,
    )

    assert result.status == (
        ReasoningOrchestrationStatus.FAILED
    )

    assert result.reasoning is None
    assert "strategic_reasoning_failed" in (
        result.reasons
    )


def test_snapshot_is_preserved():
    router = make_router(
        FakeReasoner()
    )

    result = run_title_reasoning_orchestration(
        orchestration_id="orchestration-004",
        investigation=make_investigation(),
        decision=make_decision(),
        recommendation=make_recommendation(),
        router=router,
    )

    assert result.snapshot == make_snapshot()


def test_result_is_deterministic():
    router_one = make_router(
        FakeReasoner()
    )

    router_two = make_router(
        FakeReasoner()
    )

    first = run_title_reasoning_orchestration(
        orchestration_id="orchestration-deterministic",
        investigation=make_investigation(),
        decision=make_decision(),
        recommendation=make_recommendation(),
        router=router_one,
    )

    second = run_title_reasoning_orchestration(
        orchestration_id="orchestration-deterministic",
        investigation=make_investigation(),
        decision=make_decision(),
        recommendation=make_recommendation(),
        router=router_two,
    )

    assert first.model_dump() == (
        second.model_dump()
    )