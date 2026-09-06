from datetime import date, datetime, timezone

from app.models.classification import ClassificationStatus
from app.models.gsc import RawGSCResponse
from app.models.observations import DataStatus, DailyObservation
from app.models.pipeline import PipelineStatus
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric
from app.pipeline.decision import run_decision_pipeline


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-failure-001",
        data_snapshot_id="data-failure-001",
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


def make_baseline_observations() -> list[DailyObservation]:
    return [
        DailyObservation(
            site_id="site-1",
            normalized_url="https://example.com/page",
            normalized_query="کفش مردانه",
            date=date(2026, 8, 25),
            impressions=1000,
            clicks=60,
            avg_position=4.0,
            data_status=DataStatus.OBSERVED,
        ),
    ]


def make_valid_response() -> RawGSCResponse:
    return RawGSCResponse(
        response_id="failure-test-001",
        site_id="site-1",
        fetch_date=date(2026, 9, 5),
        raw_payload={
            "rows": [
                {
                    "keys": [
                        "https://example.com/page",
                        "کفش مردانه",
                    ],
                    "impressions": 1000,
                    "clicks": 30,
                    "position": 7.0,
                }
            ]
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


def make_invalid_reconciliation_response() -> RawGSCResponse:
    return RawGSCResponse(
        response_id="failure-test-invalid-reconciliation",
        site_id="site-1",
        fetch_date=date(2026, 9, 5),
        raw_payload={
            "rows": [
                {
                    "keys": [
                        "https://example.com/page",
                        "کفش مردانه",
                    ],
                    "impressions": 150,
                    "clicks": 20,
                    "position": 7.0,
                }
            ]
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


def test_invalid_reconciliation_stops_pipeline():
    result = run_decision_pipeline(
        response=make_invalid_reconciliation_response(),
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[
            URLDailyMetric(
                site_id="site-1",
                normalized_url="https://example.com/page",
                date=date(2026, 9, 5),
                total_impressions=100,
                total_clicks=10,
            )
        ],
        current_url_metrics=[
            URLDailyMetric(
                site_id="site-1",
                normalized_url="https://example.com/page",
                date=date(2026, 9, 5),
                total_impressions=100,
                total_clicks=10,
            )
        ],
        snapshot=make_snapshot(),
        candidate_id="candidate-invalid-reconciliation",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert result.status == PipelineStatus.REJECTED_RECONCILIATION
    assert result.reconciliation.is_valid is False

    assert result.evidence is None
    assert result.features is None
    assert result.signals == []
    assert result.candidate is None
    assert result.opportunity is None
    assert result.strategy is None
    assert result.action is None
    assert result.investigation_queue == []

    assert result.classification.status == ClassificationStatus.REJECT
    assert result.classification.reasons == [
        "invalid_reconciliation",
    ]


def test_insufficient_data_quality_stops_before_signal_detection():
    result = run_decision_pipeline(
        response=make_valid_response(),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-low-quality",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert result.status == PipelineStatus.REJECTED_DATA_QUALITY

    assert result.evidence is not None
    assert result.features is not None
    assert result.features.data_quality.current_window_completeness < 0.8

    assert result.signals == []
    assert result.candidate is None
    assert result.opportunity is None
    assert result.strategy is None
    assert result.action is None
    assert result.investigation_queue == []

    assert result.classification.status == ClassificationStatus.REJECT
    assert result.classification.reasons == [
        "insufficient_data_quality",
    ]


def test_valid_pipeline_completes_normally():
    result = run_decision_pipeline(
        response=make_valid_response(),
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-valid",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert result.status == PipelineStatus.COMPLETED

    assert result.evidence is not None
    assert result.features is not None
    assert result.candidate is not None
    assert result.candidate.candidate_id == "candidate-valid"

    assert result.opportunity is not None
    assert result.strategy is not None
    assert result.action is not None

    assert result.action.strategy_id == result.strategy.strategy_id
    assert result.action.opportunity_id == result.opportunity.opportunity_id


def test_rejected_pipeline_has_no_investigation_queue():
    result = run_decision_pipeline(
        response=make_valid_response(),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-rejected",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert result.status == PipelineStatus.REJECTED_DATA_QUALITY
    assert result.investigation_queue == []

    assert result.opportunity is None
    assert result.strategy is None
    assert result.action is None


def test_failure_result_is_deterministic():
    first = run_decision_pipeline(
        response=make_valid_response(),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-deterministic",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    second = run_decision_pipeline(
        response=make_valid_response(),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-deterministic",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert first.model_dump() == second.model_dump()