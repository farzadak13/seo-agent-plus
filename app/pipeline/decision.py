from collections.abc import Iterable, Sequence
from datetime import date as Date

from app.engine.decision import run_decision_engine
from app.ingestion.gsc import ingest_gsc_response
from app.ingestion.reconciliation import reconcile_ingestion
from app.models.classification import (
    ClassificationResult,
    ClassificationStatus,
)
from app.models.evidence import DecisionEvidence
from app.models.observations import DataStatus, DailyObservation
from app.models.pipeline import (
    DecisionPipelineResult,
    PipelineStatus,
)
from app.models.gsc import RawGSCResponse
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric


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


def _build_decision_evidence(
    *,
    site_id: str,
    normalized_url: str,
    normalized_query: str,
    baseline_observations: Sequence[DailyObservation],
    current_observations: Sequence[DailyObservation],
    baseline_url_metrics: Sequence[URLDailyMetric],
    current_url_metrics: Sequence[URLDailyMetric],
    baseline_daily_status: list[tuple[Date, DataStatus]],
    current_daily_status: list[tuple[Date, DataStatus]],
    reconciliation,
    snapshot: SnapshotMetadata,
) -> DecisionEvidence:
    return DecisionEvidence(
        site_id=site_id,
        normalized_url=normalized_url,
        normalized_query=normalized_query,
        baseline_observations=list(baseline_observations),
        current_observations=list(current_observations),
        baseline_url_metrics=list(baseline_url_metrics),
        current_url_metrics=list(current_url_metrics),
        baseline_daily_status=baseline_daily_status,
        current_daily_status=current_daily_status,
        reconciliation=reconciliation,
        snapshot=snapshot,
    )


def run_decision_pipeline(
    *,
    response: RawGSCResponse,
    start_date: Date,
    end_date: Date,
    baseline_observations: Sequence[DailyObservation],
    baseline_url_metrics: Sequence[URLDailyMetric],
    snapshot: SnapshotMetadata,
    candidate_id: str,
    normalized_url: str,
    normalized_query: str,
    expected_zero_dates: Iterable[Date] | None = None,
    current_url_metrics: Sequence[URLDailyMetric] | None = None,
) -> DecisionPipelineResult:
    ingestion = ingest_gsc_response(
        response=response,
        start_date=start_date,
        end_date=end_date,
        expected_zero_dates=expected_zero_dates,
    )

    effective_current_url_metrics = (
        current_url_metrics
        if current_url_metrics is not None
        else ingestion.url_daily_metrics
    )

    reconciliation = reconcile_ingestion(
        ingestion,
        url_daily_metrics=effective_current_url_metrics,
    )

    if not reconciliation.is_valid:
        return DecisionPipelineResult(
            status=PipelineStatus.REJECTED_RECONCILIATION,
            ingestion=ingestion,
            reconciliation=reconciliation,
            evidence=None,
            features=None,
            signals=[],
            classification=_rejected_classification(
                reason="invalid_reconciliation",
            ),
            candidate=None,
            opportunity=None,
            strategy=None,
            action=None,
            investigation_queue=[],
        )

    baseline_daily_status = [
        (
            observation.date,
            observation.data_status,
        )
        for observation in baseline_observations
    ]

    current_daily_status = list(
        ingestion.daily_status,
    )

    evidence = _build_decision_evidence(
        site_id=response.site_id,
        normalized_url=normalized_url,
        normalized_query=normalized_query,
        baseline_observations=baseline_observations,
        current_observations=ingestion.observations,
        baseline_url_metrics=baseline_url_metrics,
        current_url_metrics=effective_current_url_metrics,
        baseline_daily_status=baseline_daily_status,
        current_daily_status=current_daily_status,
        reconciliation=reconciliation,
        snapshot=snapshot,
    )

    decision = run_decision_engine(
        evidence=evidence,
        candidate_id=candidate_id,
    )

    if decision.candidate is None:
        return DecisionPipelineResult(
            status=PipelineStatus.REJECTED_DATA_QUALITY,
            ingestion=ingestion,
            reconciliation=reconciliation,
            evidence=evidence,
            features=decision.features,
            signals=[],
            classification=decision.classification,
            candidate=None,
            opportunity=None,
            strategy=None,
            action=None,
            investigation_queue=[],
        )

    return DecisionPipelineResult(
        status=PipelineStatus.COMPLETED,
        ingestion=ingestion,
        reconciliation=reconciliation,
        evidence=evidence,
        features=decision.features,
        signals=decision.signals,
        classification=decision.classification,
        candidate=decision.candidate,
        opportunity=decision.opportunity,
        strategy=decision.strategy,
        action=decision.action,
        investigation_queue=decision.investigation_queue,
    )