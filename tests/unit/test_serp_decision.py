from datetime import datetime, timezone

from app.models.serp import (
    SERPInvestigation,
    SERPInvestigationStatus,
    SERPQueryEvidence,
)
from app.models.serp_decision import SERPDecisionStatus
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import (
    Strategy,
    StrategyStatus,
    StrategyType,
)
from app.serp.decision import (
    MIN_COMPETITOR_EVIDENCE,
    build_serp_decision,
)


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-serp-decision-001",
        data_snapshot_id="data-serp-decision-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            6,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )


def make_title_strategy() -> Strategy:
    return Strategy(
        strategy_id="strategy-serp-decision-001",
        opportunity_id="opportunity-serp-decision-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type="ctr_recovery",
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=0.85,
        expected_impact_score=0.80,
        effort_score=0.25,
        risk_score=0.30,
        priority_score=0.78,
        reasons=[
            "ctr_recovery",
        ],
        evidence={},
        snapshot=make_snapshot(),
    )


def make_monitor_strategy() -> Strategy:
    return Strategy(
        strategy_id="strategy-monitor-decision-001",
        opportunity_id="opportunity-monitor-decision-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type="position_recovery",
        strategy_type=StrategyType.MONITOR_ONLY,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=0.80,
        expected_impact_score=0.40,
        effort_score=0.05,
        risk_score=0.05,
        priority_score=0.60,
        reasons=[
            "monitor_due_to_volatility",
        ],
        evidence={},
        snapshot=make_snapshot(),
    )


def make_query_evidence(
    *,
    query: str = "کفش مردانه",
    target_found: bool = True,
    target_position: int | None = 5,
    competitor_count: int = 3,
    target_title: str | None = "عنوان فعلی",
) -> SERPQueryEvidence:
    competitor_titles = [
        f"عنوان رقیب {index}"
        for index in range(competitor_count)
    ]

    competitor_lengths = [
        len(title)
        for title in competitor_titles
    ]

    median = None
    average = None

    if competitor_lengths:
        ordered = sorted(competitor_lengths)
        middle = len(ordered) // 2

        if len(ordered) % 2 == 1:
            median = float(ordered[middle])
        else:
            median = float(
                (ordered[middle - 1] + ordered[middle]) / 2
            )

        average = float(
            sum(competitor_lengths) / len(competitor_lengths)
        )

    target_title_length = (
        len(target_title)
        if target_title
        else None
    )

    title_gap = None

    if (
        target_title_length is not None
        and median is not None
    ):
        title_gap = float(
            target_title_length - median
        )

    return SERPQueryEvidence(
        query=query,
        target_found=target_found,
        target_position=target_position,
        top_results_count=competitor_count + (
            1 if target_found else 0
        ),
        competitor_count=competitor_count,
        competitor_titles=competitor_titles,
        target_title=target_title,
        target_title_length=target_title_length,
        competitor_title_lengths=competitor_lengths,
        competitor_title_length_median=median,
        competitor_title_length_average=average,
        title_length_gap_vs_median=title_gap,
    )


def make_completed_investigation(
    *,
    target_found: bool = True,
    target_title: str | None = "عنوان فعلی",
    competitor_count: int = 3,
    target_position: int | None = 5,
) -> SERPInvestigation:
    query_evidence = make_query_evidence(
        target_found=target_found,
        target_position=target_position,
        competitor_count=competitor_count,
        target_title=target_title,
    )

    return SERPInvestigation(
        investigation_id="investigation-decision-001",
        strategy_id="strategy-serp-decision-001",
        opportunity_id="opportunity-serp-decision-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        status=SERPInvestigationStatus.COMPLETED,
        primary_queries=["کفش مردانه"],
        query_evidence=[query_evidence],
        target_title=target_title,
        target_found_in_any_query=target_found,
        target_best_position=target_position,
        evidence={},
    )


def make_incomplete_investigation() -> SERPInvestigation:
    return SERPInvestigation(
        investigation_id="investigation-incomplete-001",
        strategy_id="strategy-serp-decision-001",
        opportunity_id="opportunity-serp-decision-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        status=SERPInvestigationStatus.INCOMPLETE,
        primary_queries=["کفش مردانه"],
        query_evidence=[],
        target_title=None,
        target_found_in_any_query=False,
        target_best_position=None,
        evidence={},
    )


def test_sufficient_serp_evidence_passes():
    strategy = make_title_strategy()
    investigation = make_completed_investigation()

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-001",
    )

    assert decision.status == SERPDecisionStatus.PASS
    assert decision.confidence_score == 1.0
    assert decision.reasons == [
        "sufficient_serp_evidence_for_title_recommendation",
    ]


def test_pass_does_not_mean_title_change():
    strategy = make_title_strategy()
    investigation = make_completed_investigation()

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-002",
    )

    assert decision.status == SERPDecisionStatus.PASS
    assert "change_title" not in decision.reasons
    assert "execute_title_change" not in decision.reasons


def test_missing_target_requires_investigation():
    strategy = make_title_strategy()

    investigation = make_completed_investigation(
        target_found=False,
        target_position=None,
    )

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-003",
    )

    assert decision.status == SERPDecisionStatus.INVESTIGATE
    assert "target_not_found_in_serp" in decision.reasons
    assert decision.confidence_score < 1.0


def test_missing_target_title_requires_investigation():
    strategy = make_title_strategy()

    investigation = make_completed_investigation(
        target_found=True,
        target_title=None,
    )

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-004",
    )

    assert decision.status == SERPDecisionStatus.INVESTIGATE
    assert "target_title_missing" in decision.reasons


def test_insufficient_competitor_evidence_requires_investigation():
    strategy = make_title_strategy()

    investigation = make_completed_investigation(
        competitor_count=MIN_COMPETITOR_EVIDENCE - 1,
    )

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-005",
    )

    assert decision.status == SERPDecisionStatus.INVESTIGATE
    assert "insufficient_competitor_evidence" in decision.reasons


def test_incomplete_investigation_requires_investigation():
    strategy = make_title_strategy()
    investigation = make_incomplete_investigation()

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-006",
    )

    assert decision.status == SERPDecisionStatus.INVESTIGATE
    assert decision.reasons == [
        "serp_investigation_incomplete",
    ]
    assert decision.confidence_score == 0.25


def test_non_title_strategy_is_rejected():
    strategy = make_monitor_strategy()
    investigation = make_incomplete_investigation()

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-007",
    )

    assert decision.status == SERPDecisionStatus.REJECT
    assert decision.confidence_score == 1.0
    assert decision.reasons == [
        "serp_title_optimization_not_required",
    ]


def test_competitor_threshold_is_inclusive():
    strategy = make_title_strategy()

    investigation = make_completed_investigation(
        competitor_count=MIN_COMPETITOR_EVIDENCE,
    )

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-008",
    )

    assert decision.status == SERPDecisionStatus.PASS


def test_decision_preserves_strategy_context():
    strategy = make_title_strategy()
    investigation = make_completed_investigation()

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-009",
    )

    assert decision.strategy_id == strategy.strategy_id
    assert decision.opportunity_id == strategy.opportunity_id
    assert decision.investigation_id == (
        investigation.investigation_id
    )
    assert decision.snapshot == strategy.snapshot


def test_decision_contains_evidence_summary():
    strategy = make_title_strategy()
    investigation = make_completed_investigation(
        target_position=7,
        competitor_count=4,
    )

    decision = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-010",
    )

    assert decision.evidence[
        "target_found_in_any_query"
    ] is True

    assert decision.evidence[
        "target_best_position"
    ] == 7

    assert decision.evidence[
        "max_competitor_count"
    ] == 4

    assert decision.evidence[
        "minimum_competitor_evidence"
    ] == MIN_COMPETITOR_EVIDENCE


def test_decision_is_deterministic():
    strategy = make_title_strategy()
    investigation = make_completed_investigation(
        target_position=6,
        competitor_count=4,
    )

    first = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-deterministic",
    )

    second = build_serp_decision(
        strategy=strategy,
        investigation=investigation,
        decision_id="decision-deterministic",
    )

    assert first.model_dump() == second.model_dump()