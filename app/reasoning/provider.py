import json

from pydantic import ValidationError

from app.models.llm import (
    LLMFailureType,
    TitleCandidatePayloadList,
)
from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)
from app.reasoning.contracts import (
    StrategicReasoner,
)
from app.reasoning.prompt import (
    build_title_reasoning_prompt,
)
from app.reasoning.transport import (
    LLMTransport,
)


class ReasoningProviderError(RuntimeError):
    def __init__(
        self,
        *,
        failure_type: LLMFailureType,
        message: str,
        retryable: bool,
        attempt_count: int,
    ) -> None:
        self.failure_type = failure_type
        self.retryable = retryable
        self.attempt_count = attempt_count

        super().__init__(message)


class StructuredTitleReasoner(
    StrategicReasoner,
):
    def __init__(
        self,
        *,
        transport: LLMTransport,
        max_retries: int = 2,
    ) -> None:
        if max_retries < 0:
            raise ValueError(
                "max_retries cannot be negative."
            )

        self._transport = transport
        self._max_retries = max_retries

    @property
    def provider_id(self) -> str:
        return self._transport.provider_id

    def generate_title_candidates(
        self,
        reasoning_input: TitleReasoningInput,
    ) -> list[TitleReasoningCandidate]:
        messages = build_title_reasoning_prompt(
            reasoning_input
        )

        last_error: (
            ReasoningProviderError | None
        ) = None

        max_attempts = (
            self._max_retries + 1
        )

        for attempt in range(
            1,
            max_attempts + 1,
        ):
            try:
                raw_output = (
                    self._transport.complete(
                        messages=messages
                    )
                )

                if not raw_output.strip():
                    raise ReasoningProviderError(
                        failure_type=(
                            LLMFailureType.EMPTY_OUTPUT
                        ),
                        message=(
                            "LLM returned empty output."
                        ),
                        retryable=False,
                        attempt_count=attempt,
                    )

                try:
                    payload = json.loads(
                        raw_output
                    )
                except json.JSONDecodeError as exc:
                    raise ReasoningProviderError(
                        failure_type=(
                            LLMFailureType.INVALID_JSON
                        ),
                        message=(
                            "LLM returned invalid JSON."
                        ),
                        retryable=False,
                        attempt_count=attempt,
                    ) from exc

                try:
                    structured = (
                        TitleCandidatePayloadList.model_validate(
                            payload
                        )
                    )
                except ValidationError as exc:
                    raise ReasoningProviderError(
                        failure_type=(
                            LLMFailureType.INVALID_SCHEMA
                        ),
                        message=(
                            "LLM output failed "
                            "structured schema validation."
                        ),
                        retryable=False,
                        attempt_count=attempt,
                    ) from exc

                return [
                    TitleReasoningCandidate(
                        candidate_id=(
                            candidate.candidate_id
                        ),
                        title=candidate.title,
                        rationale=candidate.rationale,
                        confidence_score=(
                            candidate.confidence_score
                        ),
                    )
                    for candidate in structured.candidates
                ]

            except ReasoningProviderError as exc:
                if not exc.retryable:
                    raise

                last_error = exc

                if attempt >= max_attempts:
                    raise ReasoningProviderError(
                        failure_type=(
                            exc.failure_type
                        ),
                        message=str(exc),
                        retryable=True,
                        attempt_count=attempt,
                    ) from exc

            except Exception as exc:
                last_error = (
                    ReasoningProviderError(
                        failure_type=(
                            LLMFailureType.TRANSPORT
                        ),
                        message=(
                            "LLM transport failed."
                        ),
                        retryable=True,
                        attempt_count=attempt,
                    )
                )

                if attempt >= max_attempts:
                    raise last_error from exc

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "Reasoning failed unexpectedly."
        )