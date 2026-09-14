from datetime import datetime, timezone

from app.models.reasoning import (
    TitleReasoningCandidate,
)
from app.models.reasoning_version import (
    ReasoningVersion,
)
from app.models.telemetry import (
    LLMCallStatus,
)
from app.reasoning.telemetry import (
    TelemetryRecorder,
)


def test_success_event_is_recorded():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-001"
    )

    started = datetime(
        2026,
        9,
        7,
        5,
        0,
        0,
        tzinfo=timezone.utc,
    )

    completed = datetime(
        2026,
        9,
        7,
        5,
        0,
        0,
        50000,
        tzinfo=timezone.utc,
    )

    candidates = [
        TitleReasoningCandidate(
            candidate_id="candidate-1",
            title="خرید کفش مردانه",
            rationale="relevance",
            confidence_score=0.9,
        )
    ]

    event = recorder.record(
        provider_id="arvan_aiaas",
        model="Gemini-2.5-Flash-lite",
        version=ReasoningVersion(
            prompt_version="title-v1",
            rule_version="rules-v1",
            config_version="config-v1",
        ),
        started_at=started,
        completed_at=completed,
        attempt_count=1,
        status=LLMCallStatus.SUCCESS,
        failure_type=None,
        input_message_count=2,
        input_character_count=1000,
        output_character_count=50,
        candidates=candidates,
    )

    assert event.event_id == "event-001"
    assert event.provider_id == (
        "arvan_aiaas"
    )

    assert event.model == (
        "Gemini-2.5-Flash-lite"
    )

    assert event.prompt_version == (
        "title-v1"
    )

    assert event.latency_ms == 50.0
    assert event.status == (
        LLMCallStatus.SUCCESS
    )
    assert event.candidate_count == 1

    assert recorder.events == [event]


def test_failed_event_preserves_failure_type():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-failure"
    )

    started = datetime(
        2026,
        9,
        7,
        5,
        0,
        tzinfo=timezone.utc,
    )

    completed = datetime(
        2026,
        9,
        7,
        5,
        0,
        1,
        tzinfo=timezone.utc,
    )

    from app.models.llm import (
        LLMFailureType,
    )

    event = recorder.record(
        provider_id="arvan_aiaas",
        model="test-model",
        version=ReasoningVersion(
            prompt_version="title-v1",
            rule_version="rules-v1",
            config_version="config-v1",
        ),
        started_at=started,
        completed_at=completed,
        attempt_count=3,
        status=LLMCallStatus.FAILED,
        failure_type=(
            LLMFailureType.RATE_LIMIT
        ),
        input_message_count=2,
        input_character_count=100,
        output_character_count=0,
        candidates=[],
    )

    assert event.status == (
        LLMCallStatus.FAILED
    )

    assert event.failure_type == (
        "rate_limit"
    )

    assert event.attempt_count == 3
    assert event.candidate_count == 0


def test_telemetry_is_deterministic():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "same-event"
    )

    started = datetime(
        2026,
        9,
        7,
        5,
        0,
        tzinfo=timezone.utc,
    )

    completed = datetime(
        2026,
        9,
        7,
        5,
        0,
        0,
        100000,
        tzinfo=timezone.utc,
    )

    version = ReasoningVersion(
        prompt_version="title-v1",
        rule_version="rules-v1",
        config_version="config-v1",
    )

    first = recorder.record(
        provider_id="arvan_aiaas",
        model="test-model",
        version=version,
        started_at=started,
        completed_at=completed,
        attempt_count=1,
        status=LLMCallStatus.SUCCESS,
        failure_type=None,
        input_message_count=2,
        input_character_count=100,
        output_character_count=50,
        candidates=[],
    )

    second = recorder.record(
        provider_id="arvan_aiaas",
        model="test-model",
        version=version,
        started_at=started,
        completed_at=completed,
        attempt_count=1,
        status=LLMCallStatus.SUCCESS,
        failure_type=None,
        input_message_count=2,
        input_character_count=100,
        output_character_count=50,
        candidates=[],
    )

    assert first.model_dump() == (
        second.model_dump()
    )

def test_logical_call_id_defaults_to_event_id():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-default-call"
    )

    started = datetime(
        2026,
        9,
        7,
        5,
        0,
        tzinfo=timezone.utc,
    )

    completed = datetime(
        2026,
        9,
        7,
        5,
        0,
        1,
        tzinfo=timezone.utc,
    )

    event = recorder.record(
        provider_id="arvan_aiaas",
        model="test-model",
        version=ReasoningVersion(
            prompt_version="title-v1",
            rule_version="rules-v1",
            config_version="config-v1",
        ),
        started_at=started,
        completed_at=completed,
        attempt_count=1,
        status=LLMCallStatus.SUCCESS,
        failure_type=None,
        input_message_count=1,
        input_character_count=10,
        output_character_count=10,
        candidates=[],
    )

    assert event.logical_call_id == (
        "event-default-call"
    )

    assert event.provider_switch_count == 0


def test_explicit_logical_call_id_is_preserved():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-explicit"
    )

    started = datetime(
        2026,
        9,
        7,
        5,
        0,
        tzinfo=timezone.utc,
    )

    completed = datetime(
        2026,
        9,
        7,
        5,
        0,
        1,
        tzinfo=timezone.utc,
    )

    event = recorder.record(
        logical_call_id="reasoning:123",
        provider_id="arvan_aiaas",
        model="test-model",
        version=ReasoningVersion(
            prompt_version="title-v1",
            rule_version="rules-v1",
            config_version="config-v1",
        ),
        started_at=started,
        completed_at=completed,
        attempt_count=1,
        status=LLMCallStatus.SUCCESS,
        failure_type=None,
        input_message_count=1,
        input_character_count=10,
        output_character_count=10,
        candidates=[],
        provider_switch_count=2,
    )

    assert event.logical_call_id == (
        "reasoning:123"
    )

    assert event.provider_switch_count == 2    