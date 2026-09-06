from app.models.reasoning import (
    ReasoningStatus,
    TitleReasoningCandidate,
    TitleReasoningResult,
)
from app.models.title_recommendation import (
    TitleRecommendation,
)
from app.reasoning.contracts import StrategicReasoner
from app.reasoning.validator import (
    validate_title_candidate,
)
from app.title.reasoning import (
    build_title_reasoning_input,
)


def generate_title_reasoning(
    *,
    recommendation: TitleRecommendation,
    investigation,
    decision,
    reasoner: StrategicReasoner,
) -> TitleReasoningResult:
    reasoning_input = build_title_reasoning_input(
        recommendation=recommendation,
        investigation=investigation,
        decision=decision,
    )

    raw_candidates = (
        reasoner.generate_title_candidates(
            reasoning_input
        )
    )

    accepted_candidates: list[
        TitleReasoningCandidate
    ] = []

    rejected_count = 0

    for candidate in raw_candidates:
        errors = validate_title_candidate(
            candidate=candidate,
            reasoning_input=reasoning_input,
        )

        if errors:
            rejected_count += 1
            continue

        accepted_candidates.append(candidate)

    if not accepted_candidates:
        return TitleReasoningResult(
            recommendation_id=(
                recommendation.recommendation_id
            ),
            status=ReasoningStatus.REJECTED,
            candidates=[],
            selected_candidate_id=None,
            reasons=[
                "all_llm_candidates_failed_validation",
            ],
            evidence={
                "provider_id": reasoner.provider_id,
                "raw_candidate_count": len(
                    raw_candidates
                ),
                "rejected_candidate_count": (
                    rejected_count
                ),
            },
            snapshot=recommendation.snapshot,
        )

    # Deterministic selection:
    # highest confidence first, then original
    # provider order is preserved for ties.
    selected = max(
        enumerate(accepted_candidates),
        key=lambda item: (
            item[1].confidence_score,
            -item[0],
        ),
    )[1]

    return TitleReasoningResult(
        recommendation_id=(
            recommendation.recommendation_id
        ),
        status=ReasoningStatus.READY,
        candidates=accepted_candidates,
        selected_candidate_id=(
            selected.candidate_id
        ),
        reasons=[
            "validated_strategic_reasoning_candidates",
        ],
        evidence={
            "provider_id": reasoner.provider_id,
            "raw_candidate_count": len(
                raw_candidates
            ),
            "accepted_candidate_count": len(
                accepted_candidates
            ),
            "rejected_candidate_count": (
                rejected_count
            ),
        },
        snapshot=recommendation.snapshot,
    )