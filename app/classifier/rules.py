from collections.abc import Sequence

from app.features.data_quality_gate import passes_data_quality_gate
from app.models.features import DataQualityMatrix
from app.models.classification import (
    ClassificationResult,
    ClassificationStatus,
)

from app.models.signals import Signal, SignalType



def classify_signals(
    signals: Sequence[Signal],
    *,
    min_confidence: float = 0.5,
    data_quality: DataQualityMatrix | None = None,
) -> ClassificationResult:
    """
    Classify a set of signals into PASS, INVESTIGATE, or REJECT.

    This classifier is deterministic and does not perform any
    SEO action.
    """
    if data_quality is not None:
        if not passes_data_quality_gate(data_quality):
            return ClassificationResult(
                status=ClassificationStatus.REJECT,
                confidence=0.0,
                reasons=[
                    "insufficient_data_quality",
                ],
                signal_count=sum(
                    1
                    for signal in signals
                    if signal.detected
                ),
                risk_count=sum(
                    1
                    for signal in signals
                    if (
                        signal.detected
                        and "_UNDER_" in signal.signal_type
                    )
                ),
            )
    detected_signals = [
        signal
        for signal in signals
        if signal.detected
    ]

    if not detected_signals:
        return ClassificationResult(
            status=ClassificationStatus.PASS,
            confidence=1.0,
            reasons=["no_actionable_signal"],
            signal_count=0,
            risk_count=0,
        )

    risks = [
        signal
        for signal in detected_signals
        if signal.signal_type
        in {
            SignalType.CTR_DROP_UNDER_VOLATILITY,
            SignalType.POSITION_DECLINE_UNDER_VOLATILITY,
        }
    ]

    actionable = [
        signal
        for signal in detected_signals
        if signal.signal_type
        in {
            SignalType.CTR_DROP,
            SignalType.POSITION_DECLINE,
            SignalType.HIGH_VALUE_OPPORTUNITY,
        }
    ]

    if not actionable:
        return ClassificationResult(
            status=ClassificationStatus.PASS,
            confidence=0.0,
            reasons=["only_risk_signals_present"],
            signal_count=len(detected_signals),
            risk_count=len(risks),
        )

    strongest_confidence = max(
        signal.confidence
        for signal in actionable
    )

    adjusted_confidence = strongest_confidence

    if risks:
        adjusted_confidence *= 0.8

    reasons: list[str] = [
        signal.signal_type.value
        for signal in actionable
    ]

    if risks:
        reasons.extend(
            signal.signal_type.value
            for signal in risks
        )

    if adjusted_confidence < min_confidence:
        return ClassificationResult(
            status=ClassificationStatus.REJECT,
            confidence=adjusted_confidence,
            reasons=[
                "confidence_below_threshold",
                *reasons,
            ],
            signal_count=len(detected_signals),
            risk_count=len(risks),
        )

    return ClassificationResult(
        status=ClassificationStatus.INVESTIGATE,
        confidence=adjusted_confidence,
        reasons=reasons,
        signal_count=len(detected_signals),
        risk_count=len(risks),
    )