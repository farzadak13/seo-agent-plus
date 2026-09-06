from datetime import date, datetime, timezone

from app.engine.decision import run_decision_engine
from app.models.observations import (
    DataStatus,
    DailyObservation,
)
from app.models.pipeline import PipelineStatus
from app.models.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)
from app.models.replay import ReplayEvidence
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric
from app.replay.engine import replay


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="replay-snapshot-001",
        data_snapshot_id="replay-data-001",
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


def make_reconciliation() -> ReconciliationResult:
    return ReconciliationResult(
        impression_status=ReconciliationStatus.MATCH,
        click_status=ReconciliationStatus.MATCH,
        query_impressions=1000,
        url_impressions=1000,
        query_clicks=30,
        url_clicks=30,
        impression_gap=0,
        click_gap=0,
        is_valid=True,
        is_partial=False,
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


def make_current_observations() -> list[DailyObservation]:
    return [
        DailyObservation(
            site_id="site-1",
            normalized_url="https://example.com/page",
            normalized_query="کفش مردانه",
            date=date(2026, 9, 5),
            impressions=1000,
            clicks=30,
            avg_position=7.0,
            data_status=DataStatus.OBSERVED,
        ),
    ]


def make_baseline_url_metrics() -> list[URLDailyMetric]:
    return [
        URLDailyMetric(
            site_id="site-1",
            normalized_url="https://example.com/page",
            date=date(2026, 8, 25),
            total_impressions=1000,
            total_clicks=60,
        ),
    ]


def make_current_url_metrics() -> list[URLDailyMetric]:
    return [
        URLDailyMetric(
            site_id="site-1",
            normalized_url="https://example.com/page",
            date=date(2026, 9, 5),
            total_impressions=1000,
            total_clicks=30,
        ),
    ]


def make_evidence() -> ReplayEvidence:
    return ReplayEvidence(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        baseline_observations=make_baseline_observations(),
        current_observations=make_current_observations(),
        baseline_url_metrics=make_baseline_url_metrics(),
        current_url_metrics=make_current_url_metrics(),
        current_daily_status=[
            (
                date(2026, 9, 5),
                DataStatus.OBSERVED,
            ),
        ],
        baseline_daily_status=[
            (
                date(2026, 8, 25),
                DataStatus.OBSERVED,
            ),
        ],
        reconciliation=make_reconciliation(),
        snapshot=make_snapshot(),
    )


def run_valid_replay():
    return replay(
        evidence=make_evidence(),
        candidate_id="replay-candidate-001",
    )


def test_replay_completes_successfully():
    result = run_valid_replay()

    assert result.status == PipelineStatus.COMPLETED

    assert result.features is not None
    assert result.candidate is not None
    assert result.opportunity is not None
    assert result.strategy is not None
    assert result.action is not None

    assert result.candidate.candidate_id == (
        "replay-candidate-001"
    )

    assert (
        result.opportunity.candidate_id
        == "replay-candidate-001"
    )

    assert (
        result.strategy.opportunity_id
        == result.opportunity.opportunity_id
    )

    assert (
        result.action.strategy_id
        == result.strategy.strategy_id
    )

    assert (
        result.action.opportunity_id
        == result.opportunity.opportunity_id
    )


def test_replay_is_deterministic():
    first = run_valid_replay()
    second = run_valid_replay()

    assert first.model_dump() == second.model_dump()

    assert first.opportunity == second.opportunity
    assert first.strategy == second.strategy
    assert first.action == second.action


def test_replay_preserves_snapshot():
    result = run_valid_replay()

    assert result.candidate is not None
    assert result.opportunity is not None
    assert result.strategy is not None
    assert result.action is not None

    assert result.candidate.snapshot == make_snapshot()
    assert result.opportunity.snapshot == make_snapshot()
    assert result.strategy.snapshot == make_snapshot()
    assert result.action.snapshot == make_snapshot()


def test_replay_rejects_invalid_reconciliation():
    evidence = make_evidence().model_copy(
        update={
            "reconciliation": ReconciliationResult(
                impression_status=ReconciliationStatus.INVALID,
                click_status=ReconciliationStatus.MATCH,
                query_impressions=1000,
                url_impressions=1100,
                query_clicks=30,
                url_clicks=30,
                impression_gap=100,
                click_gap=0,
                is_valid=False,
                is_partial=False,
            ),
        },
    )

    result = replay(
        evidence=evidence,
        candidate_id="replay-invalid-001",
    )

    assert result.status == (
        PipelineStatus.REJECTED_RECONCILIATION
    )
    assert result.features is None
    assert result.signals == []
    assert result.candidate is None
    assert result.opportunity is None
    assert result.strategy is None
    assert result.action is None


def test_replay_uses_decision_engine_result():
    evidence = make_evidence()

    engine_result = run_decision_engine(
        evidence=evidence,
        candidate_id="candidate-replay-001",
    )

    replay_result = replay(
        evidence=evidence,
        candidate_id="candidate-replay-001",
    )

    assert replay_result.status == PipelineStatus.COMPLETED

    assert replay_result.features == engine_result.features
    assert replay_result.signals == engine_result.signals
    assert replay_result.classification == (
        engine_result.classification
    )
    assert replay_result.candidate == engine_result.candidate
    assert replay_result.opportunity == engine_result.opportunity
    assert replay_result.strategy == engine_result.strategy
    assert replay_result.action == engine_result.action


def test_replay_rejects_insufficient_data_quality():
    evidence = make_evidence().model_copy(
        update={
            "current_daily_status": [
                (
                    date(2026, 9, 1),
                    DataStatus.MISSING_UNKNOWN,
                ),
                (
                    date(2026, 9, 2),
                    DataStatus.MISSING_UNKNOWN,
                ),
                (
                    date(2026, 9, 3),
                    DataStatus.MISSING_UNKNOWN,
                ),
                (
                    date(2026, 9, 4),
                    DataStatus.MISSING_UNKNOWN,
                ),
                (
                    date(2026, 9, 5),
                    DataStatus.OBSERVED,
                ),
            ],
        },
    )

    result = replay(
        evidence=evidence,
        candidate_id="replay-low-quality-001",
    )

    assert result.status == (
        PipelineStatus.REJECTED_DATA_QUALITY
    )
    assert result.features is not None
    assert (
        result.features.data_quality.current_window_completeness
        == 0.2
    )
    assert result.signals == []
    assert result.candidate is None
    assert result.opportunity is None
    assert result.strategy is None
    assert result.action is None