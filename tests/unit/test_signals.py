import pytest
from pydantic import ValidationError

from app.models.signals import (
    Signal,
    SignalSeverity,
    SignalType,
)


def test_valid_signal():
    signal = Signal(
        signal_type=SignalType.CTR_DROP,
        detected=True,
        severity=SignalSeverity.WARNING,
        confidence=0.85,
        evidence={
            "baseline_ctr": 0.10,
            "current_ctr": 0.05,
            "absolute_delta": -0.05,
        },
    )

    assert signal.signal_type == SignalType.CTR_DROP
    assert signal.detected is True
    assert signal.severity == SignalSeverity.WARNING
    assert signal.confidence == 0.85
    assert signal.evidence["baseline_ctr"] == 0.10


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_must_be_between_zero_and_one(confidence):
    with pytest.raises(ValidationError):
        Signal(
            signal_type=SignalType.CTR_DROP,
            detected=True,
            confidence=confidence,
        )


def test_default_severity_is_info():
    signal = Signal(
        signal_type=SignalType.CTR_DROP,
        detected=False,
        confidence=1.0,
    )

    assert signal.severity == SignalSeverity.INFO


def test_default_evidence_is_empty():
    signal = Signal(
        signal_type=SignalType.CTR_DROP,
        detected=False,
        confidence=1.0,
    )

    assert signal.evidence == {}


def test_unknown_signal_type_is_rejected():
    with pytest.raises(ValidationError):
        Signal(
            signal_type="UNKNOWN_SIGNAL",
            detected=True,
            confidence=0.8,
        )


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        Signal(
            signal_type=SignalType.CTR_DROP,
            detected=True,
            confidence=0.8,
            unexpected_field="bad",
        )