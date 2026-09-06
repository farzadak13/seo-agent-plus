from app.engine.decision import run_decision_engine
from app.models.classification import (
    ClassificationResult,
    ClassificationStatus,
)
from app.models.pipeline import PipelineStatus
from app.models.replay import ReplayEvidence, ReplayResult


def _rejected_classification(
    *,
    reason: str,
) -> ClassificationResult:
    return ClassificationResult(
        status=ClassificationStatus.REJECT,
        confidence=0.0,
        reasons=[reason],
        signal_count=0,
        risk_count=0,
    )


def replay(
    *,
    evidence: ReplayEvidence,
    candidate_id: str,
) -> ReplayResult:
    """
    Replay the deterministic decision engine from persisted evidence.

    Replay consumes the canonical DecisionEvidence and does not
    perform ingestion, reconciliation, network access, database access,
    or other side effects.
    """

    if not evidence.reconciliation.is_valid:
        return ReplayResult(
            status=PipelineStatus.REJECTED_RECONCILIATION,
            features=None,
            signals=[],
            classification=_rejected_classification(
                reason="invalid_reconciliation",
            ),
            candidate=None,
            opportunity=None,
            strategy=None,
            action=None,
        )

    decision = run_decision_engine(
        evidence=evidence,
        candidate_id=candidate_id,
    )

    if decision.candidate is None:
        return ReplayResult(
            status=PipelineStatus.REJECTED_DATA_QUALITY,
            features=decision.features,
            signals=[],
            classification=decision.classification,
            candidate=None,
            opportunity=None,
            strategy=None,
            action=None,
        )

    return ReplayResult(
        status=PipelineStatus.COMPLETED,
        features=decision.features,
        signals=decision.signals,
        classification=decision.classification,
        candidate=decision.candidate,
        opportunity=decision.opportunity,
        strategy=decision.strategy,
        action=decision.action,
    )