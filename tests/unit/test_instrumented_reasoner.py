from datetime import datetime, timezone

from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)
from app.models.reasoning_version import (
    ReasoningVersion,
)
from app.models.snapshots import (
    SnapshotMetadata,
)
from app.reasoning.instrumented import (
    InstrumentedReasoner,
)
from app.reasoning.telemetry import (
    TelemetryRecorder,
)


class FakeReasoner:
    @property
    def provider_id(self) -> str:
        return "arvan_aiaas"

    def generate_title_candidates(
        self,
        reasoning_input,
    ):
        return [
            TitleReasoningCandidate(
                candidate_id="candidate-1",
                title="خرید کفش مردانه",
                rationale="test",
                confidence_score=0.9,
            )
        ]


class FailingReasoner:
    @property
    def provider_id(self) -> str:
        return "arvan_aiaas"

    def generate_title_candidates(
        self,
        reasoning_input,
    ):
        raise RuntimeError(
            "provider failure"
        )


def make_input() -> TitleReasoningInput:
    return TitleReasoningInput(
        recommendation_id="rec-telemetry",
        site_id="site-1",
        normalized_url="https://example.com",
        primary_query="کفش مردانه",
        current_title="عنوان",
        target_position=5,
        competitor_titles=[
            "رقیب",
        ],
        competitor_title_length_median=10.0,
        competitor_title_length_average=10.0,
        title_length_gap_vs_competitors=2.0,
        confidence_score=0.9,
        constraints=[],
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-1",
            data_snapshot_id="data-1",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime(
                2026,
                9,
                7,
                tzinfo=timezone.utc,
            ),
        ),
    )


def make_version() -> ReasoningVersion:
    return ReasoningVersion(
        prompt_version="title-v1",
        rule_version="rules-v1",
        config_version="config-v1",
    )


def test_success_is_recorded():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-001"
    )

    reasoner = InstrumentedReasoner(
        reasoner=FakeReasoner(),
        model="Gemini-2.5-Flash-lite",
        version=make_version(),
        telemetry=recorder,
    )

    result = (
        reasoner.generate_title_candidates(
            make_input()
        )
    )

    assert len(result) == 1
    assert len(recorder.events) == 1

    event = recorder.events[0]

    assert event.provider_id == (
        "arvan_aiaas"
    )

    assert event.model == (
        "Gemini-2.5-Flash-lite"
    )

    assert event.status.value == (
        "success"
    )

    assert event.candidate_count == 1


def test_failure_is_recorded_before_error_propagates():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-failure"
    )

    reasoner = InstrumentedReasoner(
        reasoner=FailingReasoner(),
        model="Gemini-2.5-Flash-lite",
        version=make_version(),
        telemetry=recorder,
    )

    import pytest

    with pytest.raises(
        RuntimeError,
        match="provider failure",
    ):
        reasoner.generate_title_candidates(
            make_input()
        )

    assert len(recorder.events) == 1

    event = recorder.events[0]

    assert event.status.value == (
        "failed"
    )


def test_provider_id_is_preserved():
    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-provider"
    )

    reasoner = InstrumentedReasoner(
        reasoner=FakeReasoner(),
        model="test-model",
        version=make_version(),
        telemetry=recorder,
    )

    assert reasoner.provider_id == (
        "arvan_aiaas"
    )



def test_execution_context_is_forwarded_to_inner_reasoner():
    from app.reasoning.context import (
        ReasoningExecutionContext,
    )

    class ContextAwareReasoner(FakeReasoner):
        def __init__(self):
            self.received_context = None

        def set_execution_context(self, context):
            self.received_context = context

    inner = ContextAwareReasoner()

    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-forward",
    )

    reasoner = InstrumentedReasoner(
        reasoner=inner,
        model="test-model",
        version=make_version(),
        telemetry=recorder,
    )

    context = ReasoningExecutionContext(
        logical_call_id="forwarded-call",
    )

    reasoner.set_execution_context(
        context
    )

    assert inner.received_context is context


def test_instrumented_reasoner_does_not_duplicate_aggregate_telemetry_inside_router_context():
    from app.reasoning.context import (
        ReasoningExecutionContext,
    )

    inner = FakeReasoner()

    recorder = TelemetryRecorder(
        event_id_factory=lambda:
        "event-no-duplicate",
    )

    reasoner = InstrumentedReasoner(
        reasoner=inner,
        model="test-model",
        version=make_version(),
        telemetry=recorder,
    )

    reasoner.set_execution_context(
        ReasoningExecutionContext(
            logical_call_id="router-call",
        )
    )

    result = reasoner.generate_title_candidates(
        make_input()
    )

    assert len(result) == 1
    assert recorder.events == []
