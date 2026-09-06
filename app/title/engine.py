from app.models.serp import SERPInvestigation
from app.models.serp_decision import (
    SERPDecision,
    SERPDecisionStatus,
)
from app.models.title_recommendation import (
    TitleRecommendation,
    TitleRecommendationStatus,
)


def _normalize_title(title: str) -> str:
    return " ".join(title.split())


def _title_length(title: str | None) -> int | None:
    if not title:
        return None

    return len(_normalize_title(title))


def _select_primary_evidence(
    investigation: SERPInvestigation,
):
    if not investigation.query_evidence:
        return None

    ranked = sorted(
        investigation.query_evidence,
        key=lambda evidence: (
            evidence.target_position
            if evidence.target_position is not None
            else 999999,
            -evidence.competitor_count,
        ),
    )

    return ranked[0]


def build_title_recommendation(
    *,
    investigation: SERPInvestigation,
    decision: SERPDecision,
    recommendation_id: str,
) -> TitleRecommendation:
    if decision.status != SERPDecisionStatus.PASS:
        return TitleRecommendation(
            recommendation_id=recommendation_id,
            investigation_id=investigation.investigation_id,
            decision_id=decision.decision_id,
            strategy_id=decision.strategy_id,
            opportunity_id=decision.opportunity_id,
            site_id=investigation.site_id,
            normalized_url=investigation.normalized_url,
            primary_query=(
                investigation.primary_queries[0]
                if investigation.primary_queries
                else ""
            ),
            status=TitleRecommendationStatus.REJECTED,
            current_title=investigation.target_title,
            recommended_title=None,
            current_title_length=_title_length(
                investigation.target_title
            ),
            recommended_title_length=None,
            competitor_title_length_median=None,
            title_length_gap_vs_competitors=None,
            confidence_score=decision.confidence_score,
            reasons=[
                "serp_decision_not_pass",
            ],
            constraints=[
                "title_recommendation_requires_serp_decision_pass",
            ],
            evidence={
                "serp_decision_status": decision.status.value,
            },
            snapshot=decision.snapshot,
        )

    evidence = _select_primary_evidence(
        investigation
    )

    if evidence is None:
        return TitleRecommendation(
            recommendation_id=recommendation_id,
            investigation_id=investigation.investigation_id,
            decision_id=decision.decision_id,
            strategy_id=decision.strategy_id,
            opportunity_id=decision.opportunity_id,
            site_id=investigation.site_id,
            normalized_url=investigation.normalized_url,
            primary_query="",
            status=TitleRecommendationStatus.REJECTED,
            current_title=investigation.target_title,
            recommended_title=None,
            current_title_length=_title_length(
                investigation.target_title
            ),
            recommended_title_length=None,
            competitor_title_length_median=None,
            title_length_gap_vs_competitors=None,
            confidence_score=0.0,
            reasons=[
                "no_serp_query_evidence",
            ],
            constraints=[
                "serp_query_evidence_required",
            ],
            evidence={},
            snapshot=decision.snapshot,
        )

    current_title = (
        _normalize_title(investigation.target_title)
        if investigation.target_title
        else None
    )

    current_title_length = _title_length(
        investigation.target_title
    )

    competitor_median = (
        evidence.competitor_title_length_median
    )

    title_gap = None

    if (
        current_title_length is not None
        and competitor_median is not None
    ):
        title_gap = float(
            current_title_length - competitor_median
        )

    # Stage 9 intentionally does not generate a new title.
    #
    # This layer establishes the evidence-backed
    # recommendation envelope. Actual title generation
    # belongs to the later Strategic/LLM reasoning stage.
    recommended_title = None
    recommended_length = None

    reasons = [
        "serp_evidence_passed",
        "title_generation_deferred_to_strategic_reasoning",
    ]

    constraints = [
        "do_not_generate_title_without_strategic_reasoning",
        "preserve_primary_query_relevance",
        "do_not_copy_competitor_titles",
        "recommendation_must_be_supported_by_serp_evidence",
    ]

    return TitleRecommendation(
        recommendation_id=recommendation_id,
        investigation_id=investigation.investigation_id,
        decision_id=decision.decision_id,
        strategy_id=decision.strategy_id,
        opportunity_id=decision.opportunity_id,
        site_id=investigation.site_id,
        normalized_url=investigation.normalized_url,
        primary_query=evidence.query,
        status=TitleRecommendationStatus.RECOMMENDED,
        current_title=current_title,
        recommended_title=recommended_title,
        current_title_length=current_title_length,
        recommended_title_length=recommended_length,
        competitor_title_length_median=competitor_median,
        title_length_gap_vs_competitors=title_gap,
        confidence_score=decision.confidence_score,
        reasons=reasons,
        constraints=constraints,
        evidence={
            "target_found_in_serp": evidence.target_found,
            "target_position": evidence.target_position,
            "competitor_count": evidence.competitor_count,
            "competitor_title_lengths": (
                evidence.competitor_title_lengths
            ),
            "competitor_title_length_median": (
                evidence.competitor_title_length_median
            ),
            "competitor_title_length_average": (
                evidence.competitor_title_length_average
            ),
        },
        snapshot=decision.snapshot,
    )