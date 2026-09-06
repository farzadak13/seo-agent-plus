import pytest

from app.classifier.rules import classify_signals
from app.models.classification import ClassificationStatus
from app.models.signals import (
    Signal,
    SignalSeverity,
    SignalType,
)


def make_signal(
    signal_type: SignalType,
    *,
    confidence: float = 1.0,
) -> Signal:
    return Signal(
        signal_type=signal_type,
        detected=True,
        severity=SignalSeverity.WARNING,
        confidence=confidence,
    )


def test_no_detected_signals_returns_pass():
    signal = Signal(
        signal_type=SignalType.CTR_DROP,
        detected=False,
        confidence=0.0,
    )

    result = classify_signals([signal])

    assert result.status == ClassificationStatus.PASS
    assert result.confidence == 1.0
    assert result.signal_count == 0
    assert result.risk_count == 0
    assert result.reasons == ["no_actionable_signal"]


def test_actionable_signal_returns_investigate():
    signals = [
        make_signal(
            SignalType.CTR_DROP,
            confidence=0.9,
        ),
    ]

    result = classify_signals(signals)

    assert result.status == ClassificationStatus.INVESTIGATE
    assert result.confidence == pytest.approx(0.9)
    assert result.signal_count == 1
    assert result.risk_count == 0
    assert "CTR_DROP" in result.reasons


def test_high_value_opportunity_returns_investigate():
    signals = [
        make_signal(
            SignalType.HIGH_VALUE_OPPORTUNITY,
            confidence=0.8,
        ),
    ]

    result = classify_signals(signals)

    assert result.status == ClassificationStatus.INVESTIGATE
    assert result.confidence == pytest.approx(0.8)


def test_risk_reduces_confidence():
    signals = [
        make_signal(
            SignalType.CTR_DROP,
            confidence=0.9,
        ),
        make_signal(
            SignalType.VOLATILITY,
            confidence=0.9,
        ),
        make_signal(
            SignalType.CTR_DROP_UNDER_VOLATILITY,
            confidence=0.9,
        ),
    ]

    result = classify_signals(signals)

    assert result.status == ClassificationStatus.INVESTIGATE
    assert result.confidence == pytest.approx(0.72)
    assert result.risk_count == 1


def test_low_confidence_is_rejected():
    signals = [
        make_signal(
            SignalType.CTR_DROP,
            confidence=0.4,
        ),
    ]

    result = classify_signals(signals)

    assert result.status == ClassificationStatus.REJECT
    assert result.confidence == pytest.approx(0.4)
    assert "confidence_below_threshold" in result.reasons


def test_risk_only_signal_does_not_trigger_investigation():
    signals = [
        make_signal(
            SignalType.CTR_DROP_UNDER_VOLATILITY,
            confidence=0.8,
        ),
    ]

    result = classify_signals(signals)

    assert result.status == ClassificationStatus.PASS
    assert result.confidence == 0.0
    assert result.signal_count == 1
    assert result.risk_count == 1


def test_custom_confidence_threshold():
    signals = [
        make_signal(
            SignalType.POSITION_DECLINE,
            confidence=0.7,
        ),
    ]

    result = classify_signals(
        signals,
        min_confidence=0.8,
    )

    assert result.status == ClassificationStatus.REJECT


def test_multiple_actionable_signals_are_preserved():
    signals = [
        make_signal(
            SignalType.CTR_DROP,
            confidence=0.8,
        ),
        make_signal(
            SignalType.POSITION_DECLINE,
            confidence=0.7,
        ),
        make_signal(
            SignalType.HIGH_VALUE_OPPORTUNITY,
            confidence=0.9,
        ),
    ]

    result = classify_signals(signals)

    assert result.status == ClassificationStatus.INVESTIGATE
    assert result.confidence == pytest.approx(0.9)
    assert result.signal_count == 3
    assert result.risk_count == 0

    assert "CTR_DROP" in result.reasons
    assert "POSITION_DECLINE" in result.reasons
    assert "HIGH_VALUE_OPPORTUNITY" in result.reasons


def test_position_risk_reduces_position_decline_confidence():
    signals = [
        make_signal(
            SignalType.POSITION_DECLINE,
            confidence=0.8,
        ),
        make_signal(
            SignalType.POSITION_DECLINE_UNDER_VOLATILITY,
            confidence=0.8,
        ),
    ]

    result = classify_signals(signals)

    assert result.status == ClassificationStatus.INVESTIGATE
    assert result.confidence == pytest.approx(0.64)
    assert result.risk_count == 1