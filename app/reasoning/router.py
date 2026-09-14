from datetime import datetime, timezone
from app.observability.context import get_current_context
from app.observability.lifecycle import lifecycle_span, observe
from app.models.observability import ObservabilityEventType

from app.models.fallback import ProviderFallbackPolicy
from app.models.llm import LLMFailureType
from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)
from app.models.reasoning_version import ReasoningVersion
from app.models.telemetry import LLMCallStatus
from app.reasoning.attempt_telemetry import AttemptTelemetryRecorder
from app.reasoning.context import ReasoningExecutionContext
from app.reasoning.fallback import (
    can_switch_provider,
    should_fallback,
)
from app.reasoning.prompt import PROMPT_VERSION
from app.reasoning.registry import ProviderRegistry
from app.reasoning.telemetry import TelemetryRecorder


class ReasoningRouter:
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        fallback_policy: ProviderFallbackPolicy,
        attempt_telemetry: AttemptTelemetryRecorder | None = None,
        telemetry: TelemetryRecorder | None = None,
    ) -> None:
        self._registry = registry
        self._fallback_policy = fallback_policy
        self._attempt_telemetry = attempt_telemetry
        self._telemetry = telemetry
        self._last_provider_id: str | None = None

    @property
    def last_provider_id(self) -> str | None:
        return self._last_provider_id

    @staticmethod
    def _model_for_reasoner(
        reasoner,
        provider_id: str,
    ) -> str:
        model = getattr(
            reasoner,
            "model",
            None,
        )
        return (
            str(model)
            if model
            else provider_id
        )

    @staticmethod
    def _version(
        reasoning_input: TitleReasoningInput,
    ) -> ReasoningVersion:
        return ReasoningVersion(
            prompt_version=PROMPT_VERSION,
            rule_version=(
                reasoning_input.snapshot.rule_version
            ),
            config_version=(
                reasoning_input.snapshot.config_version
            ),
        )

    def _record_call(
        self,
        *,
        context: ReasoningExecutionContext,
        reasoning_input: TitleReasoningInput,
        provider_id: str,
        model: str,
        started_at: datetime,
        completed_at: datetime,
        status: LLMCallStatus,
        failure_type: LLMFailureType | None,
        candidates: list[TitleReasoningCandidate],
    ) -> None:
        if self._telemetry is None:
            return

        self._telemetry.record(
            logical_call_id=context.logical_call_id,
            provider_id=provider_id,
            model=model,
            version=self._version(
                reasoning_input
            ),
            started_at=started_at,
            completed_at=completed_at,
            attempt_count=max(
                context.attempt_number,
                1,
            ),
            provider_switch_count=(
                context.provider_switch_count
            ),
            status=status,
            failure_type=failure_type,
            input_message_count=(
                context.input_message_count
            ),
            input_character_count=(
                context.input_character_count
            ),
            output_character_count=(
                context.output_character_count
            ),
            candidates=candidates,
            input_tokens=(
                context.input_tokens or None
            ),
            output_tokens=(
                context.output_tokens or None
            ),
            total_tokens=(
                context.total_tokens or None
            ),
        )

    @observe(ObservabilityEventType.LLM, "llm.generate")
    def generate_title_candidates(
        self,
        *,
        reasoning_input: TitleReasoningInput,
        provider_ids: list[str] | None = None,
        logical_call_id: str | None = None,
    ) -> list[TitleReasoningCandidate]:
        providers = (
            provider_ids
            if provider_ids is not None
            else [
                registration.provider_id
                for registration
                in self._registry.registrations()
                if registration.enabled
            ]
        )

        if not providers:
            raise ValueError(
                "No reasoning providers are available."
            )

        self._last_provider_id = None

        context = ReasoningExecutionContext(
            logical_call_id=(
                logical_call_id
                if logical_call_id is not None
                else (
                    "reasoning:"
                    f"{reasoning_input.recommendation_id}"
                )
            ),
            attempt_telemetry=(
                self._attempt_telemetry
            ),
        )

        call_started_at = datetime.now(
            timezone.utc
        )

        for index, provider_id in enumerate(
            providers
        ):
            reasoner = self._registry.get(
                provider_id
            )

            switch_index = (
                context.select_provider(
                    provider_id
                )
            )

            set_context = getattr(
                reasoner,
                "set_execution_context",
                None,
            )

            context_aware = callable(
                set_context
            )

            if context_aware:
                set_context(context)
            else:
                context.next_attempt()

            model = self._model_for_reasoner(
                reasoner,
                provider_id,
            )

            try:
                with lifecycle_span(
                    get_current_context(), event_type=ObservabilityEventType.LLM,
                    operation="llm.provider", provider_id=provider_id,
                    logical_call_id=context.logical_call_id,
                    attributes={"provider_switch_count": switch_index},
                ):
                    candidates = reasoner.generate_title_candidates(reasoning_input)

                self._last_provider_id = (
                    provider_id
                )

                self._record_call(
                    context=context,
                    reasoning_input=reasoning_input,
                    provider_id=provider_id,
                    model=model,
                    started_at=call_started_at,
                    completed_at=datetime.now(
                        timezone.utc
                    ),
                    status=LLMCallStatus.SUCCESS,
                    failure_type=None,
                    candidates=candidates,
                )

                return candidates

            except Exception as exc:
                failure_type = getattr(
                    exc,
                    "failure_type",
                    None,
                )

                if not isinstance(
                    failure_type,
                    LLMFailureType,
                ):
                    if self._telemetry is not None:
                        self._record_call(
                            context=context,
                            reasoning_input=reasoning_input,
                            provider_id=provider_id,
                            model=model,
                            started_at=call_started_at,
                            completed_at=datetime.now(
                                timezone.utc
                            ),
                            status=LLMCallStatus.FAILED,
                            failure_type=None,
                            candidates=[],
                        )
                    raise

                context.observe_failure(
                    failure_type
                )

                fallback_allowed = (
                    should_fallback(
                        failure_type=failure_type,
                        policy=self._fallback_policy,
                    )
                    and can_switch_provider(
                        switch_count=switch_index,
                        policy=self._fallback_policy,
                    )
                    and index < len(providers) - 1
                )

                if not fallback_allowed:
                    self._record_call(
                        context=context,
                        reasoning_input=reasoning_input,
                        provider_id=provider_id,
                        model=model,
                        started_at=call_started_at,
                        completed_at=datetime.now(
                            timezone.utc
                        ),
                        status=LLMCallStatus.FAILED,
                        failure_type=failure_type,
                        candidates=[],
                    )
                    raise

            finally:
                if context_aware:
                    set_context(None)

        raise RuntimeError(
            "Reasoning router failed unexpectedly."
        )

