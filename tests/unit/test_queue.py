from datetime import datetime, timezone


from app.classifier.clustering import cluster_candidates
from app.classifier.queue import build_investigation_queue
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
    priority: float,
    confidence: float,
    status: CandidateStatus,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        site_id="site-1",
        normalized_url=f"https://example.com/{candidate_id}",
        normalized_query=f"query-{candidate_id}",
        status=status,
        confidence=confidence,
        priority_score=priority,
        signal_types=["CTR_DROP"],
        risk_types=[],
        reasons=["CTR_DROP"],
        snapshot=make_snapshot(),
    )


def test_queue_contains_only_investigate_candidates():
    candidates = [
        make_candidate(
            "c1",
            10.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c2",
            20.0,
            1.0,
            CandidateStatus.REJECT,
        ),
        make_candidate(
            "c3",
            5.0,
            0.8,
            CandidateStatus.INVESTIGATE,
        ),
    ]

    queue = build_investigation_queue(candidates)

    assert [candidate.candidate_id for candidate in queue] == [
        "c1",
        "c3",
    ]


def test_queue_is_ordered_by_priority():
    candidates = [
        make_candidate(
            "c1",
            5.0,
            1.0,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c2",
            10.0,
            0.5,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c3",
            8.0,
            0.8,
            CandidateStatus.INVESTIGATE,
        ),
    ]

    queue = build_investigation_queue(candidates)

    assert [candidate.candidate_id for candidate in queue] == [
        "c2",
        "c3",
        "c1",
    ]


def test_confidence_breaks_priority_tie():
    candidates = [
        make_candidate(
            "c1",
            10.0,
            0.6,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c2",
            10.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
    ]

    queue = build_investigation_queue(candidates)

    assert [candidate.candidate_id for candidate in queue] == [
        "c2",
        "c1",
    ]


def test_queue_respects_budget():
    candidates = [
        make_candidate(
            "c1",
            10.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c2",
            9.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c3",
            8.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
    ]

    queue = build_investigation_queue(
        candidates,
        max_candidates=2,
    )

    assert len(queue) == 2
    assert [candidate.candidate_id for candidate in queue] == [
        "c1",
        "c2",
    ]


def test_empty_queue_is_valid():
    queue = build_investigation_queue([])

    assert queue == []


def test_queue_limits_candidates_per_cluster():
    candidates = [
        make_candidate(
            "c1",
            10.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c2",
            9.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c3",
            8.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c4",
            7.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
    ]

    clusters = cluster_candidates(candidates)

    queue = build_investigation_queue(
        candidates,
        max_candidates=10,
        max_per_cluster=2,
        clusters=clusters,
    )

    assert len(queue) == 2
    assert [candidate.candidate_id for candidate in queue] == [
        "c1",
        "c2",
    ]


def test_candidates_without_cluster_can_enter_queue():
    candidates = [
        make_candidate(
            "c1",
            10.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c2",
            9.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
    ]

    queue = build_investigation_queue(
        candidates,
        max_candidates=10,
        max_per_cluster=1,
        clusters=[],
    )

    assert len(queue) == 2


def test_cluster_budget_and_global_budget_work_together():
    candidates = [
        make_candidate(
            "c1",
            10.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c2",
            9.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
        make_candidate(
            "c3",
            8.0,
            0.9,
            CandidateStatus.INVESTIGATE,
        ),
    ]

    clusters = cluster_candidates(candidates)

    queue = build_investigation_queue(
        candidates,
        max_candidates=2,
        max_per_cluster=5,
        clusters=clusters,
    )

    assert len(queue) == 2
    assert [candidate.candidate_id for candidate in queue] == [
        "c1",
        "c2",
    ]
def test_rejected_candidates_are_not_investigated():
    rejected_candidate = make_candidate(
        "rejected-1",
        100.0,
        1.0,
        CandidateStatus.REJECT,
    )

    queue = build_investigation_queue(
        [rejected_candidate],
    )

    assert queue == []


def test_high_priority_rejected_candidate_is_excluded():
    rejected_candidate = make_candidate(
        "rejected-high-priority",
        99999.0,
        1.0,
        CandidateStatus.REJECT,
    )

    queue = build_investigation_queue(
        [rejected_candidate],
    )

    assert queue == []


def test_investigate_candidate_is_admitted():
    candidate = make_candidate(
        "investigate-1",
        10.0,
        0.9,
        CandidateStatus.INVESTIGATE,
    )

    queue = build_investigation_queue(
        [candidate],
    )

    assert len(queue) == 1
    assert queue[0].candidate_id == candidate.candidate_id