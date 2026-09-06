from app.action.planner import build_action
from app.classifier.candidate_builder import build_candidate
from app.classifier.queue import build_investigation_queue
from app.classifier.rules import classify_signals
from app.detectors.ctr import detect_ctr_drop
from app.detectors.high_value import detect_high_value_opportunity
from app.detectors.position import detect_position_decline
from app.detectors.risk import detect_signal_risks
from app.detectors.volatility import detect_position_volatility
from app.features.data_quality import calculate_window_completeness
from app.features.data_quality_gate import passes_data_quality_gate
from app.features.extractor import extract_feature_set
from app.features.reconciliation import (
    calculate_reconciliation_completeness,
)
from app.models.decision import DecisionEngineResult
from app.models.evidence import DecisionEvidence
from app.models.opportunities import Opportunity
from app.models.strategies import Strategy
from app.opportunity.engine import build_opportunity
from app.strategy.engine import build_strategy


def _detect_all_signals(features):
    signals = [
        detect_ctr_drop(features),
        detect_position_decline(features),
        detect_high_value_opportunity(features),
        detect_position_volatility(features),
    ]

    risks = detect_signal_risks(signals)

    return [
        *signals,
        *risks,
    ]


def run_decision_engine(
    *,
    evidence: DecisionEvidence,
    candidate_id: str,
) -> DecisionEngineResult:
    current_window_completeness = calculate_window_completeness(
        evidence.current_daily_status,
    )

    baseline_window_completeness = calculate_window_completeness(
        evidence.baseline_daily_status,
    )

    reconciliation_completeness = (
        calculate_reconciliation_completeness(
            evidence.reconciliation,
        )
    )

    features = extract_feature_set(
        baseline_observations=evidence.baseline_observations,
        current_observations=evidence.current_observations,
        baseline_url_metrics=evidence.baseline_url_metrics,
        current_url_metrics=evidence.current_url_metrics,
        reconciliation_completeness=reconciliation_completeness,
        current_window_completeness=current_window_completeness,
        baseline_window_completeness=baseline_window_completeness,
    )

    if not passes_data_quality_gate(
        features.data_quality,
    ):
        classification = classify_signals(
            [],
            data_quality=features.data_quality,
        )

        return DecisionEngineResult(
            features=features,
            signals=[],
            classification=classification,
            candidate=None,
            opportunity=None,
            strategy=None,
            action=None,
            investigation_queue=[],
        )

    signals = _detect_all_signals(
        features,
    )

    classification = classify_signals(
        signals,
        data_quality=features.data_quality,
    )

    candidate = build_candidate(
        candidate_id=candidate_id,
        site_id=evidence.site_id,
        normalized_url=evidence.normalized_url,
        normalized_query=evidence.normalized_query,
        features=features,
        signals=signals,
        classification=classification,
        snapshot=evidence.snapshot,
    )

    opportunity: Opportunity | None = build_opportunity(
        opportunity_id=f"opportunity-{candidate_id}",
        candidate_id=candidate_id,
        site_id=evidence.site_id,
        normalized_url=evidence.normalized_url,
        normalized_query=evidence.normalized_query,
        features=features,
        signals=signals,
        classification=classification.status,
        confidence=classification.confidence,
        snapshot=evidence.snapshot,
    )

    strategy: Strategy | None = (
        build_strategy(
            strategy_id=f"strategy-{candidate_id}",
            opportunity=opportunity,
        )
        if opportunity is not None
        else None
    )

    action = (
        build_action(
            action_id=f"action-{candidate_id}",
            strategy=strategy,
        )
        if strategy is not None
        else None
    )

    investigation_queue = build_investigation_queue(
        [candidate],
    )

    return DecisionEngineResult(
        features=features,
        signals=signals,
        classification=classification,
        candidate=candidate,
        opportunity=opportunity,
        strategy=strategy,
        action=action,
        investigation_queue=investigation_queue,
    )