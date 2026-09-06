from datetime import date, datetime, timezone

from app.engine.decision import run_decision_engine
from app.models.evidence import DecisionEvidence
from app.models.observations import (
    DataStatus,
    DailyObservation,
)
from app.models.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-opportunity-001",
        data_snapshot_id="data-opportunity-001",
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


def make_evidence() -> DecisionEvidence:
    baseline_day = date(2026, 8, 25)
    current_day = date(2026, 9, 5)

    baseline = make_observation(
        day=baseline_day,
        impressions=2000,
        clicks=120,
        position=4.0,
    )

    current = make_observation(
        day=current_day,
        impressions=2000,
        clicks=60,
        position=7.0,
    )

    return DecisionEvidence(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        baseline_observations=[baseline],
        current_observations=[current],
        baseline_url_metrics=[
            make_url_metric(
                day=baseline_day,
                impressions=2000,
                clicks=120,
            ),
        ],
        current_url_metrics=[
            make_url_metric(
                day=current_day,
                impressions=2000,
                clicks=60,
            ),
        ],
        baseline_daily_status=[
            (baseline_day, DataStatus.OBSERVED),
        ],
        current_daily_status=[
            (current_day, DataStatus.OBSERVED),
        ],
        reconciliation=ReconciliationResult(
            impression_status=ReconciliationStatus.MATCH,
            click_status=ReconciliationStatus.MATCH,
            query_impressions=2000,
            url_impressions=2000,
            query_clicks=60,
            url_clicks=60,
            impression_gap=0,
            click_gap=0,
            is_valid=True,
            is_partial=False,
        ),
        snapshot=make_snapshot(),
    )


def test_decision_engine_produces_opportunity():
    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="candidate-opportunity-001",
    )

    assert result.opportunity is not None
    assert (
        result.opportunity.candidate_id
        == "candidate-opportunity-001"
    )
    assert 0 <= result.opportunity.priority_score <= 1
    assert result.opportunity.signal_types


def test_opportunity_is_deterministic():
    evidence = make_evidence()

    first = run_decision_engine(
        evidence=evidence,
        candidate_id="candidate-opportunity-001",
    )

    second = run_decision_engine(
        evidence=evidence,
        candidate_id="candidate-opportunity-001",
    )

    assert first.opportunity == second.opportunity


def test_opportunity_contains_scoring_dimensions():
    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="candidate-opportunity-002",
    )

    opportunity = result.opportunity

    assert opportunity is not None
    assert opportunity.impact_score > 0
    assert opportunity.confidence_score > 0
    assert opportunity.business_value_score > 0
    assert opportunity.traffic_potential_score > 0
    assert 0 <= opportunity.effort_score <= 1
    assert 0 <= opportunity.risk_score <= 1


def test_rejected_data_quality_has_no_opportunity():
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

    result = run_decision_engine(
        evidence=evidence,
        candidate_id="candidate-opportunity-003",
    )

    assert result.opportunity is None


def test_opportunity_snapshot_matches_evidence():
    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="candidate-opportunity-004",
    )

    assert result.opportunity is not None
    assert (
        result.opportunity.snapshot
        == make_snapshot()
    )