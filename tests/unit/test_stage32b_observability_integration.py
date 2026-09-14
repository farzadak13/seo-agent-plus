import pytest

from app.models.observability import ObservabilityEventType, ObservabilityLevel
from app.observability import (
    InMemoryEventSink,
    InMemoryMetricsSink,
    ObservabilityContext,
    instrument_callable,
    instrument_execution,
    instrument_job_handler,
    instrument_pipeline,
    instrument_run,
    get_current_context,
)


def make_context():
    return ObservabilityContext(
        event_sink=InMemoryEventSink(),
        metrics_sink=InMemoryMetricsSink(),
        correlation_id="corr-stage32b",
        trace_id="trace-stage32b",
        site_id="site-1",
        job_id="job-1",
        run_id="run-1",
    )


def test_instrumented_callable_preserves_result_and_context():
    ctx = make_context()

    def work(value):
        assert get_current_context() is ctx
        return value * 2

    wrapped = instrument_callable(
        work,
        context=ctx,
        event_type=ObservabilityEventType.SYSTEM,
        operation="test.work",
        started_message="started",
        completed_message="completed",
        failed_message="failed",
    )

    assert wrapped(3) == 6
    assert get_current_context() is None

    events = ctx.event_sink.list_events(correlation_id="corr-stage32b")
    assert [event.operation for event in events] == [
        "test.work.started",
        "test.work.completed",
    ]


def test_instrumented_callable_preserves_exception_and_records_failure():
    ctx = make_context()

    def fail():
        raise RuntimeError("business failure")

    wrapped = instrument_callable(
        fail,
        context=ctx,
        event_type=ObservabilityEventType.RUN,
        operation="test.fail",
        started_message="started",
        completed_message="completed",
        failed_message="failed",
    )

    with pytest.raises(RuntimeError, match="business failure"):
        wrapped()

    events = ctx.event_sink.list_events(correlation_id="corr-stage32b")
    assert events[-1].operation == "test.fail.failed"
    assert events[-1].level == ObservabilityLevel.ERROR
    assert events[-1].attributes["error_type"] == "RuntimeError"


def test_success_metrics_are_recorded():
    ctx = make_context()

    wrapped = instrument_run(lambda: "ok", context=ctx)
    assert wrapped() == "ok"

    counters = ctx.metrics_sink.counters()
    assert any(
        metric.name == "run.execute.total"
        and metric.labels == {"status": "success"}
        and metric.value == 1
        for metric in counters
    )

    durations = ctx.metrics_sink.durations()
    assert any(
        metric.name == "run.execute.duration_ms"
        and metric.labels == {"status": "success"}
        and metric.count == 1
        for metric in durations
    )


def test_failure_metrics_are_recorded():
    ctx = make_context()

    def fail():
        raise ValueError("boom")

    wrapped = instrument_pipeline(fail, context=ctx)
    with pytest.raises(ValueError, match="boom"):
        wrapped()

    assert any(
        metric.name == "pipeline.execute.total"
        and metric.labels == {"status": "failed"}
        and metric.value == 1
        for metric in ctx.metrics_sink.counters()
    )


def test_job_handler_uses_job_event_type_and_safe_attributes():
    ctx = make_context()
    seen = []

    def handler(payload):
        seen.append(payload)
        return {"ok": True}

    wrapped = instrument_job_handler(handler, context=ctx)
    result = wrapped({"job_type": "seo_run", "api_key": "SECRET"})

    assert result == {"ok": True}
    assert seen[0]["api_key"] == "SECRET"

    events = ctx.event_sink.list_events(
        correlation_id="corr-stage32b",
        event_type="job",
    )
    assert events[0].attributes == {"job_type": "seo_run"}
    assert "api_key" not in events[0].attributes


def test_pipeline_wrapper_emits_pipeline_events():
    ctx = make_context()
    wrapped = instrument_pipeline(lambda: 42, context=ctx)
    assert wrapped() == 42
    events = ctx.event_sink.list_events(event_type="pipeline")
    assert [event.operation for event in events] == [
        "pipeline.execute.started",
        "pipeline.execute.completed",
    ]


def test_execution_wrapper_emits_execution_events():
    ctx = make_context()
    wrapped = instrument_execution(lambda: "executed", context=ctx)
    assert wrapped() == "executed"
    events = ctx.event_sink.list_events(event_type="execution")
    assert [event.operation for event in events] == [
        "execution.execute.started",
        "execution.execute.completed",
    ]


def test_nested_wrappers_keep_same_correlation_context():
    ctx = make_context()
    observed = []

    def pipeline():
        observed.append(get_current_context())
        return "done"

    wrapped_pipeline = instrument_pipeline(pipeline, context=ctx)
    wrapped_run = instrument_run(wrapped_pipeline, context=ctx)

    assert wrapped_run() == "done"
    assert observed == [ctx]

    events = ctx.event_sink.list_events(correlation_id="corr-stage32b")
    assert len(events) == 4
    assert events[0].event_type == ObservabilityEventType.RUN
    assert events[1].event_type == ObservabilityEventType.PIPELINE


def test_observability_does_not_change_business_return_type():
    ctx = make_context()

    value = {"status": "completed", "count": 3}
    wrapped = instrument_callable(
        lambda: value,
        context=ctx,
        event_type=ObservabilityEventType.SYSTEM,
        operation="test.identity",
        started_message="started",
        completed_message="completed",
        failed_message="failed",
    )

    result = wrapped()
    assert result is value


def test_observability_does_not_mask_eventual_business_failure():
    ctx = make_context()

    class SentinelError(Exception):
        pass

    def fail():
        raise SentinelError("sentinel")

    wrapped = instrument_callable(
        fail,
        context=ctx,
        event_type=ObservabilityEventType.EXECUTION,
        operation="test.sentinel",
        started_message="started",
        completed_message="completed",
        failed_message="failed",
    )

    with pytest.raises(SentinelError, match="sentinel"):
        wrapped()
