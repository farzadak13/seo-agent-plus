from app.models.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)


def calculate_reconciliation_completeness(
    result: ReconciliationResult,
) -> float:
    """
    Convert reconciliation status into a deterministic quality score.

    MATCH   -> 1.0
    PARTIAL -> 0.5
    INVALID -> 0.0
    """

    impression_scores = {
        ReconciliationStatus.MATCH: 1.0,
        ReconciliationStatus.PARTIAL: 0.5,
        ReconciliationStatus.INVALID: 0.0,
    }

    click_scores = {
        ReconciliationStatus.MATCH: 1.0,
        ReconciliationStatus.PARTIAL: 0.5,
        ReconciliationStatus.INVALID: 0.0,
    }

    impression_score = impression_scores[result.impression_status]
    click_score = click_scores[result.click_status]

    return (impression_score + click_score) / 2