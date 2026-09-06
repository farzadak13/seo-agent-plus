from collections.abc import Sequence

from app.models.candidates import Candidate, CandidateStatus
from app.models.classification import ClassificationResult
from app.models.features import FeatureSet
from app.models.signals import Signal
from app.models.snapshots import SnapshotMetadata


def build_candidate(
    *,
    candidate_id: str,
    site_id: str,
    normalized_url: str,
    normalized_query: str,
    features: FeatureSet,
    signals: Sequence[Signal],
    classification: ClassificationResult,
    snapshot: SnapshotMetadata,
) -> Candidate:
    """
    Build a replayable candidate from deterministic evidence.

    The builder does not invent new evidence or perform any SEO action.
    """

    detected_signals = [
        signal
        for signal in signals
        if signal.detected
    ]

    signal_types = [
        signal.signal_type.value
        for signal in detected_signals
    ]

    risk_types = [
        signal.signal_type.value
        for signal in detected_signals
        if "_UNDER_" in signal.signal_type.value
    ]

    priority_score = _calculate_priority_score(
        features=features,
        signals=detected_signals,
        confidence=classification.confidence,
    )

    return Candidate(
        candidate_id=candidate_id,
        site_id=site_id,
        normalized_url=normalized_url,
        normalized_query=normalized_query,
        status=CandidateStatus(classification.status.value),
        confidence=classification.confidence,
        priority_score=priority_score,
        signal_types=signal_types,
        risk_types=risk_types,
        reasons=classification.reasons,
        snapshot=snapshot,
    )


def _calculate_priority_score(
    *,
    features: FeatureSet,
    signals: Sequence[Signal],
    confidence: float,
) -> float:
    """
    Deterministic MVP priority score.

    This is intentionally a simple hypothesis and should later be
    replaced or calibrated through replay/backtesting.
    """

    impression_factor = min(
        features.volume.current_impressions / 1000,
        10.0,
    )

    signal_factor = min(
        len(signals),
        5,
    )

    return (
        impression_factor
        * signal_factor
        * confidence
    )