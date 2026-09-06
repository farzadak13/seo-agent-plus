from datetime import date, datetime, timezone

from app.engine.decision import run_decision_engine
from app.models.evidence import DecisionEvidence
from app.models.observations import DataStatus, DailyObservation
from app.models.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-engine-001",
        data_snapshot_id="data-engine-001",
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


def make_url_metric(
    *,
    day: date,
    impressions: int,
    clicks: int,
) -> URLDailyMetric:
    return URLDailyMetric(
        site_id="site-1",
        normalized_url="https://example.com/page",
        date=day,
        total_impressions=impressions,
        total_clicks=clicks,
    )


def make_observation(
    *,
    day: date,
    impressions: int,
    clicks: int,
    position: float,
) -> DailyObservation:
    return DailyObservation(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=day,
        impressions=impressions,
        clicks=clicks,
        avg_position=position,
        data_status=DataStatus.OBSERVED,
    )


def make_evidence() -> DecisionEvidence:
    baseline_day = date(2026, 8, 25)
    current_day = date(2026, 9, 5)

    baseline = make_observation(
        day=baseline_day,
        impressions=1000,
        clicks=60,
        position=4.0,
    )

    current = make_observation(
        day=current_day,
        impressions=1000,
        clicks=30,
        position=7.0,
    )

    baseline_url_metric = make_url_metric(
        day=baseline_day,
        impressions=1000,
        clicks=60,
    )

    current_url_metric = make_url_metric(
        day=current_day,
        impressions=1000,
        clicks=30,
    )

    return DecisionEvidence(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        baseline_observations=[baseline],
        current_observations=[current],
        baseline_url_metrics=[baseline_url_metric],
        current_url_metrics=[current_url_metric],
        baseline_daily_status=[
            (
                baseline_day,
                DataStatus.OBSERVED,
            ),
        ],
        current_daily_status=[
            (
                current_day,
                DataStatus.OBSERVED,
            ),
        ],
        reconciliation=ReconciliationResult(
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
        ),
        snapshot=make_snapshot(),
    )


def test_decision_engine_produces_candidate():
    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="candidate-engine-001",
    )

    assert result.features.volume.current_impressions == 1000
    assert len(result.signals) > 0

    assert result.candidate is not None
    assert (
        result.candidate.candidate_id
        == "candidate-engine-001"
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

    assert (
        result.action.opportunity_id
        == result.opportunity.opportunity_id
    )


def test_decision_engine_is_deterministic():
    evidence = make_evidence()

    first = run_decision_engine(
        evidence=evidence,
        candidate_id="candidate-engine-001",
    )

    second = run_decision_engine(
        evidence=evidence,
        candidate_id="candidate-engine-001",
    )

    assert first.model_dump() == second.model_dump()
    assert first.strategy == second.strategy
    assert first.action == second.action


def test_decision_engine_respects_partial_reconciliation():
    evidence = make_evidence().model_copy(
        update={
            "reconciliation": ReconciliationResult(
                impression_status=ReconciliationStatus.PARTIAL,
                click_status=ReconciliationStatus.MATCH,
                query_impressions=1000,
                url_impressions=1100,
                query_clicks=30,
                url_clicks=30,
                impression_gap=100,
                click_gap=0,
                is_valid=True,
                is_partial=True,
            ),
        },
    )

    result = run_decision_engine(
        evidence=evidence,
        candidate_id="candidate-engine-002",
    )

    assert (
        result.features.data_quality.reconciliation_completeness
        == 0.75
    )