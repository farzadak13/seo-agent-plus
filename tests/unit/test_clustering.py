from datetime import datetime, timezone

from app.classifier.clustering import (
    build_candidate_fingerprint,
    cluster_candidates,
    deduplicate_candidates,
)
from app.models.candidates import Candidate, CandidateStatus
from app.models.snapshots import SnapshotMetadata


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-1",
        data_snapshot_id="data-1",
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


def make_candidate(
    candidate_id: str,
    url: str,
    query: str,
    priority: float,
    signals: list[str],
    risks: list[str] | None = None,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        site_id="site-1",
        normalized_url=url,
        normalized_query=query,
        status=CandidateStatus.INVESTIGATE,
        confidence=0.8,
        priority_score=priority,
        signal_types=signals,
        risk_types=risks or [],
        reasons=signals,
        snapshot=make_snapshot(),
    )


def test_same_signal_pattern_has_same_fingerprint():
    first = make_candidate(
        "c1",
        "https://example.com/a",
        "query-a",
        5.0,
        ["CTR_DROP", "POSITION_DECLINE"],
    )

    second = make_candidate(
        "c2",
        "https://example.com/b",
        "query-b",
        4.0,
        ["POSITION_DECLINE", "CTR_DROP"],
    )

    assert (
        build_candidate_fingerprint(first)
        == build_candidate_fingerprint(second)
    )


def test_different_signal_pattern_has_different_fingerprint():
    first = make_candidate(
        "c1",
        "https://example.com/a",
        "query-a",
        5.0,
        ["CTR_DROP"],
    )

    second = make_candidate(
        "c2",
        "https://example.com/b",
        "query-b",
        4.0,
        ["POSITION_DECLINE"],
    )

    assert (
        build_candidate_fingerprint(first)
        != build_candidate_fingerprint(second)
    )


def test_candidates_are_clustered_by_fingerprint():
    candidates = [
        make_candidate(
            "c1",
            "https://example.com/a",
            "query-a",
            5.0,
            ["CTR_DROP"],
        ),
        make_candidate(
            "c2",
            "https://example.com/b",
            "query-b",
            8.0,
            ["CTR_DROP"],
        ),
        make_candidate(
            "c3",
            "https://example.com/c",
            "query-c",
            3.0,
            ["POSITION_DECLINE"],
        ),
    ]

    clusters = cluster_candidates(candidates)

    assert len(clusters) == 2

    ctr_cluster = next(
        cluster
        for cluster in clusters
        if "c1" in cluster.candidate_ids
    )

    assert ctr_cluster.candidate_count == 2
    assert ctr_cluster.representative_priority_score == 8.0


def test_deduplication_keeps_highest_priority():
    candidates = [
        make_candidate(
            "c1",
            "https://example.com/page",
            "query",
            5.0,
            ["CTR_DROP"],
        ),
        make_candidate(
            "c2",
            "https://example.com/page",
            "query",
            9.0,
            ["CTR_DROP"],
        ),
    ]

    result = deduplicate_candidates(candidates)

    assert len(result) == 1
    assert result[0].candidate_id == "c2"


def test_deduplication_keeps_different_queries():
    candidates = [
        make_candidate(
            "c1",
            "https://example.com/page",
            "query-a",
            5.0,
            ["CTR_DROP"],
        ),
        make_candidate(
            "c2",
            "https://example.com/page",
            "query-b",
            9.0,
            ["CTR_DROP"],
        ),
    ]

    result = deduplicate_candidates(candidates)

    assert len(result) == 2