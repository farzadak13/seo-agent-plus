from datetime import datetime, timezone

from app.models.llm import (
    LLMFailureType,
)
from app.models.llm_attempt import (
    LLMAttemptStatus,
)
from app.models.reasoning_version import (
    ReasoningVersion,
)
from app.reasoning.attempt_telemetry import (
    AttemptTelemetryRecorder,
)


def make_version() -> ReasoningVersion:
    return ReasoningVersion(
        prompt_version="title-v1",
        rule_version="rules-v1",
        config_version="config-v1",
    )


def make_times():
    started = datetime(
        2026,
        9,
        7,
        6,
        0,
        0,
        tzinfo=timezone.utc,
    )

    completed = datetime(
        2026,
        9,
        7,
        6,
        0,
        0,
        250000,
        tzinfo=timezone.utc,
    )

    return started, completed


def test_success_attempt_is_recorded():
    recorder = AttemptTelemetryRecorder(
        attempt_id_factory=lambda:
        "attempt-001"
    )

    started, completed = make_times()

    event = recorder.record(
        logical_call_id="call-001",
        provider_id="arvan_aiaas",
        model="Gemini-2.5-Flash-lite",
        attempt_number=1,
        started_at=started,
        completed_at=completed,
        status=LLMAttemptStatus.SUCCESS,
        failure_type=None,
        version=make_version(),
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        finish_reason="stop",
    )

    assert event.attempt_id == "attempt-001"
    assert event.logical_call_id == "call-001"
    assert event.provider_id == "arvan_aiaas"
    assert event.attempt_number == 1
    assert event.latency_ms == 250.0
    assert event.status == LLMAttemptStatus.SUCCESS
    assert event.total_tokens == 150


def test_failed_attempt_preserves_failure_type():
    recorder = AttemptTelemetryRecorder(
        attempt_id_factory=lambda:
        "attempt-failure"
    )

    started = datetime(
        2026,
        9,
        7,
        6,
        0,
        0,
        tzinfo=timezone.utc,
    )

    completed = datetime(
        2026,
        9,
        7,
        6,
        0,
        1,
        tzinfo=timezone.utc,
    )

    event = recorder.record(
        logical_call_id="call-failure",
        provider_id="arvan_aiaas",
        model="Gemini-2.5-Flash-lite",
        attempt_number=2,
        started_at=started,
        completed_at=completed,
        status=LLMAttemptStatus.FAILED,
        failure_type=LLMFailureType.TIMEOUT,
        version=make_version(),
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        finish_reason=None,
    )

    assert event.status == (
        LLMAttemptStatus.FAILED
    )

    assert event.failure_type == (
        LLMFailureType.TIMEOUT
    )

    assert event.attempt_number == 2


def test_attempt_is_linked_to_logical_call():
    recorder = AttemptTelemetryRecorder(
        attempt_id_factory=lambda:
        "attempt-replay"
    )

    started, completed = make_times()

    event = recorder.record(
        logical_call_id="call-replay",
        provider_id="arvan_aiaas",
        model="Gemini-2.5-Flash-lite",
        attempt_number=1,
        started_at=started,
        completed_at=completed,
        status=LLMAttemptStatus.SUCCESS,
        failure_type=None,
        version=make_version(),
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        finish_reason="stop",
    )

    assert event.logical_call_id == (
        "call-replay"
    )

    assert event.prompt_version == (
        "title-v1"
    )



def test_provider_switch_index_is_preserved():
    recorder = AttemptTelemetryRecorder(
        attempt_id_factory=lambda:
        "attempt-switch"
    )

    started, completed = make_times()

    event = recorder.record(
        logical_call_id="call-switch",
        provider_id="provider-b",
        model="model-b",
        attempt_number=3,
        provider_switch_index=1,
        started_at=started,
        completed_at=completed,
        status=LLMAttemptStatus.SUCCESS,
        failure_type=None,
        version=make_version(),
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        finish_reason="stop",
    )

    assert event.provider_switch_index == 1
