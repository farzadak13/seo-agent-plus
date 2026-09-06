import hashlib
from collections import defaultdict
from collections.abc import Sequence

from app.models.candidates import Candidate
from app.models.clusters import CandidateCluster


def build_candidate_fingerprint(candidate: Candidate) -> str:
    """
    Build a deterministic fingerprint from the candidate's
    signal/risk pattern.

    This is NOT a root-cause fingerprint.
    """

    signal_types = tuple(sorted(candidate.signal_types))
    risk_types = tuple(sorted(candidate.risk_types))

    raw = "|".join(
        [
            *signal_types,
            "--RISKS--",
            *risk_types,
        ]
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def cluster_candidates(
    candidates: Sequence[Candidate],
) -> list[CandidateCluster]:
    """
    Group candidates by deterministic signal/risk fingerprint.

    Exact duplicate candidates are not automatically removed here;
    that is handled by deduplicate_candidates().
    """

    groups: dict[str, list[Candidate]] = defaultdict(list)

    for candidate in candidates:
        fingerprint = build_candidate_fingerprint(candidate)
        groups[fingerprint].append(candidate)

    clusters: list[CandidateCluster] = []

    for fingerprint, members in groups.items():
        representative = max(
            members,
            key=lambda candidate: candidate.priority_score,
        )

        cluster_id = f"cluster-{fingerprint[:16]}"

        clusters.append(
            CandidateCluster(
                cluster_id=cluster_id,
                fingerprint=fingerprint,
                candidate_ids=[
                    candidate.candidate_id
                    for candidate in members
                ],
                candidate_count=len(members),
                representative_priority_score=(
                    representative.priority_score
                ),
            )
        )

    return sorted(
        clusters,
        key=lambda cluster: cluster.representative_priority_score,
        reverse=True,
    )


def deduplicate_candidates(
    candidates: Sequence[Candidate],
) -> list[Candidate]:
    """
    Remove exact duplicate candidates.

    The candidate with the highest priority score is retained.
    """

    groups: dict[tuple[str, str, str], Candidate] = {}

    for candidate in candidates:
        key = (
            candidate.site_id,
            candidate.normalized_url,
            candidate.normalized_query,
        )

        existing = groups.get(key)

        if existing is None or (
            candidate.priority_score
            > existing.priority_score
        ):
            groups[key] = candidate

    return sorted(
        groups.values(),
        key=lambda candidate: candidate.priority_score,
        reverse=True,
    )