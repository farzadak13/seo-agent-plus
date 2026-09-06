from app.models.features import FeatureSet
from app.models.signals import Signal, SignalSeverity, SignalType


def detect_high_value_opportunity(
    features: FeatureSet,
    *,
    min_impressions: int = 100,
    min_position: float = 4.0,
    max_position: float = 20.0,
    max_current_ctr: float = 0.08,
    min_data_completeness: float = 0.8,
) -> Signal:
    """
    Detect a potentially high-value SEO opportunity.

    This detector identifies an opportunity worth deeper investigation.
    It does not recommend or execute an SEO action.
    """

    evidence = {
        "current_impressions": features.volume.current_impressions,
        "current_position": features.position.current_median,
        "current_ctr": features.ctr.current_ctr,
        "baseline_ctr": features.ctr.baseline_ctr,
        "ctr_absolute_delta": features.ctr.absolute_delta,
        "baseline_window_completeness": (
            features.data_quality.baseline_window_completeness
        ),
        "current_window_completeness": (
            features.data_quality.current_window_completeness
        ),
        "min_impressions": min_impressions,
        "min_position": min_position,
        "max_position": max_position,
        "max_current_ctr": max_current_ctr,
        "min_data_completeness": min_data_completeness,
    }

    if (
        features.data_quality.baseline_window_completeness
        < min_data_completeness
        or features.data_quality.current_window_completeness
        < min_data_completeness
    ):
        return Signal(
            signal_type=SignalType.HIGH_VALUE_OPPORTUNITY,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "insufficient_data_completeness",
            },
        )

    if features.volume.current_impressions < min_impressions:
        return Signal(
            signal_type=SignalType.HIGH_VALUE_OPPORTUNITY,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "insufficient_impressions",
            },
        )

    if not (
        min_position
        <= features.position.current_median
        <= max_position
    ):
        return Signal(
            signal_type=SignalType.HIGH_VALUE_OPPORTUNITY,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "position_outside_opportunity_range",
            },
        )

    if features.ctr.current_ctr > max_current_ctr:
        return Signal(
            signal_type=SignalType.HIGH_VALUE_OPPORTUNITY,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "ctr_not_low_enough",
            },
        )

    # When the current CTR is already substantially below baseline,
    # the opportunity has stronger supporting evidence.
    ctr_decline_support = features.ctr.absolute_delta < 0

    confidence = min(
        1.0,
        (
            features.data_quality.baseline_window_completeness
            * features.data_quality.current_window_completeness
        ),
    )

    severity = (
        SignalSeverity.WARNING
        if ctr_decline_support
        else SignalSeverity.INFO
    )

    return Signal(
        signal_type=SignalType.HIGH_VALUE_OPPORTUNITY,
        detected=True,
        severity=severity,
        confidence=confidence,
        evidence={
            **evidence,
            "reason": "high_value_opportunity",
            "ctr_decline_support": ctr_decline_support,
        },
    )