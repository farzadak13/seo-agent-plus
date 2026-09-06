from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.evidence import DecisionEvidence
from app.models.observations import DataStatus, DailyObservation
from app.models.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric


def make_observation(
    *,
    site_id: str = "site-1",
    url: str = "https://example.com/page",
    query: str = "seo",
    day: date = date(2026, 9, 1),
    impressions: int = 100,
    clicks: int = 10,
    position: float = 5.0,
) -> DailyObservation:
    return DailyObservation(
        site_id=site_id,
        normalized_url=url,
        normalized_query=query,
        date=day,
        impressions=impressions,
        clicks=clicks,
        avg_position=position,
        data_status=DataStatus.OBSERVED,
    )


def make_url_metric(
    *,
    day: date = date(2026, 9, 1),
) -> URLDailyMetric:
    return URLDailyMetric(
        site_id="site-1",
        normalized_url="https://example.com/page",
        date=day,
        total_impressions=100,
        total_clicks=10,
    )


def make_reconciliation() -> ReconciliationResult:
    return ReconciliationResult(
        impression_status=ReconciliationStatus.MATCH,
        click_status=ReconciliationStatus.MATCH,
        query_impressions=100,
        url_impressions=100,
        query_clicks=10,
        url_clicks=10,
        impression_gap=0,
        click_gap=0,
        is_valid=True,
        is_partial=False,
    )


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-1",
        data_snapshot_id="data-1",
        rule_version="rules-1",
        config_version="config-1",
        generated_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )
def make_evidence() -> DecisionEvidence:
    observation = make_observation()
    url_metric = make_url_metric()

    return DecisionEvidence(
        site_id="site-1",
        normalized_url=observation.normalized_url,
        normalized_query=observation.normalized_query,
        baseline_observations=[observation],
        current_observations=[observation],
        baseline_url_metrics=[url_metric],
        current_url_metrics=[url_metric],
        baseline_daily_status=[
            (observation.date, DataStatus.OBSERVED),
        ],
        current_daily_status=[
            (observation.date, DataStatus.OBSERVED),
        ],
        reconciliation=make_reconciliation(),
        snapshot=make_snapshot(),
    )


def test_decision_evidence_contains_canonical_replay_contract():
    evidence = make_evidence()

    assert evidence.site_id == "site-1"
    assert evidence.normalized_url == "https://example.com/page"
    assert evidence.normalized_query == "seo"

    assert len(evidence.baseline_observations) == 1
    assert len(evidence.current_observations) == 1

    assert len(evidence.baseline_url_metrics) == 1
    assert len(evidence.current_url_metrics) == 1

    assert evidence.current_daily_status[0] == (
        date(2026, 9, 1),
        DataStatus.OBSERVED,
    )

    assert evidence.reconciliation.is_valid is True
    assert evidence.snapshot.snapshot_id == "snapshot-1"


def test_decision_evidence_uses_typed_data_status():
    evidence = make_evidence()

    status = evidence.current_daily_status[0][1]

    assert isinstance(status, DataStatus)
    assert status == DataStatus.OBSERVED


def test_decision_evidence_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        DecisionEvidence(
            **make_evidence().model_dump(),
            unexpected_field="must-fail",
        )


def test_replay_evidence_is_same_canonical_contract():
    from app.models.replay import ReplayEvidence

    evidence = make_evidence()

    replay_evidence = ReplayEvidence.model_validate(
        evidence.model_dump()
    )

    assert isinstance(replay_evidence, DecisionEvidence)
    assert replay_evidence == evidence