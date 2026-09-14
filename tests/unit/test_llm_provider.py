from datetime import datetime, timezone

import pytest

from app.models.llm import (
    LLMFailureType,
)
from app.models.reasoning import (
    TitleReasoningInput,
)
from app.models.llm_attempt import (
    LLMAttemptStatus,
)
from app.reasoning.attempt_telemetry import (
    AttemptTelemetryRecorder,
)

from app.models.snapshots import SnapshotMetadata
from app.reasoning.provider import (
    ReasoningProviderError,
    StructuredTitleReasoner,
)


def make_input() -> TitleReasoningInput:
    return TitleReasoningInput(
        recommendation_id="recommendation-provider-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        primary_query="کفش مردانه",
        current_title="عنوان فعلی",
        target_position=5,
        competitor_titles=[
            "خرید کفش مردانه",
            "قیمت کفش مردانه",
            "بهترین کفش مردانه",
        ],
        competitor_title_length_median=20.0,
        competitor_title_length_average=20.0,
        title_length_gap_vs_competitors=-10.0,
        confidence_score=1.0,
        constraints=[
            "do_not_copy_competitor_titles",
            "preserve_primary_query_relevance",
        ],
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-provider",
            data_snapshot_id="data-provider",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime(
                2026,
                9,
                6,
                14,
                30,
                tzinfo=timezone.utc,
            ),
        ),
    )


class FakeTransport:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    @property
    def provider_id(self) -> str:
        return "fake-provider"

    def complete(self, *, messages):
        self.calls += 1

        output = self.outputs.pop(0)

        if isinstance(output, Exception):
            raise output

        return output


def valid_output() -> str:
    return """
{
  "candidates": [
    {
      "candidate_id": "candidate-1",
      "title": "خرید کفش مردانه اصل",
      "rationale": "Preserves primary query relevance.",
      "confidence_score": 0.91
    },
    {
      "candidate_id": "candidate-2",
      "title": "کفش مردانه با بهترین قیمت",
      "rationale": "Provides broader commercial relevance.",
      "confidence_score": 0.84
    }
  ]
}
""".strip()


def test_provider_returns_structured_candidates():
    transport = FakeTransport(
        [valid_output()]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport
    )

    candidates = (
        reasoner.generate_title_candidates(
            make_input()
        )
    )

    assert len(candidates) == 2
    assert candidates[0].candidate_id == (
        "candidate-1"
    )
    assert candidates[0].title == (
        "خرید کفش مردانه اصل"
    )
    assert candidates[0].confidence_score == 0.91


def test_provider_id_is_preserved():
    transport = FakeTransport(
        [valid_output()]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport
    )

    assert reasoner.provider_id == (
        "fake-provider"
    )


def test_invalid_json_fails_without_retry():
    transport = FakeTransport(
        ["not-json"]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=3,
    )

    with pytest.raises(
        ReasoningProviderError
    ) as exc_info:
        reasoner.generate_title_candidates(
            make_input()
        )

    assert (
        exc_info.value.failure_type
        == LLMFailureType.INVALID_JSON
    )

    assert exc_info.value.retryable is False
    assert exc_info.value.attempt_count == 1
    assert transport.calls == 1


def test_invalid_schema_fails_without_retry():
    output = """
{
  "candidates": [
    {
      "candidate_id": "candidate-1",
      "title": "عنوان",
      "unexpected": "field",
      "rationale": "test",
      "confidence_score": 0.9
    }
  ]
}
""".strip()

    transport = FakeTransport(
        [output]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=3,
    )

    with pytest.raises(
        ReasoningProviderError
    ) as exc_info:
        reasoner.generate_title_candidates(
            make_input()
        )

    assert (
        exc_info.value.failure_type
        == LLMFailureType.INVALID_SCHEMA
    )

    assert exc_info.value.retryable is False
    assert exc_info.value.attempt_count == 1
    assert transport.calls == 1


def test_empty_output_fails_without_retry():
    transport = FakeTransport(
        [""]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=3,
    )

    with pytest.raises(
        ReasoningProviderError
    ) as exc_info:
        reasoner.generate_title_candidates(
            make_input()
        )

    assert (
        exc_info.value.failure_type
        == LLMFailureType.EMPTY_OUTPUT
    )

    assert exc_info.value.retryable is False
    assert transport.calls == 1


def test_transport_failure_is_retried():
    transport = FakeTransport(
        [
            ConnectionError("temporary"),
            valid_output(),
        ]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=2,
    )

    candidates = (
        reasoner.generate_title_candidates(
            make_input()
        )
    )

    assert len(candidates) == 2
    assert transport.calls == 2


def test_timeout_is_retried():
    transport = FakeTransport(
        [
            TimeoutError(),
            valid_output(),
        ]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=1,
    )

    candidates = (
        reasoner.generate_title_candidates(
            make_input()
        )
    )

    assert len(candidates) == 2
    assert transport.calls == 2


def test_transport_failure_after_retries_is_reported():
    transport = FakeTransport(
        [
            ConnectionError("one"),
            ConnectionError("two"),
            ConnectionError("three"),
        ]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=2,
    )

    with pytest.raises(
        ReasoningProviderError
    ) as exc_info:
        reasoner.generate_title_candidates(
            make_input()
        )

    assert (
        exc_info.value.failure_type
        == LLMFailureType.TRANSPORT
    )

    assert exc_info.value.retryable is True
    assert exc_info.value.attempt_count == 3
    assert transport.calls == 3


def test_zero_retries_means_single_attempt():
    transport = FakeTransport(
        [
            ConnectionError("failure"),
        ]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=0,
    )

    with pytest.raises(
        ReasoningProviderError
    ):
        reasoner.generate_title_candidates(
            make_input()
        )

    assert transport.calls == 1


def test_negative_retries_are_rejected():
    transport = FakeTransport(
        [valid_output()]
    )

    with pytest.raises(
        ValueError,
        match="cannot be negative",
    ):
        StructuredTitleReasoner(
            transport=transport,
            max_retries=-1,
        )


def test_empty_candidate_list_is_valid_structured_output():
    transport = FakeTransport(
        [
            '{"candidates": []}'
        ]
    )

    reasoner = StructuredTitleReasoner(
        transport=transport
    )

    candidates = (
        reasoner.generate_title_candidates(
            make_input()
        )
    )

    assert candidates == []


def test_provider_is_deterministic_for_same_output():
    first_transport = FakeTransport(
        [valid_output()]
    )

    second_transport = FakeTransport(
        [valid_output()]
    )

    first = StructuredTitleReasoner(
        transport=first_transport
    ).generate_title_candidates(
        make_input()
    )

    second = StructuredTitleReasoner(
        transport=second_transport
    ).generate_title_candidates(
        make_input()
    )

    assert [
        candidate.model_dump()
        for candidate in first
    ] == [
        candidate.model_dump()
        for candidate in second
    ]



def test_arvan_failure_metadata_is_preserved_as_reasoning_failure(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.reasoning.provider.time.sleep",
        lambda _: None,
    )

    from app.reasoning.arvan_transport import (
        ArvanTransportError,
    )

    attempt_recorder = AttemptTelemetryRecorder(
        attempt_id_factory=lambda:
        "attempt-auth",
    )

    class FailingTransport(FakeTransport):
        @property
        def provider_id(self):
            return "fake-provider"

        def complete(self, *, messages):
            self.calls += 1
            raise ArvanTransportError(
                failure_type=LLMFailureType.AUTHENTICATION,
                message="auth failed",
                retryable=False,
            )

    transport = FailingTransport([])

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=3,
        attempt_telemetry=attempt_recorder,
    )

    with pytest.raises(
        ReasoningProviderError
    ) as exc_info:
        reasoner.generate_title_candidates(
            make_input()
        )

    assert (
        exc_info.value.failure_type
        == LLMFailureType.AUTHENTICATION
    )
    assert exc_info.value.retryable is False
    assert transport.calls == 1

    assert len(attempt_recorder.events) == 1

    event = attempt_recorder.events[0]

    assert event.failure_type == (
        LLMFailureType.AUTHENTICATION
    )
    assert event.status == (
        LLMAttemptStatus.FAILED
    )


def test_router_context_can_supply_attempt_recorder(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.reasoning.provider.time.sleep",
        lambda _: None,
    )

    attempt_recorder = AttemptTelemetryRecorder(
        attempt_id_factory=iter(
            [
                "attempt-context-1",
                "attempt-context-2",
            ]
        ).__next__
    )

    class FailingThenSuccessTransport:
        def __init__(self):
            self.calls = 0
            self._last_usage = {}
            self._last_response_metadata = {}

        @property
        def provider_id(self):
            return "context-provider"

        @property
        def model(self):
            return "context-model"

        @property
        def last_usage(self):
            return dict(self._last_usage)

        @property
        def last_response_metadata(self):
            return dict(
                self._last_response_metadata
            )

        def complete(self, *, messages):
            self.calls += 1

            if self.calls == 1:
                raise TimeoutError()

            self._last_usage = {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            }

            self._last_response_metadata = {
                "finish_reason": "stop",
            }

            return '{"candidates":[]}'

    from app.reasoning.context import (
        ReasoningExecutionContext,
    )

    transport = FailingThenSuccessTransport()
    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=1,
    )

    context = ReasoningExecutionContext(
        logical_call_id="context-call",
        attempt_telemetry=attempt_recorder,
    )
    context.select_provider(
        "context-provider"
    )

    reasoner.set_execution_context(context)

    result = reasoner.generate_title_candidates(
        make_input()
    )

    assert result == []
    assert len(attempt_recorder.events) == 2
    assert [
        event.attempt_number
        for event in attempt_recorder.events
    ] == [1, 2]
    assert all(
        event.logical_call_id
        == "context-call"
        for event in attempt_recorder.events
    )
    assert attempt_recorder.events[1].total_tokens == 30
