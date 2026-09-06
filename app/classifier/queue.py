from collections.abc import Sequence

from app.models.candidates import Candidate, CandidateStatus
from app.models.clusters import CandidateCluster


def build_investigation_queue(
    candidates: Sequence[Candidate],
    *,
    max_candidates: int = 50,
    max_per_cluster: int = 5,
    clusters: Sequence[CandidateCluster] | None = None,
) -> list[Candidate]:
    """
    Build a bounded investigation queue.

    Only INVESTIGATE candidates are eligible for the queue.
    PASS and REJECT candidates are excluded.

    Cluster limits are used as investigation-budget controls,
    not as root-cause diagnosis.
    """

    eligible = [
        candidate
        for candidate in candidates
        if candidate.status == CandidateStatus.INVESTIGATE
    ]

    eligible.sort(
        key=lambda candidate: (
            -candidate.priority_score,
            -candidate.confidence,
        )
    )

    if clusters is None:
        return eligible[:max_candidates]

    candidate_to_cluster: dict[str, str] = {}

    for cluster in clusters:
        for candidate_id in cluster.candidate_ids:
            candidate_to_cluster[candidate_id] = cluster.cluster_id

    cluster_counts: dict[str, int] = {}
    queue: list[Candidate] = []

    for candidate in eligible:
        if len(queue) >= max_candidates:
            break

        cluster_id = candidate_to_cluster.get(candidate.candidate_id)

        if cluster_id is not None:
            current_count = cluster_counts.get(cluster_id, 0)

            if current_count >= max_per_cluster:
                continue

            cluster_counts[cluster_id] = current_count + 1

        queue.append(candidate)

    return queue