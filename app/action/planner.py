from app.models.actions import (
    Action,
    ActionRiskLevel,
    ActionStatus,
    ActionType,
)
from app.models.strategies import (
    Strategy,
    StrategyType,
)


STRATEGY_TO_ACTION: dict[
    StrategyType,
    ActionType,
] = {
    StrategyType.SERP_TITLE_OPTIMIZATION: (
        ActionType.OPTIMIZE_TITLE
    ),
    StrategyType.CANNIBALIZATION_INVESTIGATION: (
        ActionType.INVESTIGATE_CANNIBALIZATION
    ),
    StrategyType.INTERNAL_LINKING: (
        ActionType.IMPROVE_INTERNAL_LINKING
    ),
    StrategyType.CONTENT_DEPTH_IMPROVEMENT: (
        ActionType.IMPROVE_CONTENT_DEPTH
    ),
    StrategyType.MONITOR_ONLY: (
        ActionType.MONITOR
    ),
}


def _risk_level(strategy: Strategy) -> ActionRiskLevel:
    if strategy.risk_score >= 0.65:
        return ActionRiskLevel.HIGH

    if strategy.risk_score >= 0.35:
        return ActionRiskLevel.MEDIUM

    return ActionRiskLevel.LOW


def _requires_approval(
    *,
    action_type: ActionType,
    risk_level: ActionRiskLevel,
) -> bool:
    if action_type in {
        ActionType.OPTIMIZE_TITLE,
        ActionType.IMPROVE_CONTENT_DEPTH,
    }:
        return True

    return risk_level in {
        ActionRiskLevel.MEDIUM,
        ActionRiskLevel.HIGH,
    }


def build_action(
    *,
    strategy: Strategy,
    action_id: str,
) -> Action:
    try:
        action_type = STRATEGY_TO_ACTION[
            strategy.strategy_type
        ]
    except KeyError as exc:
        raise ValueError(
            "No action mapping exists for strategy type: "
            f"{strategy.strategy_type.value}"
        ) from exc

    risk_level = _risk_level(strategy)

    requires_approval = _requires_approval(
        action_type=action_type,
        risk_level=risk_level,
    )

    initial_status = (
        ActionStatus.AWAITING_APPROVAL
        if requires_approval
        else ActionStatus.PLANNED
    )

    return Action(
        action_id=action_id,
        strategy_id=strategy.strategy_id,
        opportunity_id=strategy.opportunity_id,
        site_id=strategy.site_id,
        normalized_url=strategy.normalized_url,
        normalized_query=strategy.normalized_query,
        strategy_type=strategy.strategy_type,
        action_type=action_type,
        status=initial_status,
        risk_level=risk_level,
        confidence_score=strategy.confidence_score,
        expected_impact_score=(
            strategy.expected_impact_score
        ),
        priority_score=strategy.priority_score,
        requires_approval=requires_approval,
        reasons=list(strategy.reasons),
        parameters={},
        evidence={
            "strategy_priority_score": (
                strategy.priority_score
            ),
            "strategy_confidence_score": (
                strategy.confidence_score
            ),
            "strategy_risk_score": strategy.risk_score,
            "strategy_type": strategy.strategy_type.value,
            "action_type": action_type.value,
        },
        snapshot=strategy.snapshot,
    )