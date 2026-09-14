from app.models.reasoning_orchestration import (
    ReasoningOrchestrationStatus,
    TitleReasoningOrchestrationResult,
)
from app.models.serp import (
    SERPInvestigation,
)
from app.models.serp_decision import (
    SERPDecision,
    SERPDecisionStatus,
)
from app.models.title_recommendation import (
    TitleRecommendation,
)
from app.reasoning.router import (
    ReasoningRouter,
)
from app.reasoning.contracts import (
    StrategicReasoner,
)
from app.title.generator import (
    generate_title_reasoning,
)


class RouterReasoner(
    StrategicReasoner,
):
    def __init__(
        self,
        *,
        router: ReasoningRouter,
        provider_ids: list[str] | None,
    ) -> None:
        self._router = router
        self._provider_ids = provider_ids

    @property
    def provider_id(self) -> str:
        return (
            self._router.last_provider_id
            or "router"
        )

    def generate_title_candidates(
        self,
        reasoning_input,
    ):
        return (
            self._router.generate_title_candidates(
                reasoning_input=reasoning_input,
                provider_ids=self._provider_ids,
            )
        )


def run_title_reasoning_orchestration(
    *,
    orchestration_id: str,
    investigation: SERPInvestigation,
    decision: SERPDecision,
    recommendation: TitleRecommendation,
    router: ReasoningRouter,
    provider_ids: list[str] | None = None,
) -> TitleReasoningOrchestrationResult:
    if decision.status != SERPDecisionStatus.PASS:
        return TitleReasoningOrchestrationResult(
            orchestration_id=orchestration_id,
            status=(
                ReasoningOrchestrationStatus.BLOCKED
            ),
            investigation=investigation,
            decision=decision,
            recommendation=recommendation,
            reasoning=None,
            provider_id=None,
            reasons=[
                "serp_decision_not_pass",
            ],
            snapshot=decision.snapshot,
        )

    reasoner = RouterReasoner(
        router=router,
        provider_ids=provider_ids,
    )

    try:
        reasoning = generate_title_reasoning(
            recommendation=recommendation,
            investigation=investigation,
            decision=decision,
            reasoner=reasoner,
        )
    except Exception as exc:
        return TitleReasoningOrchestrationResult(
            orchestration_id=orchestration_id,
            status=(
                ReasoningOrchestrationStatus.FAILED
            ),
            investigation=investigation,
            decision=decision,
            recommendation=recommendation,
            reasoning=None,
            provider_id=router.last_provider_id,
            reasons=[
                "strategic_reasoning_failed",
                str(exc),
            ],
            snapshot=decision.snapshot,
        )

    if not reasoning.candidates:
        status = (
            ReasoningOrchestrationStatus.FAILED
        )
    else:
        status = (
            ReasoningOrchestrationStatus.COMPLETED
        )

    return TitleReasoningOrchestrationResult(
        orchestration_id=orchestration_id,
        status=status,
        investigation=investigation,
        decision=decision,
        recommendation=recommendation,
        reasoning=reasoning,
        provider_id=router.last_provider_id,
        reasons=list(reasoning.reasons),
        snapshot=decision.snapshot,
    )