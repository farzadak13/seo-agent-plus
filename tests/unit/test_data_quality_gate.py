import pytest

from app.features.data_quality_gate import passes_data_quality_gate
from app.models.features import DataQualityMatrix


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


def test_complete_data_passes_gate():
    quality = make_quality()

    assert passes_data_quality_gate(quality) is True


@pytest.mark.parametrize(
    "field",
    [
        "query",
        "url",
        "current",
        "baseline",
    ],
)
def test_insufficient_window_or_level_completeness_fails_gate(
    field: str,
):
    values = {
        "query": 1.0,
        "url": 1.0,
        "current": 1.0,
        "baseline": 1.0,
    }

    values[field] = 0.79

    quality = make_quality(**values)

    assert passes_data_quality_gate(quality) is False


def test_partial_reconciliation_at_default_threshold_passes():
    quality = make_quality(
        reconciliation=0.5,
    )

    assert passes_data_quality_gate(quality) is True


def test_insufficient_reconciliation_fails_gate():
    quality = make_quality(
        reconciliation=0.49,
    )

    assert passes_data_quality_gate(quality) is False


def test_custom_thresholds_are_supported():
    quality = make_quality(
        query=0.85,
        url=0.85,
        current=0.85,
        baseline=0.85,
        reconciliation=0.75,
    )

    assert passes_data_quality_gate(
        quality,
        min_query_completeness=0.85,
        min_url_completeness=0.85,
        min_current_window_completeness=0.85,
        min_baseline_window_completeness=0.85,
        min_reconciliation_completeness=0.75,
    ) is True


def test_custom_threshold_can_reject_same_data():
    quality = make_quality(
        query=0.85,
        url=0.85,
        current=0.85,
        baseline=0.85,
        reconciliation=0.75,
    )

    assert passes_data_quality_gate(
        quality,
        min_query_completeness=0.9,
    ) is False


def test_exact_threshold_passes():
    quality = make_quality(
        query=0.8,
        url=0.8,
        current=0.8,
        baseline=0.8,
        reconciliation=0.5,
    )

    assert passes_data_quality_gate(quality) is True


def test_zero_quality_fails_gate():
    quality = make_quality(
        query=0.0,
        url=0.0,
        current=0.0,
        baseline=0.0,
        reconciliation=0.0,
    )

    assert passes_data_quality_gate(quality) is False