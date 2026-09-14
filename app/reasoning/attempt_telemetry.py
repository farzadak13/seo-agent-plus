from app.observability.context import get_current_context
from app.observability.lifecycle import safe_call
from app.models.observability import ObservabilityEventType, ObservabilityLevel
from collections.abc import Callable
from datetime import datetime

from app.models.llm import LLMFailureType
from app.models.llm_attempt import (
    LLMAttemptStatus,
    LLMAttemptTelemetry,
)
from app.models.reasoning_version import (
    ReasoningVersion,
)


class AttemptTelemetryRecorder:
    def __init__(
        self,
        *,
        attempt_id_factory: Callable[[], str],
    ) -> None:
        self._attempt_id_factory = (
            attempt_id_factory
        )

        self.events: list[
            LLMAttemptTelemetry
        ] = []

    def record(
        self,
        *,
        logical_call_id: str,
        provider_id: str,
        model: str,
        attempt_number: int,
        provider_switch_index: int = 0,
        started_at: datetime,
        completed_at: datetime,
        status: LLMAttemptStatus,
        failure_type: LLMFailureType | None,
        version: ReasoningVersion,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        finish_reason: str | None,
    ) -> LLMAttemptTelemetry:
        latency_ms = (
            completed_at - started_at
        ).total_seconds() * 1000

        event = LLMAttemptTelemetry(
            attempt_id=self._attempt_id_factory(),
            logical_call_id=logical_call_id,
            provider_id=provider_id,
            model=model,
            attempt_number=attempt_number,
            provider_switch_index=(
                provider_switch_index
            ),
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
            status=status,
            failure_type=(
                failure_type.value
                if failure_type is not None
                else None
            ),
            prompt_version=version.prompt_version,
            rule_version=version.rule_version,
            config_version=version.config_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            evidence={},
        )

        self.events.append(event)

        context = get_current_context()
        if context is not None:
            failed = status.value != "success"
            safe_call(
                context.event, event_type=ObservabilityEventType.LLM,
                operation="llm.attempt.failed" if failed else "llm.attempt.completed",
                message="LLM attempt finished.", parent_span_id=context.span_id,
                logical_call_id=logical_call_id, provider_id=provider_id,
                level=ObservabilityLevel.ERROR if failed else ObservabilityLevel.INFO,
                attributes={"attempt_number": attempt_number,
                            "provider_switch_index": provider_switch_index,
                            "failure_type": event.failure_type,
                            "input_tokens": input_tokens, "output_tokens": output_tokens,
                            "total_tokens": total_tokens},
            )
            safe_call(context.increment, "llm.attempt.total",
                      labels={"status": "failed" if failed else "success"})
            safe_call(context.duration, "llm.attempt.duration_ms", max(0, latency_ms),
                      labels={"status": "failed" if failed else "success"})

        return event

