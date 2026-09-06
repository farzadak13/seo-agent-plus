from app.classifier.rules import classify_signals
from app.models.classification import ClassificationStatus
from app.models.features import DataQualityMatrix
from app.models.signals import Signal, SignalSeverity, SignalType


def make_quality(
    *,
    query: float = 1.0,
    url: float = 1.0,
    current: float = 1.0,
    baseline: float = 1.0,
    reconciliation: float = 1.0,
) -> DataQualityMatrix:
    return DataQualityMatrix(
        url_level_completeness=url,
        query_level_completeness=query,
        current_window_completeness=current,
        baseline_window_completeness=baseline,
        reconciliation_completeness=reconciliation,
    )


def make_ctr_drop_signal() -> Signal:
    return Signal(
        signal_type=SignalType.CTR_DROP,
        detected=True,
        severity=SignalSeverity.WARNING,
        confidence=0.9,
        evidence={
            "absolute_delta": -0.05,
        },
    )


def test_good_quality_allows_investigation():
    result = classify_signals(
        [make_ctr_drop_signal()],
        data_quality=make_quality(),
    )

    assert result.status == ClassificationStatus.INVESTIGATE
    assert result.confidence == 0.9


def test_insufficient_query_quality_rejects():
    result = classify_signals(
        [make_ctr_drop_signal()],
        data_quality=make_quality(
            query=0.79,
        ),
    )

    assert result.status == ClassificationStatus.REJECT
    assert result.confidence == 0.0
    assert "insufficient_data_quality" in result.reasons


def test_insufficient_url_quality_rejects():
    result = classify_signals(
        [make_ctr_drop_signal()],
        data_quality=make_quality(
            url=0.79,
        ),
    )

    assert result.status == ClassificationStatus.REJECT


def test_insufficient_current_window_quality_rejects():
    result = classify_signals(
        [make_ctr_drop_signal()],
        data_quality=make_quality(
            current=0.79,
        ),
    )

    assert result.status == ClassificationStatus.REJECT


def test_insufficient_baseline_window_quality_rejects():
    result = classify_signals(
        [make_ctr_drop_signal()],
        data_quality=make_quality(
            baseline=0.79,
        ),
    )

    assert result.status == ClassificationStatus.REJECT


def test_insufficient_reconciliation_quality_rejects():
    result = classify_signals(
        [make_ctr_drop_signal()],
        data_quality=make_quality(
            reconciliation=0.49,
        ),
    )

    assert result.status == ClassificationStatus.REJECT


def test_partial_reconciliation_at_threshold_allows_investigation():
    result = classify_signals(
        [make_ctr_drop_signal()],
        data_quality=make_quality(
            reconciliation=0.5,
        ),
    )

    assert result.status == ClassificationStatus.INVESTIGATE


def test_quality_gate_is_optional_for_backward_compatibility():
    result = classify_signals(
        [make_ctr_drop_signal()],
    )

    assert result.status == ClassificationStatus.INVESTIGATE