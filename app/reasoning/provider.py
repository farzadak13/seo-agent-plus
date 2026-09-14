import json
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from app.models.llm import (
    LLMFailureType,
    TitleCandidatePayloadList,
)
from app.models.llm_attempt import LLMAttemptStatus
from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)
from app.models.reasoning_version import ReasoningVersion
from app.reasoning.attempt_telemetry import AttemptTelemetryRecorder
from app.reasoning.contracts import StrategicReasoner
from app.reasoning.context import ReasoningExecutionContext
from app.reasoning.prompt import (
    PROMPT_VERSION,
    build_title_reasoning_prompt,
)
from app.reasoning.transport import LLMTransport


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


class StructuredTitleReasoner(StrategicReasoner):
    def __init__(
        self,
        *,
        transport: LLMTransport,
        max_retries: int = 2,
        attempt_telemetry: AttemptTelemetryRecorder | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError(
                "max_retries cannot be negative."
            )

        self._transport = transport
        self._max_retries = max_retries
        self._attempt_telemetry = attempt_telemetry
        self._execution_context: (
            ReasoningExecutionContext | None
        ) = None

    @property
    def provider_id(self) -> str:
        return self._transport.provider_id

    @property
    def model(self) -> str:
        return str(
            getattr(
                self._transport,
                "model",
                self.provider_id,
            )
        )

    def set_execution_context(
        self,
        context: ReasoningExecutionContext | None,
    ) -> None:
        self._execution_context = context

    def _version(
        self,
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

    def _usage(self) -> dict:
        usage = getattr(
            self._transport,
            "last_usage",
            {},
        )
        return (
            dict(usage)
            if isinstance(usage, dict)
            else {}
        )

    def _finish_reason(self) -> str | None:
        metadata = getattr(
            self._transport,
            "last_response_metadata",
            {},
        )

        if not isinstance(metadata, dict):
            return None

        value = metadata.get("finish_reason")
        return value if isinstance(value, str) else None

    @staticmethod
    def _token_value(
        usage: dict,
        primary_key: str,
        fallback_key: str | None = None,
    ) -> int | None:
        value = usage.get(primary_key)

        if value is None and fallback_key is not None:
            value = usage.get(fallback_key)

        if isinstance(value, int) and value >= 0:
            return value

        return None

    def _record_attempt(
        self,
        *,
        reasoning_input: TitleReasoningInput,
        attempt_number: int,
        provider_switch_index: int,
        started_at: datetime,
        completed_at: datetime,
        status: LLMAttemptStatus,
        failure_type: LLMFailureType | None,
        usage: dict,
        finish_reason: str | None,
    ) -> None:
        context = self._execution_context

        recorder = (
            context.attempt_telemetry
            if (
                context is not None
                and context.attempt_telemetry is not None
            )
            else self._attempt_telemetry
        )

        if recorder is None:
            return

        logical_call_id = (
            context.logical_call_id
            if context is not None
            else (
                "reasoning:"
                f"{reasoning_input.recommendation_id}"
            )
        )

        recorder.record(
            logical_call_id=logical_call_id,
            provider_id=self.provider_id,
            model=self.model,
            attempt_number=attempt_number,
            provider_switch_index=provider_switch_index,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            failure_type=failure_type,
            version=self._version(reasoning_input),
            input_tokens=self._token_value(
                usage,
                "prompt_tokens",
                "input_tokens",
            ),
            output_tokens=self._token_value(
                usage,
                "completion_tokens",
                "output_tokens",
            ),
            total_tokens=self._token_value(
                usage,
                "total_tokens",
            ),
            finish_reason=finish_reason,
        )

    def generate_title_candidates(
        self,
        reasoning_input: TitleReasoningInput,
    ) -> list[TitleReasoningCandidate]:
        messages = build_title_reasoning_prompt(
            reasoning_input
        )

        context = self._execution_context

        if context is not None:
            context.observe_input(messages)

        last_error: ReasoningProviderError | None = None
        max_attempts = self._max_retries + 1

        for local_attempt in range(
            1,
            max_attempts + 1,
        ):
            attempt_number = (
                context.next_attempt()
                if context is not None
                else local_attempt
            )

            provider_switch_index = (
                context.provider_switch_count
                if context is not None
                else 0
            )

            started_at = datetime.now(timezone.utc)
            usage: dict = {}
            finish_reason: str | None = None

            try:
                raw_output = self._transport.complete(
                    messages=messages
                )

                usage = self._usage()

                if context is not None:
                    context.observe_attempt_usage(
                        usage
                    )

                finish_reason = self._finish_reason()

                if not raw_output.strip():
                    raise ReasoningProviderError(
                        failure_type=LLMFailureType.EMPTY_OUTPUT,
                        message="LLM returned empty output.",
                        retryable=False,
                        attempt_count=local_attempt,
                    )

                try:
                    payload = json.loads(raw_output)
                except json.JSONDecodeError as exc:
                    raise ReasoningProviderError(
                        failure_type=LLMFailureType.INVALID_JSON,
                        message="LLM returned invalid JSON.",
                        retryable=False,
                        attempt_count=local_attempt,
                    ) from exc

                try:
                    structured = (
                        TitleCandidatePayloadList.model_validate(
                            payload
                        )
                    )
                except ValidationError as exc:
                    raise ReasoningProviderError(
                        failure_type=LLMFailureType.INVALID_SCHEMA,
                        message=(
                            "LLM output failed structured "
                            "schema validation."
                        ),
                        retryable=False,
                        attempt_count=local_attempt,
                    ) from exc

                candidates = [
                    TitleReasoningCandidate(
                        candidate_id=candidate.candidate_id,
                        title=candidate.title,
                        rationale=candidate.rationale,
                        confidence_score=candidate.confidence_score,
                    )
                    for candidate in structured.candidates
                ]

                completed_at = datetime.now(timezone.utc)

                self._record_attempt(
                    reasoning_input=reasoning_input,
                    attempt_number=attempt_number,
                    provider_switch_index=provider_switch_index,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=LLMAttemptStatus.SUCCESS,
                    failure_type=None,
                    usage=usage,
                    finish_reason=finish_reason,
                )

                if context is not None:
                    context.observe_success(
                        candidates,
                        finish_reason=finish_reason,
                    )

                return candidates

            except ReasoningProviderError as exc:
                completed_at = datetime.now(timezone.utc)

                if context is not None:
                    context.observe_failure(
                        exc.failure_type
                    )

                self._record_attempt(
                    reasoning_input=reasoning_input,
                    attempt_number=attempt_number,
                    provider_switch_index=provider_switch_index,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=LLMAttemptStatus.FAILED,
                    failure_type=exc.failure_type,
                    usage=usage,
                    finish_reason=finish_reason,
                )

                if not exc.retryable:
                    raise

                last_error = exc

                if local_attempt >= max_attempts:
                    raise ReasoningProviderError(
                        failure_type=exc.failure_type,
                        message=str(exc),
                        retryable=True,
                        attempt_count=local_attempt,
                    ) from exc

            except Exception as exc:
                failure_type = getattr(
                    exc,
                    "failure_type",
                    LLMFailureType.TRANSPORT,
                )

                if not isinstance(
                    failure_type,
                    LLMFailureType,
                ):
                    failure_type = (
                        LLMFailureType.TRANSPORT
                    )

                retryable = getattr(
                    exc,
                    "retryable",
                    True,
                )

                if not isinstance(
                    retryable,
                    bool,
                ):
                    retryable = True

                wrapped = ReasoningProviderError(
                    failure_type=failure_type,
                    message=str(exc)
                    or "LLM transport failed.",
                    retryable=retryable,
                    attempt_count=local_attempt,
                )

                completed_at = datetime.now(timezone.utc)

                if context is not None:
                    context.observe_failure(
                        wrapped.failure_type
                    )

                self._record_attempt(
                    reasoning_input=reasoning_input,
                    attempt_number=attempt_number,
                    provider_switch_index=provider_switch_index,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=LLMAttemptStatus.FAILED,
                    failure_type=wrapped.failure_type,
                    usage={},
                    finish_reason=None,
                )

                last_error = wrapped

                if not wrapped.retryable:
                    raise wrapped from exc

                if local_attempt >= max_attempts:
                    raise wrapped from exc

            time.sleep(
                min(
                    2 ** local_attempt,
                    30.0,
                )
            )

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "Reasoning failed unexpectedly."
        )
