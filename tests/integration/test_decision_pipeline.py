from datetime import date, datetime, timezone

from app.classifier.candidate_builder import build_candidate
from app.classifier.queue import build_investigation_queue
from app.classifier.rules import classify_signals
from app.detectors.ctr import detect_ctr_drop
from app.detectors.high_value import detect_high_value_opportunity
from app.detectors.position import detect_position_decline
from app.detectors.risk import detect_signal_risks
from app.detectors.volatility import detect_position_volatility
from app.features.extractor import extract_feature_set
from app.ingestion.gsc import ingest_gsc_response
from app.ingestion.reconciliation import reconcile_ingestion
from app.models.candidates import CandidateStatus
from app.models.classification import ClassificationStatus
from app.models.gsc import RawGSCResponse
from app.models.snapshots import SnapshotMetadata
from app.features.reconciliation import (
    calculate_reconciliation_completeness,
)


def make_response(
    *,
    response_id: str,
    day: date,
    rows: list[dict],
) -> RawGSCResponse:
    return RawGSCResponse(
        response_id=response_id,
        site_id="site-1",
        fetch_date=day,
        raw_payload={
            "rows": rows,
        },
        created_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )


def detect_all_signals(features):
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


def test_full_decision_pipeline_produces_investigatable_candidate():
    baseline_response = make_response(
        response_id="baseline-001",
        day=date(2026, 8, 25),
        rows=[
            {
                "keys": [
                    "https://example.com/product",
                    "کفش مردانه",
                ],
                "impressions": 1000,
                "clicks": 60,
                "position": 4.0,
            },
            {
                "keys": [
                    "https://example.com/product",
                    "کفش ورزشی",
                ],
                "impressions": 500,
                "clicks": 30,
                "position": 4.5,
            },
        ],
    )

    current_response = make_response(
        response_id="current-001",
        day=date(2026, 9, 5),
        rows=[
            {
                "keys": [
                    "https://example.com/product",
                    "کفش مردانه",
                ],
                "impressions": 1000,
                "clicks": 30,
                "position": 7.0,
            },
            {
                "keys": [
                    "https://example.com/product",
                    "کفش ورزشی",
                ],
                "impressions": 500,
                "clicks": 15,
                "position": 7.5,
            },
        ],
    )

    baseline_ingestion = ingest_gsc_response(
        response=baseline_response,
        start_date=date(2026, 8, 25),
        end_date=date(2026, 8, 25),
    )

    current_ingestion = ingest_gsc_response(
        response=current_response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    baseline_reconciliation = reconcile_ingestion(
        baseline_ingestion,
    )

    current_reconciliation = reconcile_ingestion(
        current_ingestion,
    )

    baseline_features = extract_feature_set(
        baseline_observations=baseline_ingestion.observations,
        current_observations=baseline_ingestion.observations,
        baseline_url_metrics=baseline_ingestion.url_daily_metrics,
        current_url_metrics=baseline_ingestion.url_daily_metrics,
        reconciliation_completeness=calculate_reconciliation_completeness(
            baseline_reconciliation,
        ),
    )

    current_features = extract_feature_set(
        baseline_observations=baseline_ingestion.observations,
        current_observations=current_ingestion.observations,
        baseline_url_metrics=baseline_ingestion.url_daily_metrics,
        current_url_metrics=current_ingestion.url_daily_metrics,
        reconciliation_completeness=calculate_reconciliation_completeness(
            current_reconciliation,
        ),
    )

    assert baseline_features.data_quality.reconciliation_completeness == 1.0
    assert current_features.data_quality.reconciliation_completeness == 1.0

    signals = detect_all_signals(current_features)

    assert any(
        signal.detected
        for signal in signals
    )

    classification = classify_signals(
        signals,
        data_quality=current_features.data_quality,
    )

    assert classification.status == ClassificationStatus.INVESTIGATE
    assert classification.confidence > 0

    snapshot = SnapshotMetadata(
        snapshot_id="snapshot-e2e-001",
        data_snapshot_id="data-e2e-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )

    candidate = build_candidate(
        candidate_id="candidate-e2e-001",
        site_id="site-1",
        normalized_url="https://example.com/product",
        normalized_query="کفش مردانه",
        features=current_features,
        signals=signals,
        classification=classification,
        snapshot=snapshot,
    )

    assert candidate.status == CandidateStatus.INVESTIGATE
    assert candidate.confidence > 0
    assert candidate.snapshot.snapshot_id == "snapshot-e2e-001"

    queue = build_investigation_queue(
        [candidate],
    )

    assert len(queue) == 1
    assert queue[0].candidate_id == "candidate-e2e-001"


def test_full_pipeline_is_deterministic():
    response = make_response(
        response_id="deterministic-001",
        day=date(2026, 9, 5),
        rows=[
            {
                "keys": [
                    "https://example.com/product",
                    "کفش مردانه",
                ],
                "impressions": 1000,
                "clicks": 30,
                "position": 7.0,
            },
            {
                "keys": [
                    "https://example.com/product",
                    "کفش ورزشی",
                ],
                "impressions": 500,
                "clicks": 15,
                "position": 7.5,
            },
        ],
    )

    ingestion_1 = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    ingestion_2 = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert ingestion_1.model_dump() == ingestion_2.model_dump()

    reconciliation_1 = reconcile_ingestion(ingestion_1)
    reconciliation_2 = reconcile_ingestion(ingestion_2)

    assert reconciliation_1.model_dump() == reconciliation_2.model_dump()

    features_1 = extract_feature_set(
        baseline_observations=ingestion_1.observations,
        current_observations=ingestion_1.observations,
        baseline_url_metrics=ingestion_1.url_daily_metrics,
        current_url_metrics=ingestion_1.url_daily_metrics,
    )

    features_2 = extract_feature_set(
        baseline_observations=ingestion_2.observations,
        current_observations=ingestion_2.observations,
        baseline_url_metrics=ingestion_2.url_daily_metrics,
        current_url_metrics=ingestion_2.url_daily_metrics,
    )

    assert features_1.model_dump() == features_2.model_dump()


def test_pipeline_rejects_low_quality_evidence_before_queue():
    response = make_response(
        response_id="low-quality-001",
        day=date(2026, 9, 5),
        rows=[
            {
                "keys": [
                    "https://example.com/product",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 3,
                "position": 10.0,
            },
        ],
    )

    ingestion = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
    )

    features = extract_feature_set(
        baseline_observations=ingestion.observations,
        current_observations=ingestion.observations,
        baseline_url_metrics=ingestion.url_daily_metrics,
        current_url_metrics=ingestion.url_daily_metrics,
        reconciliation_completeness=1.0,
    )

    signals = detect_all_signals(features)

    classification = classify_signals(
        signals,
        data_quality=features.data_quality.model_copy(
            update={
                "current_window_completeness": 0.2,
                "query_level_completeness": 0.2,
            }
        ),
    )

    assert classification.status == ClassificationStatus.REJECT
    assert classification.confidence == 0.0