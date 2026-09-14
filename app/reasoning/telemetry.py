from collections.abc import Callable
from datetime import datetime

from app.models.llm import LLMFailureType
from app.models.reasoning import TitleReasoningCandidate
from app.models.reasoning_version import ReasoningVersion
from app.models.telemetry import (
    LLMCallStatus,
    LLMCallTelemetry,
)


class TelemetryRecorder:
    def __init__(
        self,
        *,
        event_id_factory: Callable[[], str],
    ) -> None:
        self._event_id_factory = (
            event_id_factory
        )

        self.events: list[
            LLMCallTelemetry
        ] = []

    def record(
        self,
        *,
        provider_id: str,
        model: str,
        version: ReasoningVersion,
        started_at: datetime,
        completed_at: datetime,
        attempt_count: int,
        status: LLMCallStatus,
        failure_type: LLMFailureType | None,
        input_message_count: int,
        input_character_count: int,
        output_character_count: int,
        candidates: list[TitleReasoningCandidate],
        logical_call_id: str | None = None,
        provider_switch_count: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> LLMCallTelemetry:
        if provider_switch_count < 0:
            raise ValueError(
                "provider_switch_count "
                "cannot be negative."
            )

        latency_ms = (
            completed_at - started_at
        ).total_seconds() * 1000

        event_id = self._event_id_factory()

        resolved_logical_call_id = (
            logical_call_id
            if logical_call_id is not None
            else event_id
        )

        event = LLMCallTelemetry(
            event_id=event_id,
            logical_call_id=(
                resolved_logical_call_id
            ),
            provider_id=provider_id,
            model=model,
            prompt_version=(
                version.prompt_version
            ),
            rule_version=(
                version.rule_version
            ),
            config_version=(
                version.config_version
            ),
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
            attempt_count=attempt_count,
            provider_switch_count=(
                provider_switch_count
            ),
            status=status,
            failure_type=(
                failure_type.value
                if failure_type is not None
                else None
            ),
            input_message_count=(
                input_message_count
            ),
            input_character_count=(
                input_character_count
            ),
            output_character_count=(
                output_character_count
            ),
            candidate_count=len(candidates),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            evidence={},
        )

        self.events.append(event)

        return event