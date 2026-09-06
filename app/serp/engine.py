from statistics import mean, median

from app.models.serp import (
    SERPInvestigation,
    SERPInvestigationStatus,
    SERPQueryEvidence,
    SERPQuerySnapshot,
)
from app.models.strategies import Strategy, StrategyType


MAX_PRIMARY_QUERIES = 2


def requires_serp_investigation(strategy: Strategy) -> bool:
    return strategy.strategy_type == StrategyType.SERP_TITLE_OPTIMIZATION


def _normalize_title(title: str) -> str:
    return " ".join(title.split())


def _title_length(title: str) -> int:
    return len(_normalize_title(title))


def _build_query_evidence(
    query_snapshot: SERPQuerySnapshot,
    target_url: str,
    target_title: str | None,
) -> SERPQueryEvidence:
    target_result = next(
        (
            result
            for result in query_snapshot.results
            if result.is_target or result.url == target_url
        ),
        None,
    )

    competitors = [
        result
        for result in query_snapshot.results
        if result.url != target_url
        and not result.is_target
    ]

    competitor_titles = [
        _normalize_title(result.title)
        for result in competitors
        if result.title.strip()
    ]

    competitor_title_lengths = [
        _title_length(result.title)
        for result in competitors
        if result.title.strip()
    ]

    competitor_count = len(competitors)

    median_length = (
        float(median(competitor_title_lengths))
        if competitor_title_lengths
        else None
    )

    average_length = (
        float(mean(competitor_title_lengths))
        if competitor_title_lengths
        else None
    )

    normalized_target_title = (
        _normalize_title(target_title)
        if target_title
        else None
    )

    target_title_length = (
        _title_length(target_title)
        if target_title
        else None
    )

    title_gap = None

    if target_title_length is not None and median_length is not None:
        title_gap = float(target_title_length - median_length)

    return SERPQueryEvidence(
        query=query_snapshot.query,
        target_found=target_result is not None,
        target_position=(
            target_result.position
            if target_result is not None
            else None
        ),
        top_results_count=len(query_snapshot.results),
        competitor_count=competitor_count,
        competitor_titles=competitor_titles,
        target_title=normalized_target_title,
        target_title_length=target_title_length,
        competitor_title_lengths=competitor_title_lengths,
        competitor_title_length_median=median_length,
        competitor_title_length_average=average_length,
        title_length_gap_vs_median=title_gap,
    )


def build_serp_investigation(
    *,
    strategy: Strategy,
    primary_queries: list[str],
    query_snapshots: list[SERPQuerySnapshot],
    investigation_id: str,
    target_title: str | None = None,
) -> SERPInvestigation:
    if not requires_serp_investigation(strategy):
        return SERPInvestigation(
            investigation_id=investigation_id,
            strategy_id=strategy.strategy_id,
            opportunity_id=strategy.opportunity_id,
            site_id=strategy.site_id,
            normalized_url=strategy.normalized_url,
            status=SERPInvestigationStatus.NOT_REQUIRED,
            primary_queries=[],
            query_evidence=[],
            target_title=target_title,
            target_found_in_any_query=False,
            target_best_position=None,
            evidence={
                "reason": "serp_not_required_for_strategy",
                "strategy_type": strategy.strategy_type.value,
            },
        )

    if not primary_queries:
        raise ValueError(
            "SERP title optimization requires at least one primary query."
        )

    if len(primary_queries) > MAX_PRIMARY_QUERIES:
        raise ValueError(
            "SERP investigation supports at most 2 primary queries."
        )

    if len(query_snapshots) != len(primary_queries):
        raise ValueError(
            "Number of SERP snapshots must match number of primary queries."
        )

    if len(set(primary_queries)) != len(primary_queries):
        raise ValueError(
            "Primary queries must be unique."
        )

    snapshot_queries = [snapshot.query for snapshot in query_snapshots]

    if snapshot_queries != primary_queries:
        raise ValueError(
            "SERP snapshot queries must match primary_queries order exactly."
        )

    query_evidence = [
        _build_query_evidence(
            query_snapshot=snapshot,
            target_url=strategy.normalized_url,
            target_title=target_title,
        )
        for snapshot in query_snapshots
    ]

    found_positions = [
        evidence.target_position
        for evidence in query_evidence
        if evidence.target_position is not None
    ]

    target_found = bool(found_positions)

    best_position = min(found_positions) if found_positions else None

    status = (
        SERPInvestigationStatus.COMPLETED
        if query_evidence
        else SERPInvestigationStatus.INCOMPLETE
    )

    return SERPInvestigation(
        investigation_id=investigation_id,
        strategy_id=strategy.strategy_id,
        opportunity_id=strategy.opportunity_id,
        site_id=strategy.site_id,
        normalized_url=strategy.normalized_url,
        status=status,
        primary_queries=primary_queries,
        query_evidence=query_evidence,
        target_title=(
            _normalize_title(target_title)
            if target_title
            else None
        ),
        target_found_in_any_query=target_found,
        target_best_position=best_position,
        evidence={
            "queries_investigated": len(primary_queries),
            "target_found_in_any_query": target_found,
            "target_best_position": best_position,
            "max_primary_queries": MAX_PRIMARY_QUERIES,
        },
    )