from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.llm import LLMFailureType
from app.models.reasoning import TitleReasoningCandidate

if TYPE_CHECKING:
    from app.models.llm import LLMMessage
    from app.reasoning.attempt_telemetry import AttemptTelemetryRecorder


@dataclass
class ReasoningExecutionContext:
    logical_call_id: str
    attempt_telemetry: "AttemptTelemetryRecorder | None" = None

    attempt_number: int = 0
    provider_switch_count: int = 0
    current_provider_id: str | None = None

    input_message_count: int = 0
    input_character_count: int = 0
    output_character_count: int = 0
    candidate_count: int = 0

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    last_failure_type: LLMFailureType | None = None
    last_finish_reason: str | None = None

    def select_provider(self, provider_id: str) -> int:
        if self.current_provider_id is None:
            self.current_provider_id = provider_id
            return self.provider_switch_count

        if self.current_provider_id != provider_id:
            self.provider_switch_count += 1
            self.current_provider_id = provider_id

        return self.provider_switch_count

    def next_attempt(self) -> int:
        self.attempt_number += 1
        return self.attempt_number

    def observe_input(self, messages: list["LLMMessage"]) -> None:
        if self.input_message_count != 0:
            return

        self.input_message_count = len(messages)
        self.input_character_count = sum(
            len(message.content)
            for message in messages
        )

    def observe_attempt_usage(self, usage: dict) -> None:
        if not isinstance(usage, dict):
            return

        input_tokens = usage.get("prompt_tokens")
        if input_tokens is None:
            input_tokens = usage.get("input_tokens")

        output_tokens = usage.get("completion_tokens")
        if output_tokens is None:
            output_tokens = usage.get("output_tokens")

        total_tokens = usage.get("total_tokens")

        if isinstance(input_tokens, int) and input_tokens >= 0:
            self.input_tokens += input_tokens

        if isinstance(output_tokens, int) and output_tokens >= 0:
            self.output_tokens += output_tokens

        if isinstance(total_tokens, int) and total_tokens >= 0:
            self.total_tokens += total_tokens

    def observe_success(
        self,
        candidates: list[TitleReasoningCandidate],
        *,
        finish_reason: str | None,
    ) -> None:
        self.output_character_count = sum(
            len(candidate.title)
            for candidate in candidates
        )
        self.candidate_count = len(candidates)
        self.last_finish_reason = finish_reason
        self.last_failure_type = None

    def observe_failure(self, failure_type: LLMFailureType) -> None:
        self.last_failure_type = failure_type
