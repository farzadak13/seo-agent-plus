from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.candidates import Candidate, CandidateStatus
from app.models.snapshots import SnapshotMetadata


def make_snapshot(
    snapshot_id: str = "snapshot-001",
    data_snapshot_id: str = "data-001",
    rule_version: str = "rules-v1",
    config_version: str = "config-v1",
) -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id=snapshot_id,
        data_snapshot_id=data_snapshot_id,
        rule_version=rule_version,
        config_version=config_version,
        generated_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )


def test_valid_candidate():
    candidate = Candidate(
        candidate_id="candidate-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        status=CandidateStatus.INVESTIGATE,
        confidence=0.8,
        priority_score=4.2,
        signal_types=[
            "HIGH_VALUE_OPPORTUNITY",
            "CTR_DROP",
        ],
        risk_types=[
            "CTR_DROP_UNDER_VOLATILITY",
        ],
        reasons=[
            "HIGH_VALUE_OPPORTUNITY",
            "CTR_DROP",
        ],
        snapshot=make_snapshot(),
    )

    assert candidate.status == CandidateStatus.INVESTIGATE
    assert candidate.confidence == 0.8
    assert candidate.priority_score == 4.2
    assert "CTR_DROP" in candidate.signal_types
    assert candidate.snapshot.snapshot_id == "snapshot-001"


def test_confidence_must_be_between_zero_and_one():
    with pytest.raises(ValidationError):
        Candidate(
            candidate_id="candidate-002",
            site_id="site-1",
            normalized_url="https://example.com/page",
            normalized_query="کفش مردانه",
            status=CandidateStatus.INVESTIGATE,
            confidence=1.1,
            priority_score=1.0,
            snapshot=make_snapshot(),
        )


def test_negative_priority_score_is_rejected():
    with pytest.raises(ValidationError):
        Candidate(
            candidate_id="candidate-003",
            site_id="site-1",
            normalized_url="https://example.com/page",
            normalized_query="کفش مردانه",
            status=CandidateStatus.INVESTIGATE,
            confidence=0.8,
            priority_score=-1.0,
            snapshot=make_snapshot(),
        )


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        Candidate(
            candidate_id="candidate-004",
            site_id="site-1",
            normalized_url="https://example.com/page",
            normalized_query="کفش مردانه",
            status=CandidateStatus.INVESTIGATE,
            confidence=0.8,
            priority_score=1.0,
            snapshot=make_snapshot(),
            unexpected_field="bad",
        )