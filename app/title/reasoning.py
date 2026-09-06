from app.models.reasoning import (
    ReasoningStatus,
    TitleReasoningInput,
    TitleReasoningResult,
)
from app.models.serp import SERPInvestigation
from app.models.serp_decision import (
    SERPDecision,
    SERPDecisionStatus,
)
from app.models.title_recommendation import (
    TitleRecommendation,
)


def build_title_reasoning_input(
    *,
    recommendation: TitleRecommendation,
    investigation: SERPInvestigation,
    decision: SERPDecision,
) -> TitleReasoningInput:
    if decision.status != SERPDecisionStatus.PASS:
        raise ValueError(
            "Strategic title reasoning requires "
            "a PASS SERP decision."
        )

    if (
        recommendation.status.value
        != "recommended"
    ):
        raise ValueError(
            "Strategic title reasoning requires "
            "a recommended title recommendation."
        )

    primary_evidence = None

    if investigation.query_evidence:
        matching = [
            evidence
            for evidence in investigation.query_evidence
            if evidence.query
            == recommendation.primary_query
        ]

        if matching:
            primary_evidence = matching[0]

    if primary_evidence is None:
        raise ValueError(
            "Primary SERP evidence is required "
            "for strategic title reasoning."
        )

    return TitleReasoningInput(
        recommendation_id=recommendation.recommendation_id,
        site_id=recommendation.site_id,
        normalized_url=recommendation.normalized_url,
        primary_query=recommendation.primary_query,
        current_title=recommendation.current_title,
        target_position=primary_evidence.target_position,
        competitor_titles=list(
            primary_evidence.competitor_titles
        ),
        competitor_title_length_median=(
            primary_evidence.competitor_title_length_median
        ),
        competitor_title_length_average=(
            primary_evidence.competitor_title_length_average
        ),
        title_length_gap_vs_competitors=(
            recommendation.title_length_gap_vs_competitors
        ),
        confidence_score=recommendation.confidence_score,
        constraints=list(
            recommendation.constraints
        ),
        evidence={
            "target_found": primary_evidence.target_found,
            "target_position": (
                primary_evidence.target_position
            ),
            "competitor_count": (
                primary_evidence.competitor_count
            ),
            "competitor_titles": list(
                primary_evidence.competitor_titles
            ),
            "competitor_title_lengths": list(
                primary_evidence.competitor_title_lengths
            ),
            "competitor_title_length_median": (
                primary_evidence.competitor_title_length_median
            ),
            "competitor_title_length_average": (
                primary_evidence.competitor_title_length_average
            ),
        },
        snapshot=recommendation.snapshot,
    )


def build_empty_reasoning_result(
    *,
    reasoning_input: TitleReasoningInput,
    reason: str,
) -> TitleReasoningResult:
    return TitleReasoningResult(
        recommendation_id=(
            reasoning_input.recommendation_id
        ),
        status=ReasoningStatus.REJECTED,
        candidates=[],
        selected_candidate_id=None,
        reasons=[reason],
        evidence=dict(reasoning_input.evidence),
        snapshot=reasoning_input.snapshot,
    )