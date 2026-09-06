from datetime import datetime, timezone

import pytest

from app.models.reasoning import (
    ReasoningStatus,
    TitleReasoningCandidate,
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
from app.title.generator import (
    generate_title_reasoning,
)


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-reasoning-001",
        data_snapshot_id="data-reasoning-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            6,
            9,
            0,
            tzinfo=timezone.utc,
        ),
    )


def make_recommendation() -> TitleRecommendation:
    return TitleRecommendation(
        recommendation_id="recommendation-001",
        investigation_id="investigation-001",
        decision_id="decision-001",
        strategy_id="strategy-001",
        opportunity_id="opportunity-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        primary_query="کفش مردانه",
        status=TitleRecommendationStatus.RECOMMENDED,
        current_title="عنوان فعلی",
        recommended_title=None,
        current_title_length=10,
        recommended_title_length=None,
        competitor_title_length_median=25.0,
        title_length_gap_vs_competitors=-15.0,
        confidence_score=1.0,
        reasons=["serp_evidence_passed"],
        constraints=[
            "do_not_copy_competitor_titles",
            "preserve_primary_query_relevance",
        ],
        evidence={},
        snapshot=make_snapshot(),
    )


def make_investigation() -> SERPInvestigation:
    evidence = SERPQueryEvidence(
        query="کفش مردانه",
        target_found=True,
        target_position=6,
        top_results_count=5,
        competitor_count=4,
        competitor_titles=[
            "خرید کفش مردانه ارزان",
            "قیمت کفش مردانه",
            "بهترین کفش مردانه",
            "کفش مردانه اصل",
        ],
        target_title="عنوان فعلی",
        target_title_length=10,
        competitor_title_lengths=[
            21,
            18,
            19,
            17,
        ],
        competitor_title_length_median=18.5,
        competitor_title_length_average=18.75,
        title_length_gap_vs_median=-8.5,
    )

    return SERPInvestigation(
        investigation_id="investigation-001",
        strategy_id="strategy-001",
        opportunity_id="opportunity-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        status=SERPInvestigationStatus.COMPLETED,
        primary_queries=["کفش مردانه"],
        query_evidence=[evidence],
        target_title="عنوان فعلی",
        target_found_in_any_query=True,
        target_best_position=6,
        evidence={},
    )


def make_pass_decision() -> SERPDecision:
    return SERPDecision(
        decision_id="decision-001",
        investigation_id="investigation-001",
        strategy_id="strategy-001",
        opportunity_id="opportunity-001",
        status=SERPDecisionStatus.PASS,
        confidence_score=1.0,
        reasons=["test"],
        evidence={},
        snapshot=make_snapshot(),
    )


class FakeReasoner:
    @property
    def provider_id(self) -> str:
        return "fake-reasoner"

    def generate_title_candidates(
        self,
        reasoning_input,
    ) -> list[TitleReasoningCandidate]:
        assert reasoning_input.primary_query == (
            "کفش مردانه"
        )

        return [
            TitleReasoningCandidate(
                candidate_id="candidate-1",
                title="خرید کفش مردانه با بهترین قیمت",
                rationale="query relevance",
                confidence_score=0.90,
            ),
            TitleReasoningCandidate(
                candidate_id="candidate-2",
                title="کفش مردانه اصل و باکیفیت",
                rationale="query coverage",
                confidence_score=0.80,
            ),
        ]


class FakeInvalidReasoner:
    @property
    def provider_id(self) -> str:
        return "invalid-fake-reasoner"

    def generate_title_candidates(
        self,
        reasoning_input,
    ) -> list[TitleReasoningCandidate]:
        return [
            TitleReasoningCandidate(
                candidate_id="invalid-1",
                title="عنوان بدون کلمه کلیدی",
                rationale="invalid",
                confidence_score=0.99,
            ),
            TitleReasoningCandidate(
                candidate_id="invalid-2",
                title="خرید کفش مردانه با بهترین قیمت",
                rationale="competitor clone",
                confidence_score=0.95,
            ),
        ]


def test_ready_reasoning_result_contains_valid_candidates():
    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=FakeReasoner(),
    )

    assert result.status == ReasoningStatus.READY
    assert len(result.candidates) == 2
    assert result.selected_candidate_id == (
        "candidate-1"
    )


def test_selected_candidate_has_highest_confidence():
    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=FakeReasoner(),
    )

    scores = {
        candidate.candidate_id:
        candidate.confidence_score
        for candidate in result.candidates
    }

    assert scores[result.selected_candidate_id] == 0.90


def test_invalid_candidates_are_filtered():
    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=FakeInvalidReasoner(),
    )

    assert result.status == ReasoningStatus.READY
    assert len(result.candidates) == 1
    assert result.candidates[0].candidate_id == (
        "invalid-2"
    )


def test_all_invalid_candidates_reject_result():
    class AllInvalidReasoner:
        @property
        def provider_id(self) -> str:
            return "all-invalid"

        def generate_title_candidates(
            self,
            reasoning_input,
        ):
            return [
                TitleReasoningCandidate(
                    candidate_id="bad-1",
                    title="عنوان نامرتبط",
                    rationale="bad",
                    confidence_score=0.99,
                )
            ]

    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=AllInvalidReasoner(),
    )

    assert result.status == ReasoningStatus.REJECTED
    assert result.candidates == []
    assert result.selected_candidate_id is None


def test_non_pass_decision_cannot_enter_reasoning():
    decision = SERPDecision(
        decision_id="decision-002",
        investigation_id="investigation-001",
        strategy_id="strategy-001",
        opportunity_id="opportunity-001",
        status=SERPDecisionStatus.INVESTIGATE,
        confidence_score=0.5,
        reasons=["insufficient_evidence"],
        evidence={},
        snapshot=make_snapshot(),
    )

    with pytest.raises(
        ValueError,
        match="requires a PASS SERP decision",
    ):
        generate_title_reasoning(
            recommendation=make_recommendation(),
            investigation=make_investigation(),
            decision=decision,
            reasoner=FakeReasoner(),
        )


def test_competitor_title_is_rejected():
    class CompetitorCloneReasoner:
        @property
        def provider_id(self) -> str:
            return "competitor-clone"

        def generate_title_candidates(
            self,
            reasoning_input,
        ):
            return [
                TitleReasoningCandidate(
                    candidate_id="clone",
                    title="قیمت کفش مردانه",
                    rationale="copy competitor",
                    confidence_score=1.0,
                )
            ]

    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=CompetitorCloneReasoner(),
    )

    assert result.status == ReasoningStatus.REJECTED


def test_same_title_as_current_is_rejected():
    class SameTitleReasoner:
        @property
        def provider_id(self) -> str:
            return "same-title"

        def generate_title_candidates(
            self,
            reasoning_input,
        ):
            return [
                TitleReasoningCandidate(
                    candidate_id="same",
                    title="عنوان فعلی",
                    rationale="same",
                    confidence_score=1.0,
                )
            ]

    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=SameTitleReasoner(),
    )

    assert result.status == ReasoningStatus.REJECTED


def test_snapshot_is_preserved():
    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=FakeReasoner(),
    )

    assert result.snapshot == make_snapshot()


def test_provider_id_is_preserved_in_evidence():
    result = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=FakeReasoner(),
    )

    assert result.evidence["provider_id"] == (
        "fake-reasoner"
    )


def test_reasoning_is_deterministic():
    first = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=FakeReasoner(),
    )

    second = generate_title_reasoning(
        recommendation=make_recommendation(),
        investigation=make_investigation(),
        decision=make_pass_decision(),
        reasoner=FakeReasoner(),
    )

    assert first.model_dump() == second.model_dump()