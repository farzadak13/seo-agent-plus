from collections.abc import Sequence

from app.models.signals import (
    Signal,
    SignalSeverity,
    SignalType,
)


def detect_signal_risks(
    signals: Sequence[Signal],
) -> list[Signal]:
    """
    Detect contextual risks caused by conflicting or amplifying signals.

    This detector does not reject or invalidate the original signals.
    It produces additional risk signals for the classifier.
    """

    signal_types = {
        signal.signal_type
        for signal in signals
        if signal.detected
    }

    risks: list[Signal] = []

    volatility_present = SignalType.VOLATILITY in signal_types

    if not volatility_present:
        return risks

    if SignalType.CTR_DROP in signal_types:
        ctr_signal = next(
            signal
            for signal in signals
            if signal.signal_type == SignalType.CTR_DROP
            and signal.detected
        )

        confidence = min(
            ctr_signal.confidence,
            0.8,
        )

        risks.append(
            Signal(
                signal_type=SignalType.CTR_DROP_UNDER_VOLATILITY,
                detected=True,
                severity=SignalSeverity.WARNING,
                confidence=confidence,
                evidence={
                    "original_signal": SignalType.CTR_DROP.value,
                    "risk_signal": SignalType.VOLATILITY.value,
                    "original_confidence": ctr_signal.confidence,
                    "reason": "ctr_drop_occurs_during_high_volatility",
                },
            )
        )

    if SignalType.POSITION_DECLINE in signal_types:
        position_signal = next(
            signal
            for signal in signals
            if signal.signal_type == SignalType.POSITION_DECLINE
            and signal.detected
        )

        confidence = min(
            position_signal.confidence,
            0.8,
        )

        risks.append(
            Signal(
                signal_type=SignalType.POSITION_DECLINE_UNDER_VOLATILITY,
                detected=True,
                severity=SignalSeverity.WARNING,
                confidence=confidence,
                evidence={
                    "original_signal": SignalType.POSITION_DECLINE.value,
                    "risk_signal": SignalType.VOLATILITY.value,
                    "original_confidence": position_signal.confidence,
                    "reason": (
                        "position_decline_occurs_during_high_volatility"
                    ),
                },
            )
        )

    return risks