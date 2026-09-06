from app.models.features import FeatureSet
from app.models.signals import Signal, SignalSeverity, SignalType


def detect_position_volatility(
    features: FeatureSet,
    *,
    min_mad: float = 1.5,
    critical_mad: float = 3.0,
    min_data_completeness: float = 0.8,
) -> Signal:
    """
    Detect unusually high position volatility.

    MAD (Median Absolute Deviation) is used as a robust measure
    of dispersion in current daily position observations.

    This detector identifies a signal only.
    It does not recommend or execute an SEO action.
    """

    evidence = {
        "mad_current": features.position.mad_current,
        "trend_slope": features.position.trend_slope,
        "trend_direction": features.position.trend_direction,
        "baseline_window_completeness": (
            features.data_quality.baseline_window_completeness
        ),
        "current_window_completeness": (
            features.data_quality.current_window_completeness
        ),
        "min_mad": min_mad,
        "critical_mad": critical_mad,
        "min_data_completeness": min_data_completeness,
    }

    if (
        features.data_quality.baseline_window_completeness
        < min_data_completeness
        or features.data_quality.current_window_completeness
        < min_data_completeness
    ):
        return Signal(
            signal_type=SignalType.VOLATILITY,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "insufficient_data_completeness",
            },
        )

    detected = features.position.mad_current >= min_mad

    if not detected:
        return Signal(
            signal_type=SignalType.VOLATILITY,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "volatility_below_threshold",
            },
        )

    confidence = min(
        1.0,
        features.data_quality.baseline_window_completeness
        * features.data_quality.current_window_completeness,
    )

    severity = (
        SignalSeverity.CRITICAL
        if features.position.mad_current >= critical_mad
        else SignalSeverity.WARNING
    )

    return Signal(
        signal_type=SignalType.VOLATILITY,
        detected=True,
        severity=severity,
        confidence=confidence,
        evidence={
            **evidence,
            "reason": "high_position_volatility",
        },
    )