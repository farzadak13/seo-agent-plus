from app.models.serp import (
    SERPInvestigation,
    SERPInvestigationStatus,
)
from app.models.serp_decision import (
    SERPDecision,
    SERPDecisionStatus,
)
from app.models.strategies import Strategy, StrategyType


MIN_COMPETITOR_EVIDENCE = 3


def _count_competitor_evidence(
    investigation: SERPInvestigation,
) -> int:
    return max(
        (
            evidence.competitor_count
            for evidence in investigation.query_evidence
        ),
        default=0,
    )


def _calculate_confidence(
    *,
    investigation: SERPInvestigation,
    target_found: bool,
    target_title_present: bool,
    sufficient_competitors: bool,
) -> float:
    components = 0

    if investigation.status == SERPInvestigationStatus.COMPLETED:
        components += 1

    if target_found:
        components += 1

    if target_title_present:
        components += 1

    if sufficient_competitors:
        components += 1

    return components / 4


def build_serp_decision(
    *,
    strategy: Strategy,
    investigation: SERPInvestigation,
    decision_id: str,
) -> SERPDecision:
    if strategy.strategy_type != StrategyType.SERP_TITLE_OPTIMIZATION:
        return SERPDecision(
            decision_id=decision_id,
            investigation_id=investigation.investigation_id,
            strategy_id=strategy.strategy_id,
            opportunity_id=strategy.opportunity_id,
            status=SERPDecisionStatus.REJECT,
            confidence_score=1.0,
            reasons=[
                "serp_title_optimization_not_required",
            ],
            evidence={
                "strategy_type": strategy.strategy_type.value,
            },
            snapshot=strategy.snapshot,
        )

    if investigation.status != SERPInvestigationStatus.COMPLETED:
        return SERPDecision(
            decision_id=decision_id,
            investigation_id=investigation.investigation_id,
            strategy_id=strategy.strategy_id,
            opportunity_id=strategy.opportunity_id,
            status=SERPDecisionStatus.INVESTIGATE,
            confidence_score=0.25,
            reasons=[
                "serp_investigation_incomplete",
            ],
            evidence={
                "investigation_status": investigation.status.value,
            },
            snapshot=strategy.snapshot,
        )

    target_found = investigation.target_found_in_any_query

    target_title_present = bool(
        investigation.target_title
        and investigation.target_title.strip()
    )

    max_competitors = _count_competitor_evidence(
        investigation
    )

    sufficient_competitors = (
        max_competitors >= MIN_COMPETITOR_EVIDENCE
    )

    confidence = _calculate_confidence(
        investigation=investigation,
        target_found=target_found,
        target_title_present=target_title_present,
        sufficient_competitors=sufficient_competitors,
    )

    reasons: list[str] = []

    if not target_found:
        reasons.append("target_not_found_in_serp")

    if not target_title_present:
        reasons.append("target_title_missing")

    if not sufficient_competitors:
        reasons.append(
            "insufficient_competitor_evidence"
        )

    if reasons:
        return SERPDecision(
            decision_id=decision_id,
            investigation_id=investigation.investigation_id,
            strategy_id=strategy.strategy_id,
            opportunity_id=strategy.opportunity_id,
            status=SERPDecisionStatus.INVESTIGATE,
            confidence_score=confidence,
            reasons=reasons,
            evidence={
                "target_found_in_any_query": target_found,
                "target_title_present": target_title_present,
                "max_competitor_count": max_competitors,
                "minimum_competitor_evidence": (
                    MIN_COMPETITOR_EVIDENCE
                ),
            },
            snapshot=strategy.snapshot,
        )

    return SERPDecision(
        decision_id=decision_id,
        investigation_id=investigation.investigation_id,
        strategy_id=strategy.strategy_id,
        opportunity_id=strategy.opportunity_id,
        status=SERPDecisionStatus.PASS,
        confidence_score=confidence,
        reasons=[
            "sufficient_serp_evidence_for_title_recommendation",
        ],
        evidence={
            "target_found_in_any_query": target_found,
            "target_best_position": investigation.target_best_position,
            "target_title_present": target_title_present,
            "max_competitor_count": max_competitors,
            "minimum_competitor_evidence": (
                MIN_COMPETITOR_EVIDENCE
            ),
        },
        snapshot=strategy.snapshot,
    )