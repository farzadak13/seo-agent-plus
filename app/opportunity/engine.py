from app.models.classification import ClassificationStatus
from app.models.features import FeatureSet
from app.models.opportunities import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
)
from app.models.signals import Signal
from app.models.snapshots import SnapshotMetadata


def _detected_signal_types(
    signals: list[Signal],
) -> list[str]:
    return [
        signal.signal_type.value
        for signal in signals
        if signal.detected
    ]


def _risk_types(
    signals: list[Signal],
) -> list[str]:
    return [
        signal.signal_type.value
        for signal in signals
        if signal.detected and "_UNDER_" in signal.signal_type.value
    ]


def _resolve_opportunity_type(
    signal_types: list[str],
) -> OpportunityType:
    has_ctr = "ctr_drop" in signal_types
    has_position = "position_decline" in signal_types
    has_high_value = "high_value_opportunity" in signal_types

    if has_ctr and has_position:
        return OpportunityType.MIXED_RECOVERY

    if has_ctr:
        return OpportunityType.CTR_RECOVERY

    if has_position:
        return OpportunityType.POSITION_RECOVERY

    if has_high_value:
        return OpportunityType.VISIBILITY_GROWTH

    return OpportunityType.VISIBILITY_GROWTH


def _impact_score(
    features: FeatureSet,
) -> float:
    return min(
        features.volume.current_impressions / 5000.0,
        1.0,
    )


def _business_value_score(
    features: FeatureSet,
) -> float:
    return min(
        features.volume.current_clicks / 500.0,
        1.0,
    )


def _traffic_potential_score(
    features: FeatureSet,
) -> float:
    position = features.position.current_median

    if position <= 3:
        return 0.35

    if position <= 10:
        return 1.0

    if position <= 20:
        return 0.7

    return 0.3


def _effort_score(
    opportunity_type: OpportunityType,
) -> float:
    if opportunity_type == OpportunityType.CTR_RECOVERY:
        return 0.3

    if opportunity_type == OpportunityType.POSITION_RECOVERY:
        return 0.6

    if opportunity_type == OpportunityType.VISIBILITY_GROWTH:
        return 0.4

    return 0.7


def _risk_score(
    risk_types: list[str],
) -> float:
    return min(
        len(risk_types) * 0.25,
        1.0,
    )


def _priority_score(
    *,
    impact_score: float,
    confidence_score: float,
    business_value_score: float,
    traffic_potential_score: float,
    effort_score: float,
    risk_score: float,
) -> float:
    score = (
        0.30 * impact_score
        + 0.20 * confidence_score
        + 0.15 * business_value_score
        + 0.15 * traffic_potential_score
        + 0.10 * (1.0 - effort_score)
        + 0.10 * (1.0 - risk_score)
    )

    return round(
        min(max(score, 0.0), 1.0),
        6,
    )


def build_opportunity(
    *,
    opportunity_id: str,
    candidate_id: str,
    site_id: str,
    normalized_url: str,
    normalized_query: str,
    features: FeatureSet,
    signals: list[Signal],
    classification: ClassificationStatus,
    confidence: float,
    snapshot: SnapshotMetadata,
) -> Opportunity | None:
    signal_types = _detected_signal_types(signals)
    risk_types = _risk_types(signals)

    if classification != ClassificationStatus.INVESTIGATE:
        return None

    if not signal_types:
        return None

    opportunity_type = _resolve_opportunity_type(
        signal_types,
    )

    impact_score = _impact_score(features)
    business_value_score = _business_value_score(features)
    traffic_potential_score = _traffic_potential_score(features)
    effort_score = _effort_score(opportunity_type)
    risk_score = _risk_score(risk_types)

    priority_score = _priority_score(
        impact_score=impact_score,
        confidence_score=confidence,
        business_value_score=business_value_score,
        traffic_potential_score=traffic_potential_score,
        effort_score=effort_score,
        risk_score=risk_score,
    )

    reasons = [
        f"signals={','.join(signal_types)}",
        f"priority_score={priority_score}",
    ]

    evidence = {
        "current_impressions": features.volume.current_impressions,
        "current_clicks": features.volume.current_clicks,
        "current_ctr": features.ctr.current_ctr,
        "ctr_delta": features.ctr.absolute_delta,
        "current_position": features.position.current_median,
        "position_delta": features.position.delta,
        "query_visibility_share": (
            features.visibility.query_visibility_share
        ),
    }

    return Opportunity(
        opportunity_id=opportunity_id,
        candidate_id=candidate_id,
        site_id=site_id,
        normalized_url=normalized_url,
        normalized_query=normalized_query,
        opportunity_type=opportunity_type,
        status=OpportunityStatus.QUALIFIED,
        impact_score=impact_score,
        confidence_score=confidence,
        business_value_score=business_value_score,
        traffic_potential_score=traffic_potential_score,
        effort_score=effort_score,
        risk_score=risk_score,
        priority_score=priority_score,
        signal_types=signal_types,
        risk_types=risk_types,
        reasons=reasons,
        evidence=evidence,
        snapshot=snapshot,
    )