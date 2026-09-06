from app.detectors.risk import detect_signal_risks
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


def test_ctr_drop_under_volatility_creates_risk_signal():
    signals = [
        make_signal(SignalType.CTR_DROP, confidence=0.9),
        make_signal(SignalType.VOLATILITY, confidence=0.95),
    ]

    risks = detect_signal_risks(signals)

    assert len(risks) == 1

    risk = risks[0]

    assert (
        risk.signal_type
        == SignalType.CTR_DROP_UNDER_VOLATILITY
    )
    assert risk.detected is True
    assert risk.severity == SignalSeverity.WARNING
    assert risk.confidence == 0.8
    assert (
        risk.evidence["reason"]
        == "ctr_drop_occurs_during_high_volatility"
    )


def test_position_decline_under_volatility_creates_risk_signal():
    signals = [
        make_signal(SignalType.POSITION_DECLINE, confidence=0.7),
        make_signal(SignalType.VOLATILITY, confidence=0.9),
    ]

    risks = detect_signal_risks(signals)

    assert len(risks) == 1

    risk = risks[0]

    assert (
        risk.signal_type
        == SignalType.POSITION_DECLINE_UNDER_VOLATILITY
    )
    assert risk.detected is True
    assert risk.confidence == 0.7


def test_both_declines_under_volatility_create_two_risks():
    signals = [
        make_signal(SignalType.CTR_DROP),
        make_signal(SignalType.POSITION_DECLINE),
        make_signal(SignalType.VOLATILITY),
    ]

    risks = detect_signal_risks(signals)

    assert len(risks) == 2

    signal_types = {risk.signal_type for risk in risks}

    assert SignalType.CTR_DROP_UNDER_VOLATILITY in signal_types
    assert (
        SignalType.POSITION_DECLINE_UNDER_VOLATILITY
        in signal_types
    )


def test_volatility_without_decline_creates_no_risk():
    signals = [
        make_signal(SignalType.VOLATILITY),
    ]

    risks = detect_signal_risks(signals)

    assert risks == []


def test_ctr_drop_without_volatility_creates_no_risk():
    signals = [
        make_signal(SignalType.CTR_DROP),
    ]

    risks = detect_signal_risks(signals)

    assert risks == []


def test_confidence_is_capped_at_point_eight():
    signals = [
        make_signal(SignalType.CTR_DROP, confidence=1.0),
        make_signal(SignalType.VOLATILITY, confidence=1.0),
    ]

    risks = detect_signal_risks(signals)

    assert risks[0].confidence == 0.8


def test_original_signals_are_not_modified():
    ctr = make_signal(
        SignalType.CTR_DROP,
        confidence=0.9,
    )
    volatility = make_signal(
        SignalType.VOLATILITY,
        confidence=0.9,
    )

    original = [ctr, volatility]

    detect_signal_risks(original)

    assert ctr.confidence == 0.9
    assert volatility.confidence == 0.9