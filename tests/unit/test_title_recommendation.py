from datetime import datetime, timezone

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
    TitleRecommendationStatus,
)
from app.title.engine import build_title_recommendation


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-title-001",
        data_snapshot_id="data-title-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            6,
            8,
            30,
            tzinfo=timezone.utc,
        ),
    )


def make_evidence(
    *,
    query: str = "کفش مردانه",
    target_found: bool = True,
    target_position: int = 5,
    competitor_count: int = 3,
    median: float = 20.0,
    average: float = 20.0,
) -> SERPQueryEvidence:
    lengths = [20] * competitor_count

    return SERPQueryEvidence(
        query=query,
        target_found=target_found,
        target_position=target_position,
        top_results_count=competitor_count + (
            1 if target_found else 0
        ),
        competitor_count=competitor_count,
        competitor_titles=[
            f"رقیب {index}"
            for index in range(competitor_count)
        ],
        target_title="خرید کفش مردانه",
        target_title_length=len(
            "خرید کفش مردانه"
        ),
        competitor_title_lengths=lengths,
        competitor_title_length_median=median,
        competitor_title_length_average=average,
        title_length_gap_vs_median=(
            len("خرید کفش مردانه") - median
        ),
    )


def make_investigation(
    *,
    query_evidence: list[SERPQueryEvidence],
    target_title: str | None = "خرید کفش مردانه",
) -> SERPInvestigation:
    return SERPInvestigation(
        investigation_id="investigation-title-001",
        strategy_id="strategy-title-001",
        opportunity_id="opportunity-title-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        status=SERPInvestigationStatus.COMPLETED,
        primary_queries=[
            evidence.query
            for evidence in query_evidence
        ],
        query_evidence=query_evidence,
        target_title=target_title,
        target_found_in_any_query=any(
            evidence.target_found
            for evidence in query_evidence
        ),
        target_best_position=min(
            (
                evidence.target_position
                for evidence in query_evidence
                if evidence.target_position is not None
            ),
            default=None,
        ),
        evidence={},
    )


def make_decision(
    *,
    status: SERPDecisionStatus,
    confidence_score: float = 1.0,
) -> SERPDecision:
    return SERPDecision(
        decision_id="decision-title-001",
        investigation_id="investigation-title-001",
        strategy_id="strategy-title-001",
        opportunity_id="opportunity-title-001",
        status=status,
        confidence_score=confidence_score,
        reasons=["test"],
        evidence={},
        snapshot=make_snapshot(),
    )


def test_pass_creates_recommendation():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-001",
    )

    assert (
        result.status
        == TitleRecommendationStatus.RECOMMENDED
    )

    assert result.recommendation_id == (
        "recommendation-001"
    )

    assert result.primary_query == "کفش مردانه"


def test_recommendation_does_not_generate_title_yet():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-002",
    )

    assert result.recommended_title is None
    assert result.recommended_title_length is None

    assert (
        "title_generation_deferred_to_strategic_reasoning"
        in result.reasons
    )


def test_current_title_is_preserved():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ],
        target_title="  خرید   کفش مردانه  ",
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-003",
    )

    assert result.current_title == (
        "خرید کفش مردانه"
    )

    assert result.current_title_length == len(
        "خرید کفش مردانه"
    )


def test_competitor_statistics_are_preserved():
    investigation = make_investigation(
        query_evidence=[
            make_evidence(
                competitor_count=4,
                median=24.5,
                average=25.0,
            )
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-004",
    )

    assert (
        result.competitor_title_length_median
        == 24.5
    )

    assert (
        result.title_length_gap_vs_competitors
        == len("خرید کفش مردانه") - 24.5
    )

    assert result.evidence[
        "competitor_count"
    ] == 4


def test_non_pass_decision_rejects_recommendation():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.INVESTIGATE,
        confidence_score=0.5,
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-005",
    )

    assert (
        result.status
        == TitleRecommendationStatus.REJECTED
    )

    assert result.recommended_title is None

    assert result.reasons == [
        "serp_decision_not_pass"
    ]


def test_reject_decision_also_rejects_recommendation():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.REJECT
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-006",
    )

    assert (
        result.status
        == TitleRecommendationStatus.REJECTED
    )

    assert result.recommended_title is None


def test_empty_query_evidence_is_rejected():
    investigation = SERPInvestigation(
        investigation_id="investigation-title-empty",
        strategy_id="strategy-title-001",
        opportunity_id="opportunity-title-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        status=SERPInvestigationStatus.COMPLETED,
        primary_queries=["کفش مردانه"],
        query_evidence=[],
        target_title="خرید کفش مردانه",
        target_found_in_any_query=True,
        target_best_position=5,
        evidence={},
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-007",
    )

    assert (
        result.status
        == TitleRecommendationStatus.REJECTED
    )

    assert result.recommended_title is None

    assert result.confidence_score == 0.0

    assert result.reasons == [
        "no_serp_query_evidence"
    ]


def test_primary_evidence_prefers_best_target_position():
    first = make_evidence(
        query="خرید کفش",
        target_position=8,
        competitor_count=5,
        median=30.0,
    )

    second = make_evidence(
        query="کفش مردانه",
        target_position=3,
        competitor_count=3,
        median=20.0,
    )

    investigation = make_investigation(
        query_evidence=[
            first,
            second,
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-008",
    )

    assert result.primary_query == "کفش مردانه"
    assert result.competitor_title_length_median == 20.0


def test_tied_position_prefers_more_competitor_evidence():
    first = make_evidence(
        query="query-one",
        target_position=5,
        competitor_count=3,
        median=20.0,
    )

    second = make_evidence(
        query="query-two",
        target_position=5,
        competitor_count=7,
        median=27.0,
    )

    investigation = make_investigation(
        query_evidence=[
            first,
            second,
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-009",
    )

    assert result.primary_query == "query-two"
    assert result.competitor_title_length_median == 27.0


def test_constraints_are_explicit():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-010",
    )

    assert (
        "do_not_generate_title_without_strategic_reasoning"
        in result.constraints
    )

    assert (
        "do_not_copy_competitor_titles"
        in result.constraints
    )

    assert (
        "preserve_primary_query_relevance"
        in result.constraints
    )


def test_snapshot_is_preserved():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-011",
    )

    assert result.snapshot == decision.snapshot


def test_recommendation_is_deterministic():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    first = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-deterministic",
    )

    second = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-deterministic",
    )

    assert first.model_dump() == second.model_dump()

def test_recommendation_is_platform_agnostic():
    investigation = make_investigation(
        query_evidence=[
            make_evidence()
        ]
    )

    decision = make_decision(
        status=SERPDecisionStatus.PASS
    )

    result = build_title_recommendation(
        investigation=investigation,
        decision=decision,
        recommendation_id="recommendation-platform-neutral",
    )

    dumped = result.model_dump_json().lower()

    assert "wordpress" not in dumped
    assert "wp_post_id" not in dumped
    assert "php" not in dumped
    assert "django" not in dumped
    assert "asp.net" not in dumped