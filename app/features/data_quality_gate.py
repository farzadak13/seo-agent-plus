from app.models.features import DataQualityMatrix


def passes_data_quality_gate(
    data_quality: DataQualityMatrix,
    *,
    min_query_completeness: float = 0.8,
    min_url_completeness: float = 0.8,
    min_current_window_completeness: float = 0.8,
    min_baseline_window_completeness: float = 0.8,
    min_reconciliation_completeness: float = 0.5,
) -> bool:
    """
    Determine whether the available evidence is sufficiently complete
    for downstream investigation.

    This is a quality gate only. It does not diagnose SEO issues
    and does not decide whether an action should be taken.
    """

    return (
        data_quality.query_level_completeness
        >= min_query_completeness
        and data_quality.url_level_completeness
        >= min_url_completeness
        and data_quality.current_window_completeness
        >= min_current_window_completeness
        and data_quality.baseline_window_completeness
        >= min_baseline_window_completeness
        and data_quality.reconciliation_completeness
        >= min_reconciliation_completeness
    )