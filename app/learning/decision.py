from app.learning.direction import benefit_change
from dataclasses import dataclass

from app.action.planner import STRATEGY_TO_ACTION
from app.models.learning import LearningContext, LearningMaturity
from app.models.strategies import Strategy, StrategyType


@dataclass(frozen=True)
class LearningDecisionPolicy:
    """Conservative policy for using historical learning as advisory evidence.

    Learning may influence strategy prioritization only. It never changes
    classification, signal detection, opportunity creation, or deterministic
    rule behavior.
    """

    min_samples: int = 5
    min_confidence: float = 0.5
    max_priority_adjustment: float = 0.10
    adjustment_scale: float = 0.25

    def __post_init__(self) -> None:
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1.")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1.")
        if not 0 < self.max_priority_adjustment <= 1:
            raise ValueError("max_priority_adjustment must be in (0, 1].")
        if not 0 < self.adjustment_scale <= 1:
            raise ValueError("adjustment_scale must be in (0, 1].")


@dataclass(frozen=True)
class LearningDecisionAdjustment:
    applied: bool
    adjustment: float
    confidence: float
    reason: str
    signal_id: str | None = None


def _find_matching_signal(
    *,
    strategy: Strategy,
    context: LearningContext,
    policy: LearningDecisionPolicy,
):
    expected_action = STRATEGY_TO_ACTION[strategy.strategy_type]

    matches = [
        signal
        for signal in context.signals
        if signal.strategy_type == strategy.strategy_type
        and signal.action_type == expected_action
        and signal.maturity == LearningMaturity.ESTABLISHED
        and signal.sample_size >= policy.min_samples
        and signal.confidence >= policy.min_confidence
    ]

    return sorted(matches, key=lambda signal: signal.signal_id)[0] if matches else None


def calculate_learning_adjustment(
    *,
    strategy: Strategy,
    context: LearningContext | None,
    policy: LearningDecisionPolicy | None = None,
) -> LearningDecisionAdjustment:
    effective_policy = policy or LearningDecisionPolicy()

    if context is None:
        return LearningDecisionAdjustment(
            applied=False,
            adjustment=0.0,
            confidence=0.0,
            reason="no_learning_context",
        )

    signal = _find_matching_signal(
        strategy=strategy,
        context=context,
        policy=effective_policy,
    )

    if signal is None:
        return LearningDecisionAdjustment(
            applied=False,
            adjustment=0.0,
            confidence=0.0,
            reason="no_eligible_learning_signal",
        )

    if signal.direction not in {"positive", "negative"}:
        return LearningDecisionAdjustment(
            applied=False,
            adjustment=0.0,
            confidence=signal.confidence,
            reason="learning_signal_not_directional",
            signal_id=signal.signal_id,
        )

    if signal.average_relative_change is None:
        return LearningDecisionAdjustment(
            applied=False,
            adjustment=0.0,
            confidence=signal.confidence,
            reason="learning_signal_missing_average_change",
            signal_id=signal.signal_id,
        )

    raw_adjustment = (
        benefit_change(signal.primary_metric, signal.average_relative_change)
        * signal.confidence
        * effective_policy.adjustment_scale
    )

    bounded_adjustment = max(
        -effective_policy.max_priority_adjustment,
        min(effective_policy.max_priority_adjustment, raw_adjustment),
    )

    return LearningDecisionAdjustment(
        applied=True,
        adjustment=round(bounded_adjustment, 6),
        confidence=signal.confidence,
        reason="established_directional_learning_signal",
        signal_id=signal.signal_id,
    )


def apply_learning_to_strategy(
    *,
    strategy: Strategy,
    context: LearningContext | None,
    policy: LearningDecisionPolicy | None = None,
) -> Strategy:
    adjustment = calculate_learning_adjustment(
        strategy=strategy,
        context=context,
        policy=policy,
    )

    if not adjustment.applied:
        return strategy.model_copy(
            update={
                "evidence": {
                    **strategy.evidence,
                    "learning": {
                        "applied": False,
                        "adjustment": 0.0,
                        "confidence": adjustment.confidence,
                        "reason": adjustment.reason,
                        "signal_id": adjustment.signal_id,
                    },
                },
            }
        )

    adjusted_priority = round(
        min(
            max(strategy.priority_score + adjustment.adjustment, 0.0),
            1.0,
        ),
        6,
    )

    return strategy.model_copy(
        update={
            "priority_score": adjusted_priority,
            "reasons": [
                *strategy.reasons,
                f"learning_adjustment={adjustment.adjustment}",
            ],
            "evidence": {
                **strategy.evidence,
                "learning": {
                    "applied": True,
                    "adjustment": adjustment.adjustment,
                    "confidence": adjustment.confidence,
                    "reason": adjustment.reason,
                    "signal_id": adjustment.signal_id,
                    "learning_context_id": context.context_id if context else None,
                    "rules_were_not_mutated": True,
                },
            },
        }
    )

