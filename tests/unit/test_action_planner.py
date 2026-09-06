from datetime import datetime, timezone

from app.action.planner import build_action
from app.models.actions import (
    ActionRiskLevel,
    ActionStatus,
    ActionType,
)
from app.models.opportunities import OpportunityType
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import (
    Strategy,
    StrategyStatus,
    StrategyType,
)


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-action-001",
        data_snapshot_id="data-action-001",
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


def make_strategy(
    strategy_type: StrategyType,
    risk_score: float = 0.20,
) -> Strategy:
    return Strategy(
        strategy_id="strategy-action-001",
        opportunity_id="opportunity-action-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type=OpportunityType.CTR_RECOVERY,
        strategy_type=strategy_type,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=0.90,
        expected_impact_score=0.80,
        effort_score=0.30,
        risk_score=risk_score,
        priority_score=0.85,
        reasons=["test"],
        evidence={"test": True},
        snapshot=make_snapshot(),
    )


def test_title_strategy_creates_approval_action():
    action = build_action(
        action_id="action-001",
        strategy=make_strategy(
            StrategyType.SERP_TITLE_OPTIMIZATION,
        ),
    )

    assert action.action_type == ActionType.OPTIMIZE_TITLE
    assert action.requires_approval is True
    assert action.status == ActionStatus.AWAITING_APPROVAL
    assert action.risk_level == ActionRiskLevel.LOW


def test_monitor_strategy_can_start_planned():
    action = build_action(
        action_id="action-002",
        strategy=make_strategy(
            StrategyType.MONITOR_ONLY,
        ),
    )

    assert action.action_type == ActionType.MONITOR
    assert action.requires_approval is False
    assert action.status == ActionStatus.PLANNED


def test_high_risk_strategy_requires_approval():
    action = build_action(
        action_id="action-003",
        strategy=make_strategy(
            StrategyType.INTERNAL_LINKING,
            risk_score=0.70,
        ),
    )

    assert action.requires_approval is True
    assert action.risk_level == ActionRiskLevel.HIGH
    assert action.status == ActionStatus.AWAITING_APPROVAL


def test_action_is_deterministic():
    strategy = make_strategy(
        StrategyType.SERP_TITLE_OPTIMIZATION,
    )

    first = build_action(
        action_id="action-004",
        strategy=strategy,
    )

    second = build_action(
        action_id="action-004",
        strategy=strategy,
    )

    assert first == second


def test_action_preserves_strategy_context():
    strategy = make_strategy(
        StrategyType.CONTENT_DEPTH_IMPROVEMENT,
    )

    action = build_action(
        action_id="action-005",
        strategy=strategy,
    )

    assert action.strategy_id == strategy.strategy_id
    assert action.opportunity_id == strategy.opportunity_id
    assert action.snapshot == strategy.snapshot

    