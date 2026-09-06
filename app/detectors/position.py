from app.models.features import FeatureSet
from app.models.signals import Signal, SignalSeverity, SignalType


def detect_position_decline(
    features: FeatureSet,
    *,
    min_position_drop: float = 1.5,
    critical_position_drop: float = 3.0,
    min_data_completeness: float = 0.8,
) -> Signal:
    """
    Detect a meaningful decline in average search position.

    In GSC position metrics, a higher number means a worse position.

    This detector identifies a signal only.
    It does not decide what action should be taken.
    """

    evidence = {
        "baseline_median": features.position.baseline_median,
        "current_median": features.position.current_median,
        "position_delta": features.position.delta,
        "trend_slope": features.position.trend_slope,
        "trend_direction": features.position.trend_direction,
        "baseline_window_completeness": (
            features.data_quality.baseline_window_completeness
        ),
        "current_window_completeness": (
            features.data_quality.current_window_completeness
        ),
        "min_position_drop": min_position_drop,
        "critical_position_drop": critical_position_drop,
        "min_data_completeness": min_data_completeness,
    }

    if (
        features.data_quality.baseline_window_completeness
        < min_data_completeness
        or features.data_quality.current_window_completeness
        < min_data_completeness
    ):
        return Signal(
            signal_type=SignalType.POSITION_DECLINE,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "insufficient_data_completeness",
            },
        )

    position_drop = features.position.delta >= min_position_drop

    if not position_drop:
        return Signal(
            signal_type=SignalType.POSITION_DECLINE,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "position_drop_below_threshold",
            },
        )

    confidence = min(
        1.0,
        features.data_quality.baseline_window_completeness
        * features.data_quality.current_window_completeness,
    )

    severity = (
        SignalSeverity.CRITICAL
        if features.position.delta >= critical_position_drop
        else SignalSeverity.WARNING
    )

    return Signal(
        signal_type=SignalType.POSITION_DECLINE,
        detected=True,
        severity=severity,
        confidence=confidence,
        evidence={
            **evidence,
            "reason": "meaningful_position_decline",
        },
    )