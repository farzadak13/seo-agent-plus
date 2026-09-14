from app.observability.lifecycle import observe
from app.models.observability import ObservabilityEventType
from datetime import datetime, timezone

from app.models.llm import LLMFailureType
from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)
from app.models.reasoning_version import ReasoningVersion
from app.models.telemetry import LLMCallStatus
from app.reasoning.contracts import StrategicReasoner
from app.reasoning.context import ReasoningExecutionContext
from app.reasoning.telemetry import TelemetryRecorder


class InstrumentedReasoner(StrategicReasoner):
    def __init__(
        self,
        *,
        reasoner: StrategicReasoner,
        model: str,
        version: ReasoningVersion,
        telemetry: TelemetryRecorder,
    ) -> None:
        self._reasoner = reasoner
        self._model = model
        self._version = version
        self._telemetry = telemetry
        self._execution_context: (
            ReasoningExecutionContext | None
        ) = None

    @property
    def provider_id(self) -> str:
        return self._reasoner.provider_id

    def set_execution_context(
        self,
        context: ReasoningExecutionContext | None,
    ) -> None:
        self._execution_context = context

        setter = getattr(
            self._reasoner,
            "set_execution_context",
            None,
        )

        if callable(setter):
            setter(context)

    @observe(ObservabilityEventType.LLM, "llm.call")
    def generate_title_candidates(
        self,
        reasoning_input: TitleReasoningInput,
    ) -> list[TitleReasoningCandidate]:
        started_at = datetime.now(
            timezone.utc
        )

        logical_call_id = (
            "reasoning:"
            f"{reasoning_input.recommendation_id}"
        )

        try:
            candidates = (
                self._reasoner
                .generate_title_candidates(
                    reasoning_input
                )
            )

            completed_at = datetime.now(
                timezone.utc
            )

            if self._execution_context is None:
                self._telemetry.record(
                    logical_call_id=logical_call_id,
                    provider_id=self.provider_id,
                    model=self._model,
                    version=self._version,
                    started_at=started_at,
                    completed_at=completed_at,
                    attempt_count=1,
                    provider_switch_count=0,
                    status=LLMCallStatus.SUCCESS,
                    failure_type=None,
                    input_message_count=0,
                    input_character_count=0,
                    output_character_count=sum(
                        len(candidate.title)
                        for candidate in candidates
                    ),
                    candidates=candidates,
                )

            return candidates

        except Exception as exc:
            completed_at = datetime.now(
                timezone.utc
            )

            failure_type = getattr(
                exc,
                "failure_type",
                None,
            )

            if not isinstance(
                failure_type,
                LLMFailureType,
            ):
                failure_type = None

            if self._execution_context is None:
                self._telemetry.record(
                    logical_call_id=logical_call_id,
                    provider_id=self.provider_id,
                    model=self._model,
                    version=self._version,
                    started_at=started_at,
                    completed_at=completed_at,
                    attempt_count=1,
                    provider_switch_count=0,
                    status=LLMCallStatus.FAILED,
                    failure_type=failure_type,
                    input_message_count=0,
                    input_character_count=0,
                    output_character_count=0,
                    candidates=[],
                )

            raise

