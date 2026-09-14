from datetime import datetime, timezone

import pytest

from app.models.actions import (
    Action,
    ActionRiskLevel,
    ActionStatus,
    ActionType,
)
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType
from app.models.verification import VerificationStatus
from app.verification.engine import apply_verification, verify_action_state


def snapshot():
    return SnapshotMetadata(
        snapshot_id="snapshot-verification",
        data_snapshot_id="data-verification",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )


def action():
    return Action(
        action_id="action-verification-001",
        strategy_id="strategy-001",
        opportunity_id="opportunity-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        action_type=ActionType.OPTIMIZE_TITLE,
        status=ActionStatus.WAITING_FOR_RECRAWL,
        risk_level=ActionRiskLevel.LOW,
        confidence_score=0.9,
        expected_impact_score=0.8,
        priority_score=0.9,
        requires_approval=True,
        reasons=[],
        parameters={
            "expected_title": "خرید کفش مردانه اصل",
        },
        evidence={},
        snapshot=snapshot(),
    )


def test_matching_state_is_verified():
    result = verify_action_state(
        action=action(),
        verification_id="verification-001",
        observed_state={
            "title": "خرید کفش مردانه اصل",
        },
    )
    assert result.status == VerificationStatus.VERIFIED
    assert result.matched_keys == ["title"]
    assert result.mismatched_keys == []


def test_mismatching_state_is_not_verified():
    result = verify_action_state(
        action=action(),
        verification_id="verification-002",
        observed_state={
            "title": "عنوان قدیمی",
        },
    )
    assert result.status == VerificationStatus.NOT_VERIFIED
    assert result.mismatched_keys == ["title"]


def test_verified_state_enters_measurement_window():
    verification = verify_action_state(
        action=action(),
        verification_id="verification-003",
        observed_state={
            "title": "خرید کفش مردانه اصل",
        },
    )
    updated = apply_verification(
        action=action(),
        verification=verification,
    )
    assert updated.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE


def test_failed_verification_enters_failed_state():
    verification = verify_action_state(
        action=action(),
        verification_id="verification-004",
        observed_state={
            "title": "عنوان قدیمی",
        },
    )
    updated = apply_verification(
        action=action(),
        verification=verification,
    )
    assert updated.status == ActionStatus.FAILED


def test_incomplete_observation_is_inconclusive():
    result = verify_action_state(
        action=action(),
        verification_id="verification-005",
        observed_state={},
    )

    assert result.status == VerificationStatus.INCONCLUSIVE
    assert result.mismatched_keys == ["title"]

    updated = apply_verification(
        action=action(),
        verification=result,
    )
    assert updated.status == ActionStatus.WAITING_FOR_RECRAWL


def test_verification_requires_waiting_for_recrawl():
    not_waiting = action().model_copy(
        update={"status": ActionStatus.EXECUTED}
    )
    with pytest.raises(ValueError, match="WAITING_FOR_RECRAWL"):
        verify_action_state(
            action=not_waiting,
            verification_id="verification-006",
            observed_state={"title": "x"},
        )


def test_verification_action_mismatch_is_rejected():
    verification = verify_action_state(
        action=action(),
        verification_id="verification-007",
        observed_state={"title": "خرید کفش مردانه اصل"},
    ).model_copy(update={"action_id": "other-action"})

    with pytest.raises(ValueError, match="action_id"):
        apply_verification(action=action(), verification=verification)


def test_verification_is_deterministic_for_fixed_timestamp():
    checked_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    first = verify_action_state(
        action=action(),
        verification_id="verification-008",
        observed_state={"title": "خرید کفش مردانه اصل"},
        checked_at=checked_at,
    )
    second = verify_action_state(
        action=action(),
        verification_id="verification-008",
        observed_state={"title": "خرید کفش مردانه اصل"},
        checked_at=checked_at,
    )
    assert first.model_dump() == second.model_dump()
