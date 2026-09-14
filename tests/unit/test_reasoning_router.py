import pytest

from app.models.fallback import ProviderFallbackPolicy
from app.models.llm import LLMFailureType
from app.models.providers import ProviderRegistration
from app.models.reasoning import TitleReasoningCandidate, TitleReasoningInput
from app.models.llm_attempt import LLMAttemptStatus
from app.reasoning.attempt_telemetry import AttemptTelemetryRecorder
from app.reasoning.context import ReasoningExecutionContext
from app.reasoning.provider import StructuredTitleReasoner
from app.reasoning.telemetry import TelemetryRecorder
from app.models.snapshots import SnapshotMetadata
from app.reasoning.registry import ProviderRegistry
from app.reasoning.router import ReasoningRouter


class RetryableFailure(RuntimeError):
    failure_type = LLMFailureType.TIMEOUT


class AuthenticationFailure(RuntimeError):
    failure_type = LLMFailureType.AUTHENTICATION


class FakeReasoner:
    def __init__(
        self,
        *,
        provider_id,
        candidates=None,
        failure=None,
    ):
        self._provider_id = provider_id
        self._candidates = candidates or []
        self._failure = failure

    @property
    def provider_id(self):
        return self._provider_id

    def generate_title_candidates(
        self,
        reasoning_input,
    ):
        if self._failure is not None:
            raise self._failure

        return self._candidates


def make_input():
    from datetime import datetime, timezone

    return TitleReasoningInput(
        recommendation_id="router-001",
        site_id="site-1",
        normalized_url="https://example.com",
        primary_query="کفش مردانه",
        current_title="عنوان",
        target_position=5,
        competitor_titles=[
            "رقیب",
        ],
        competitor_title_length_median=10,
        competitor_title_length_average=10,
        title_length_gap_vs_competitors=1,
        confidence_score=0.9,
        constraints=[],
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-router",
            data_snapshot_id="data-router",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime.now(timezone.utc),
        ),
    )


def make_registry(providers):
    registry = ProviderRegistry()

    for provider_id, reasoner in providers:
        registry.register(
            registration=ProviderRegistration(
                provider_id=provider_id,
                display_name=provider_id,
                enabled=True,
                priority=1,
                model="test-model",
                config_version="config-v1",
            ),
            reasoner=reasoner,
        )

    return registry


def test_first_provider_is_used():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-1",
        title="خرید کفش مردانه",
        rationale="test",
        confidence_score=0.9,
    )

    first = FakeReasoner(
        provider_id="first",
        candidates=[candidate],
    )

    second = FakeReasoner(
        provider_id="second",
        candidates=[],
    )

    router = ReasoningRouter(
        registry=make_registry(
            [
                ("first", first),
                ("second", second),
            ]
        ),
        fallback_policy=ProviderFallbackPolicy(),
    )

    result = router.generate_title_candidates(
        reasoning_input=make_input(),
        provider_ids=[
            "first",
            "second",
        ],
    )

    assert result == [candidate]
    assert router.last_provider_id == "first"


def test_timeout_falls_back_to_next_provider():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-2",
        title="خرید کفش مردانه اصل",
        rationale="fallback",
        confidence_score=0.8,
    )

    first = FakeReasoner(
        provider_id="first",
        failure=RetryableFailure(),
    )

    second = FakeReasoner(
        provider_id="second",
        candidates=[candidate],
    )

    router = ReasoningRouter(
        registry=make_registry(
            [
                ("first", first),
                ("second", second),
            ]
        ),
        fallback_policy=ProviderFallbackPolicy(),
    )

    result = router.generate_title_candidates(
        reasoning_input=make_input(),
        provider_ids=[
            "first",
            "second",
        ],
    )

    assert result == [candidate]
    assert router.last_provider_id == "second"


def test_authentication_failure_does_not_fallback():
    first = FakeReasoner(
        provider_id="first",
        failure=AuthenticationFailure(),
    )

    second = FakeReasoner(
        provider_id="second",
        candidates=[],
    )

    router = ReasoningRouter(
        registry=make_registry(
            [
                ("first", first),
                ("second", second),
            ]
        ),
        fallback_policy=ProviderFallbackPolicy(),
    )

    with pytest.raises(AuthenticationFailure):
        router.generate_title_candidates(
            reasoning_input=make_input(),
            provider_ids=[
                "first",
                "second",
            ],
        )

    assert router.last_provider_id is None


def test_fallback_limit_is_respected():
    first = FakeReasoner(
        provider_id="first",
        failure=RetryableFailure(),
    )

    second = FakeReasoner(
        provider_id="second",
        failure=RetryableFailure(),
    )

    third = FakeReasoner(
        provider_id="third",
        candidates=[],
    )

    router = ReasoningRouter(
        registry=make_registry(
            [
                ("first", first),
                ("second", second),
                ("third", third),
            ]
        ),
        fallback_policy=ProviderFallbackPolicy(
            max_provider_switches=1,
        ),
    )

    with pytest.raises(RetryableFailure):
        router.generate_title_candidates(
            reasoning_input=make_input(),
            provider_ids=[
                "first",
                "second",
                "third",
            ],
        )


def test_disabled_fallback_stops_after_first_failure():
    first = FakeReasoner(
        provider_id="first",
        failure=RetryableFailure(),
    )

    second = FakeReasoner(
        provider_id="second",
        candidates=[],
    )

    router = ReasoningRouter(
        registry=make_registry(
            [
                ("first", first),
                ("second", second),
            ]
        ),
        fallback_policy=ProviderFallbackPolicy(
            mode="disabled",
        ),
    )

    with pytest.raises(RetryableFailure):
        router.generate_title_candidates(
            reasoning_input=make_input(),
            provider_ids=[
                "first",
                "second",
            ],
        )



class SequenceTransport:
    def __init__(
        self,
        *,
        provider_id: str,
        outputs: list,
        usages=None,
        response_metadata=None,
    ):
        self._provider_id = provider_id
        self._outputs = list(outputs)
        self._usages = list(
            usages
            if usages is not None
            else [{} for _ in outputs]
        )
        self._metadata = list(
            response_metadata
            if response_metadata is not None
            else [{} for _ in outputs]
        )

        self._last_usage = {}
        self._last_response_metadata = {}

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return f"{self._provider_id}-model"

    @property
    def last_usage(self) -> dict:
        return dict(self._last_usage)

    @property
    def last_response_metadata(self) -> dict:
        return dict(
            self._last_response_metadata
        )

    def complete(self, *, messages):
        output = self._outputs.pop(0)
        self._last_usage = self._usages.pop(0)
        self._last_response_metadata = (
            self._metadata.pop(0)
        )

        if isinstance(output, Exception):
            raise output

        return output


def test_router_attempt_telemetry_tracks_retry_and_provider_switch(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.reasoning.provider.time.sleep",
        lambda _: None,
    )

    attempt_recorder = AttemptTelemetryRecorder(
        attempt_id_factory=iter(
            [
                "attempt-a-1",
                "attempt-a-2",
                "attempt-b-1",
            ]
        ).__next__
    )

    call_recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "call-001"
    )

    first = StructuredTitleReasoner(
        transport=SequenceTransport(
            provider_id="provider-a",
            outputs=[
                TimeoutError(),
                TimeoutError(),
            ],
        ),
        max_retries=1,
    )

    second = StructuredTitleReasoner(
        transport=SequenceTransport(
            provider_id="provider-b",
            outputs=[
                '{"candidates":[{"candidate_id":"b-1","title":"خرید کفش مردانه","rationale":"fallback","confidence_score":0.9}]}',
            ],
            usages=[
                {
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_tokens": 200,
                }
            ],
            response_metadata=[
                {
                    "finish_reason": "stop",
                }
            ],
        ),
        max_retries=0,
    )

    router = ReasoningRouter(
        registry=make_registry(
            [
                ("provider-a", first),
                ("provider-b", second),
            ]
        ),
        fallback_policy=ProviderFallbackPolicy(),
        attempt_telemetry=attempt_recorder,
        telemetry=call_recorder,
    )

    result = router.generate_title_candidates(
        reasoning_input=make_input(),
        provider_ids=[
            "provider-a",
            "provider-b",
        ],
        logical_call_id="logical-router-001",
    )

    assert len(result) == 1
    assert router.last_provider_id == (
        "provider-b"
    )

    assert len(attempt_recorder.events) == 3

    assert [
        event.attempt_number
        for event in attempt_recorder.events
    ] == [1, 2, 3]

    assert [
        event.provider_id
        for event in attempt_recorder.events
    ] == [
        "provider-a",
        "provider-a",
        "provider-b",
    ]

    assert [
        event.provider_switch_index
        for event in attempt_recorder.events
    ] == [0, 0, 1]

    assert [
        event.status
        for event in attempt_recorder.events
    ] == [
        LLMAttemptStatus.FAILED,
        LLMAttemptStatus.FAILED,
        LLMAttemptStatus.SUCCESS,
    ]

    assert all(
        event.logical_call_id
        == "logical-router-001"
        for event in attempt_recorder.events
    )

    assert len(call_recorder.events) == 1

    call = call_recorder.events[0]

    assert call.logical_call_id == (
        "logical-router-001"
    )
    assert call.provider_id == "provider-b"
    assert call.attempt_count == 3
    assert call.provider_switch_count == 1
    assert call.status.value == "success"
    assert call.input_tokens == 120
    assert call.output_tokens == 80
    assert call.total_tokens == 200


def test_router_resets_last_provider_on_new_failed_call():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-reset",
        title="خرید کفش مردانه",
        rationale="reset",
        confidence_score=0.9,
    )

    reasoner = FakeReasoner(
        provider_id="first",
        candidates=[candidate],
    )

    router = ReasoningRouter(
        registry=make_registry(
            [("first", reasoner)]
        ),
        fallback_policy=ProviderFallbackPolicy(),
    )

    first = router.generate_title_candidates(
        reasoning_input=make_input(),
        provider_ids=["first"],
    )

    assert first == [candidate]
    assert router.last_provider_id == "first"

    reasoner._failure = (
        AuthenticationFailure()
    )

    with pytest.raises(AuthenticationFailure):
        router.generate_title_candidates(
            reasoning_input=make_input(),
            provider_ids=["first"],
        )

    assert router.last_provider_id is None


def test_context_switch_index_increments_only_on_provider_change():
    context = ReasoningExecutionContext(
        logical_call_id="context-001"
    )

    assert context.select_provider(
        "provider-a"
    ) == 0

    context.next_attempt()

    assert context.select_provider(
        "provider-a"
    ) == 0

    context.next_attempt()

    assert context.select_provider(
        "provider-b"
    ) == 1

    context.next_attempt()

    assert context.select_provider(
        "provider-b"
    ) == 1

    assert context.provider_switch_count == 1
