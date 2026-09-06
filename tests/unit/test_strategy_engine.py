from datetime import datetime, timezone

from app.models.opportunities import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
)
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import (
    StrategyStatus,
    StrategyType,
)
from app.strategy.engine import build_strategy


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-strategy-001",
        data_snapshot_id="data-strategy-001",
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


def make_opportunity(
    *,
    opportunity_type: OpportunityType,
    signal_types: list[str],
    risk_types: list[str] | None = None,
) -> Opportunity:
    return Opportunity(
        opportunity_id="opportunity-strategy-001",
        candidate_id="candidate-strategy-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type=opportunity_type,
        status=OpportunityStatus.QUALIFIED,
        impact_score=0.80,
        confidence_score=0.90,
        business_value_score=0.70,
        traffic_potential_score=0.80,
        effort_score=0.40,
        risk_score=0.10,
        priority_score=0.82,
        signal_types=signal_types,
        risk_types=risk_types or [],
        reasons=["test"],
        evidence={"test": True},
        snapshot=make_snapshot(),
    )


def test_ctr_recovery_maps_to_title_strategy():
    opportunity = make_opportunity(
        opportunity_type=OpportunityType.CTR_RECOVERY,
        signal_types=["ctr_drop"],
    )

    strategy = build_strategy(
        strategy_id="strategy-001",
        opportunity=opportunity,
    )

    assert strategy is not None
    assert strategy.status == StrategyStatus.RECOMMENDED
    assert (
        strategy.strategy_type
        == StrategyType.SERP_TITLE_OPTIMIZATION
    )


def test_position_recovery_maps_to_internal_linking():
    opportunity = make_opportunity(
        opportunity_type=OpportunityType.POSITION_RECOVERY,
        signal_types=["position_decline"],
    )

    strategy = build_strategy(
        strategy_id="strategy-002",
        opportunity=opportunity,
    )

    assert strategy is not None
    assert (
        strategy.strategy_type
        == StrategyType.INTERNAL_LINKING
    )


def test_volatile_position_recovery_prefers_monitoring():
    opportunity = make_opportunity(
        opportunity_type=OpportunityType.POSITION_RECOVERY,
        signal_types=[
            "position_decline",
            "position_decline_under_volatility",
        ],
        risk_types=[
            "position_decline_under_volatility",
        ],
    )

    strategy = build_strategy(
        strategy_id="strategy-003",
        opportunity=opportunity,
    )

    assert strategy is not None
    assert strategy.strategy_type == StrategyType.MONITOR_ONLY


def test_strategy_is_deterministic():
    opportunity = make_opportunity(
        opportunity_type=OpportunityType.CTR_RECOVERY,
        signal_types=["ctr_drop"],
    )

    first = build_strategy(
        strategy_id="strategy-004",
        opportunity=opportunity,
    )

    second = build_strategy(
        strategy_id="strategy-004",
        opportunity=opportunity,
    )

    assert first == second


def test_unqualified_opportunity_produces_no_strategy():
    opportunity = make_opportunity(
        opportunity_type=OpportunityType.CTR_RECOVERY,
        signal_types=["ctr_drop"],
    ).model_copy(
        update={
            "status": OpportunityStatus.REJECTED,
        },
    )

    strategy = build_strategy(
        strategy_id="strategy-005",
        opportunity=opportunity,
    )

    assert strategy is None


def test_strategy_preserves_snapshot():
    opportunity = make_opportunity(
        opportunity_type=OpportunityType.CTR_RECOVERY,
        signal_types=["ctr_drop"],
    )

    strategy = build_strategy(
        strategy_id="strategy-006",
        opportunity=opportunity,
    )

    assert strategy is not None
    assert strategy.snapshot == opportunity.snapshot