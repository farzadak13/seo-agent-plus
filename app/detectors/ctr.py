from app.models.features import FeatureSet
from app.models.signals import Signal, SignalSeverity, SignalType


def detect_ctr_drop(
    features: FeatureSet,
    *,
    min_absolute_drop: float = 0.02,
    critical_absolute_drop: float = 0.05,
    min_data_completeness: float = 0.8,
) -> Signal:
    """
    Detect a meaningful CTR decline.

    This detector identifies a signal only.
    It does not decide what action should be taken.
    """

    evidence = {
        "baseline_ctr": features.ctr.baseline_ctr,
        "current_ctr": features.ctr.current_ctr,
        "absolute_delta": features.ctr.absolute_delta,
        "relative_change": features.ctr.relative_change,
        "baseline_ctr_zero": features.ctr.baseline_ctr_zero,
        "baseline_window_completeness": (
            features.data_quality.baseline_window_completeness
        ),
        "current_window_completeness": (
            features.data_quality.current_window_completeness
        ),
        "min_absolute_drop": min_absolute_drop,
        "critical_absolute_drop": critical_absolute_drop,
        "min_data_completeness": min_data_completeness,
    }

    # Relative CTR change is undefined when the baseline CTR is zero.
    if features.ctr.baseline_ctr_zero:
        return Signal(
            signal_type=SignalType.CTR_DROP,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "baseline_ctr_zero",
            },
        )

    # Do not produce a meaningful signal when the measurement window
    # does not contain enough usable data.
    if (
        features.data_quality.baseline_window_completeness
        < min_data_completeness
        or features.data_quality.current_window_completeness
        < min_data_completeness
    ):
        return Signal(
            signal_type=SignalType.CTR_DROP,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "insufficient_data_completeness",
            },
        )

    # CTR delta is current CTR minus baseline CTR.
    # Therefore a negative value means a decline.
    detected = features.ctr.absolute_delta <= -min_absolute_drop

    if not detected:
        return Signal(
            signal_type=SignalType.CTR_DROP,
            detected=False,
            severity=SignalSeverity.INFO,
            confidence=0.0,
            evidence={
                **evidence,
                "reason": "drop_below_threshold",
            },
        )

    # Confidence is based on the completeness of both windows.
    confidence = min(
        1.0,
        features.data_quality.baseline_window_completeness
        * features.data_quality.current_window_completeness,
    )

    severity = (
        SignalSeverity.CRITICAL
        if features.ctr.absolute_delta <= -critical_absolute_drop
        else SignalSeverity.WARNING
    )

    return Signal(
        signal_type=SignalType.CTR_DROP,
        detected=True,
        severity=severity,
        confidence=confidence,
        evidence={
            **evidence,
            "reason": "meaningful_ctr_drop",
        },
    )