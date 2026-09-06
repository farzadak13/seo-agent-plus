from datetime import datetime, timezone

import pytest

from app.models.serp import (
    SERPInvestigationStatus,
    SERPQuerySnapshot,
    SERPResultItem,
)
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import (
    Strategy,
    StrategyStatus,
    StrategyType,
)
from app.serp.engine import (
    build_serp_investigation,
    requires_serp_investigation,
)


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-serp-001",
        data_snapshot_id="data-serp-001",
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


def make_title_strategy() -> Strategy:
    return Strategy(
        strategy_id="strategy-serp-001",
        opportunity_id="opportunity-serp-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type="ctr_recovery",
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=0.85,
        expected_impact_score=0.80,
        effort_score=0.25,
        risk_score=0.30,
        priority_score=0.78,
        reasons=[
            "ctr_recovery",
        ],
        evidence={},
        snapshot=make_snapshot(),
    )


def make_monitor_strategy() -> Strategy:
    return Strategy(
        strategy_id="strategy-monitor-001",
        opportunity_id="opportunity-monitor-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type="position_recovery",
        strategy_type=StrategyType.MONITOR_ONLY,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=0.80,
        expected_impact_score=0.40,
        effort_score=0.05,
        risk_score=0.05,
        priority_score=0.60,
        reasons=[
            "monitor_due_to_volatility",
        ],
        evidence={},
        snapshot=make_snapshot(),
    )


def make_query_snapshot(
    query: str,
    results: list[SERPResultItem],
) -> SERPQuerySnapshot:
    return SERPQuerySnapshot(
        query=query,
        provider="test-provider",
        response_id=f"response-{query}",
        fetched_at=datetime(
            2026,
            9,
            5,
            10,
            30,
            tzinfo=timezone.utc,
        ),
        results=results,
    )


def test_title_strategy_requires_serp_investigation():
    strategy = make_title_strategy()

    assert requires_serp_investigation(strategy) is True


def test_non_title_strategy_does_not_require_serp():
    strategy = make_monitor_strategy()

    assert requires_serp_investigation(strategy) is False


def test_non_title_strategy_returns_not_required():
    strategy = make_monitor_strategy()

    result = build_serp_investigation(
        strategy=strategy,
        primary_queries=[],
        query_snapshots=[],
        investigation_id="investigation-001",
    )

    assert result.status == SERPInvestigationStatus.NOT_REQUIRED
    assert result.primary_queries == []
    assert result.query_evidence == []
    assert result.target_found_in_any_query is False
    assert result.target_best_position is None


def test_title_investigation_detects_target_position():
    strategy = make_title_strategy()

    snapshot = make_query_snapshot(
        "کفش مردانه",
        [
            SERPResultItem(
                position=1,
                url="https://competitor.com/a",
                title="خرید کفش مردانه ارزان",
            ),
            SERPResultItem(
                position=2,
                url="https://example.com/page",
                title="خرید کفش مردانه با بهترین قیمت",
                is_target=True,
            ),
            SERPResultItem(
                position=3,
                url="https://competitor.com/b",
                title="قیمت کفش مردانه",
            ),
        ],
    )

    result = build_serp_investigation(
        strategy=strategy,
        primary_queries=["کفش مردانه"],
        query_snapshots=[snapshot],
        investigation_id="investigation-002",
        target_title="خرید کفش مردانه با بهترین قیمت",
    )

    assert result.status == SERPInvestigationStatus.COMPLETED
    assert result.target_found_in_any_query is True
    assert result.target_best_position == 2

    evidence = result.query_evidence[0]

    assert evidence.target_found is True
    assert evidence.target_position == 2
    assert evidence.competitor_count == 2
    assert evidence.target_title_length == len(
        "خرید کفش مردانه با بهترین قیمت"
    )


def test_title_length_statistics_are_deterministic():
    strategy = make_title_strategy()

    snapshot = make_query_snapshot(
        "کفش مردانه",
        [
            SERPResultItem(
                position=1,
                url="https://competitor.com/a",
                title="کفش مردانه",
            ),
            SERPResultItem(
                position=2,
                url="https://competitor.com/b",
                title="خرید کفش مردانه ارزان",
            ),
            SERPResultItem(
                position=3,
                url="https://example.com/page",
                title="عنوان فعلی",
                is_target=True,
            ),
        ],
    )

    result = build_serp_investigation(
        strategy=strategy,
        primary_queries=["کفش مردانه"],
        query_snapshots=[snapshot],
        investigation_id="investigation-003",
        target_title="عنوان فعلی",
    )

    evidence = result.query_evidence[0]

    assert evidence.competitor_title_lengths == [
        len("کفش مردانه"),
        len("خرید کفش مردانه ارزان"),
    ]

    assert evidence.competitor_title_length_median == 15.5
    assert evidence.competitor_title_length_average == 15.5


def test_target_can_be_found_by_url_without_is_target_flag():
    strategy = make_title_strategy()

    snapshot = make_query_snapshot(
        "کفش مردانه",
        [
            SERPResultItem(
                position=1,
                url="https://competitor.com/a",
                title="نتیجه رقیب",
            ),
            SERPResultItem(
                position=7,
                url="https://example.com/page",
                title="عنوان فعلی",
            ),
        ],
    )

    result = build_serp_investigation(
        strategy=strategy,
        primary_queries=["کفش مردانه"],
        query_snapshots=[snapshot],
        investigation_id="investigation-004",
        target_title="عنوان فعلی",
    )

    assert result.target_found_in_any_query is True
    assert result.target_best_position == 7
    assert result.query_evidence[0].target_position == 7


def test_target_best_position_across_two_queries():
    strategy = make_title_strategy()

    snapshot_one = make_query_snapshot(
        "کفش مردانه",
        [
            SERPResultItem(
                position=4,
                url="https://example.com/page",
                title="عنوان فعلی",
            ),
        ],
    )

    snapshot_two = make_query_snapshot(
        "خرید کفش مردانه",
        [
            SERPResultItem(
                position=8,
                url="https://example.com/page",
                title="عنوان فعلی",
            ),
        ],
    )

    result = build_serp_investigation(
        strategy=strategy,
        primary_queries=[
            "کفش مردانه",
            "خرید کفش مردانه",
        ],
        query_snapshots=[
            snapshot_one,
            snapshot_two,
        ],
        investigation_id="investigation-005",
        target_title="عنوان فعلی",
    )

    assert result.status == SERPInvestigationStatus.COMPLETED
    assert result.target_found_in_any_query is True
    assert result.target_best_position == 4
    assert result.primary_queries == [
        "کفش مردانه",
        "خرید کفش مردانه",
    ]


def test_missing_target_is_valid_serp_evidence():
    strategy = make_title_strategy()

    snapshot = make_query_snapshot(
        "کفش مردانه",
        [
            SERPResultItem(
                position=1,
                url="https://competitor.com/a",
                title="رقیب اول",
            ),
            SERPResultItem(
                position=2,
                url="https://competitor.com/b",
                title="رقیب دوم",
            ),
        ],
    )

    result = build_serp_investigation(
        strategy=strategy,
        primary_queries=["کفش مردانه"],
        query_snapshots=[snapshot],
        investigation_id="investigation-006",
        target_title="عنوان فعلی",
    )

    assert result.status == SERPInvestigationStatus.COMPLETED
    assert result.target_found_in_any_query is False
    assert result.target_best_position is None
    assert result.query_evidence[0].target_found is False
    assert result.query_evidence[0].target_position is None


def test_more_than_two_primary_queries_is_rejected():
    strategy = make_title_strategy()

    snapshots = [
        make_query_snapshot(query, [])
        for query in [
            "q1",
            "q2",
            "q3",
        ]
    ]

    with pytest.raises(
        ValueError,
        match="at most 2 primary queries",
    ):
        build_serp_investigation(
            strategy=strategy,
            primary_queries=["q1", "q2", "q3"],
            query_snapshots=snapshots,
            investigation_id="investigation-007",
        )


def test_zero_primary_queries_is_rejected_for_title_strategy():
    strategy = make_title_strategy()

    with pytest.raises(
        ValueError,
        match="at least one primary query",
    ):
        build_serp_investigation(
            strategy=strategy,
            primary_queries=[],
            query_snapshots=[],
            investigation_id="investigation-008",
        )


def test_snapshot_count_must_match_query_count():
    strategy = make_title_strategy()

    snapshot = make_query_snapshot(
        "کفش مردانه",
        [],
    )

    with pytest.raises(
        ValueError,
        match="must match number of primary queries",
    ):
        build_serp_investigation(
            strategy=strategy,
            primary_queries=[
                "کفش مردانه",
                "خرید کفش مردانه",
            ],
            query_snapshots=[snapshot],
            investigation_id="investigation-009",
        )


def test_primary_queries_must_be_unique():
    strategy = make_title_strategy()

    snapshot = make_query_snapshot(
        "کفش مردانه",
        [],
    )

    with pytest.raises(
        ValueError,
        match="must be unique",
    ):
        build_serp_investigation(
            strategy=strategy,
            primary_queries=[
                "کفش مردانه",
                "کفش مردانه",
            ],
            query_snapshots=[
                snapshot,
                snapshot,
            ],
            investigation_id="investigation-010",
        )


def test_snapshot_query_order_must_match_primary_queries():
    strategy = make_title_strategy()

    snapshot_one = make_query_snapshot(
        "query-2",
        [],
    )

    snapshot_two = make_query_snapshot(
        "query-1",
        [],
    )

    with pytest.raises(
        ValueError,
        match="must match primary_queries order exactly",
    ):
        build_serp_investigation(
            strategy=strategy,
            primary_queries=[
                "query-1",
                "query-2",
            ],
            query_snapshots=[
                snapshot_one,
                snapshot_two,
            ],
            investigation_id="investigation-011",
        )


def test_serp_investigation_is_deterministic():
    strategy = make_title_strategy()

    snapshot = make_query_snapshot(
        "کفش مردانه",
        [
            SERPResultItem(
                position=1,
                url="https://competitor.com/a",
                title="خرید کفش مردانه",
            ),
            SERPResultItem(
                position=2,
                url="https://example.com/page",
                title="عنوان فعلی",
            ),
        ],
    )

    first = build_serp_investigation(
        strategy=strategy,
        primary_queries=["کفش مردانه"],
        query_snapshots=[snapshot],
        investigation_id="investigation-deterministic",
        target_title="عنوان فعلی",
    )

    second = build_serp_investigation(
        strategy=strategy,
        primary_queries=["کفش مردانه"],
        query_snapshots=[snapshot],
        investigation_id="investigation-deterministic",
        target_title="عنوان فعلی",
    )

    assert first.model_dump() == second.model_dump()