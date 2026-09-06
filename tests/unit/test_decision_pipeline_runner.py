from datetime import date, datetime, timezone

from app.engine.decision import run_decision_engine
from app.models.gsc import RawGSCResponse
from app.models.observations import DataStatus, DailyObservation
from app.models.snapshots import SnapshotMetadata
from app.pipeline.decision import run_decision_pipeline


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-pipeline-001",
        data_snapshot_id="data-pipeline-001",
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


def make_response() -> RawGSCResponse:
    return RawGSCResponse(
        response_id="pipeline-001",
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


def test_pipeline_returns_complete_result():
    result = run_decision_pipeline(
        response=make_response(),
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-pipeline-001",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert result.status.value == "completed"

    assert result.ingestion.response_id == "pipeline-001"
    assert result.reconciliation.is_valid is True

    assert result.evidence is not None
    assert result.evidence.site_id == "site-1"
    assert result.evidence.normalized_url == (
        "https://example.com/page"
    )
    assert result.evidence.normalized_query == "کفش مردانه"

    assert result.evidence.reconciliation == result.reconciliation

    assert (
        result.evidence.current_observations
        == result.ingestion.observations
    )

    assert (
        result.evidence.current_url_metrics
        == result.ingestion.url_daily_metrics
    )

    assert result.evidence.snapshot == make_snapshot()

    assert result.features is not None
    assert result.features.volume.current_impressions == 1000

    assert len(result.signals) > 0
    assert result.classification.confidence >= 0

    assert result.candidate is not None
    assert (
        result.candidate.candidate_id
        == "candidate-pipeline-001"
    )

    assert result.opportunity is not None
    assert result.strategy is not None
    assert result.action is not None

    assert (
        result.strategy.opportunity_id
        == result.opportunity.opportunity_id
    )

    assert (
        result.action.strategy_id
        == result.strategy.strategy_id
    )

    assert len(result.investigation_queue) == 1
    assert (
        result.investigation_queue[0].candidate_id
        == "candidate-pipeline-001"
    )

    engine_result = run_decision_engine(
        evidence=result.evidence,
        candidate_id="candidate-pipeline-001",
    )

    assert result.features == engine_result.features
    assert result.signals == engine_result.signals
    assert result.classification == engine_result.classification
    assert result.candidate == engine_result.candidate
    assert result.opportunity == engine_result.opportunity
    assert result.strategy == engine_result.strategy
    assert result.action == engine_result.action
    assert (
        result.investigation_queue
        == engine_result.investigation_queue
    )


def test_pipeline_is_deterministic():
    first = run_decision_pipeline(
        response=make_response(),
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-pipeline-001",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    second = run_decision_pipeline(
        response=make_response(),
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-pipeline-001",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert first.status == second.status
    assert first.model_dump() == second.model_dump()

    assert first.evidence is not None
    assert second.evidence is not None
    assert first.evidence == second.evidence

    assert first.opportunity == second.opportunity
    assert first.strategy == second.strategy
    assert first.action == second.action

    assert first.investigation_queue == second.investigation_queue


def test_pipeline_preserves_snapshot():
    result = run_decision_pipeline(
        response=make_response(),
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-pipeline-001",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert result.candidate is not None
    assert result.opportunity is not None
    assert result.strategy is not None
    assert result.action is not None

    assert result.candidate.snapshot == make_snapshot()
    assert result.opportunity.snapshot == make_snapshot()
    assert result.strategy.snapshot == make_snapshot()
    assert result.action.snapshot == make_snapshot()

    assert result.evidence is not None
    assert result.evidence.snapshot == make_snapshot()


def test_pipeline_queue_contains_only_current_candidate():
    result = run_decision_pipeline(
        response=make_response(),
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        baseline_observations=make_baseline_observations(),
        baseline_url_metrics=[],
        snapshot=make_snapshot(),
        candidate_id="candidate-queue-001",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
    )

    assert [
        candidate.candidate_id
        for candidate in result.investigation_queue
    ] == [
        "candidate-queue-001",
    ]