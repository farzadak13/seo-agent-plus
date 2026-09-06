from app.models.opportunities import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
)
from app.models.strategies import (
    Strategy,
    StrategyStatus,
    StrategyType,
)


def _contains_signal(
    opportunity: Opportunity,
    signal_type: str,
) -> bool:
    return signal_type in opportunity.signal_types


def _recommend_strategy_type(
    opportunity: Opportunity,
) -> StrategyType:
    if opportunity.opportunity_type == OpportunityType.CTR_RECOVERY:
        return StrategyType.SERP_TITLE_OPTIMIZATION

    if opportunity.opportunity_type == OpportunityType.POSITION_RECOVERY:
        if _contains_signal(
            opportunity,
            "position_decline_under_volatility",
        ):
            return StrategyType.MONITOR_ONLY

        return StrategyType.INTERNAL_LINKING

    if opportunity.opportunity_type == OpportunityType.MIXED_RECOVERY:
        if _contains_signal(
            opportunity,
            "position_decline_under_volatility",
        ):
            return StrategyType.CANNIBALIZATION_INVESTIGATION

        return StrategyType.SERP_TITLE_OPTIMIZATION

    if opportunity.opportunity_type == OpportunityType.VISIBILITY_GROWTH:
        return StrategyType.CONTENT_DEPTH_IMPROVEMENT

    return StrategyType.MONITOR_ONLY


def _strategy_effort(
    strategy_type: StrategyType,
) -> float:
    values = {
        StrategyType.SERP_TITLE_OPTIMIZATION: 0.25,
        StrategyType.CANNIBALIZATION_INVESTIGATION: 0.60,
        StrategyType.INTERNAL_LINKING: 0.45,
        StrategyType.CONTENT_DEPTH_IMPROVEMENT: 0.70,
        StrategyType.MONITOR_ONLY: 0.05,
    }

    return values[strategy_type]


def _strategy_risk(
    strategy_type: StrategyType,
) -> float:
    values = {
        StrategyType.SERP_TITLE_OPTIMIZATION: 0.30,
        StrategyType.CANNIBALIZATION_INVESTIGATION: 0.20,
        StrategyType.INTERNAL_LINKING: 0.35,
        StrategyType.CONTENT_DEPTH_IMPROVEMENT: 0.45,
        StrategyType.MONITOR_ONLY: 0.05,
    }

    return values[strategy_type]


def _expected_impact(
    opportunity: Opportunity,
    strategy_type: StrategyType,
) -> float:
    base = opportunity.impact_score

    multipliers = {
        StrategyType.SERP_TITLE_OPTIMIZATION: 0.95,
        StrategyType.CANNIBALIZATION_INVESTIGATION: 0.80,
        StrategyType.INTERNAL_LINKING: 0.75,
        StrategyType.CONTENT_DEPTH_IMPROVEMENT: 0.70,
        StrategyType.MONITOR_ONLY: 0.20,
    }

    return round(
        min(base * multipliers[strategy_type], 1.0),
        6,
    )


def _priority_score(
    *,
    expected_impact_score: float,
    confidence_score: float,
    effort_score: float,
    risk_score: float,
) -> float:
    score = (
        0.40 * expected_impact_score
        + 0.35 * confidence_score
        + 0.15 * (1.0 - effort_score)
        + 0.10 * (1.0 - risk_score)
    )

    return round(
        min(max(score, 0.0), 1.0),
        6,
    )


def build_strategy(
    *,
    strategy_id: str,
    opportunity: Opportunity,
) -> Strategy | None:
    if opportunity.status != OpportunityStatus.QUALIFIED:
        return None

    strategy_type = _recommend_strategy_type(
        opportunity,
    )

    effort_score = _strategy_effort(
        strategy_type,
    )

    risk_score = _strategy_risk(
        strategy_type,
    )

    expected_impact_score = _expected_impact(
        opportunity,
        strategy_type,
    )

    confidence_score = round(
        opportunity.confidence_score,
        6,
    )

    priority_score = _priority_score(
        expected_impact_score=expected_impact_score,
        confidence_score=confidence_score,
        effort_score=effort_score,
        risk_score=risk_score,
    )

    reasons = [
        f"opportunity_type={opportunity.opportunity_type.value}",
        f"strategy_type={strategy_type.value}",
        f"priority_score={priority_score}",
    ]

    evidence = {
        "opportunity_priority_score": opportunity.priority_score,
        "opportunity_impact_score": opportunity.impact_score,
        "opportunity_confidence_score": opportunity.confidence_score,
        "opportunity_risk_score": opportunity.risk_score,
        "signal_types": list(opportunity.signal_types),
        "risk_types": list(opportunity.risk_types),
    }

    return Strategy(
        strategy_id=strategy_id,
        opportunity_id=opportunity.opportunity_id,
        site_id=opportunity.site_id,
        normalized_url=opportunity.normalized_url,
        normalized_query=opportunity.normalized_query,
        opportunity_type=opportunity.opportunity_type,
        strategy_type=strategy_type,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=confidence_score,
        expected_impact_score=expected_impact_score,
        effort_score=effort_score,
        risk_score=risk_score,
        priority_score=priority_score,
        reasons=reasons,
        evidence=evidence,
        snapshot=opportunity.snapshot,
    )