from app.models.observability import ObservabilityEventType, ObservabilityLevel
from app.observability import InMemoryEventSink, InMemoryMetricsSink, ObservabilityContext

def make_context():
    return ObservabilityContext(event_sink=InMemoryEventSink(),metrics_sink=InMemoryMetricsSink(),correlation_id="corr-001",trace_id="trace-001",site_id="site-1",job_id="job-1",run_id="run-1")

def test_event_preserves_correlation_and_domain_ids():
    event=make_context().event(event_type=ObservabilityEventType.RUN,operation="run.started",message="SEO run started.",attributes={"attempt":1})
    assert event.correlation_id=="corr-001" and event.trace_id=="trace-001" and event.site_id=="site-1" and event.job_id=="job-1" and event.run_id=="run-1"
    assert event.attributes=={"attempt":1}

def test_event_sink_filters_by_correlation_and_type():
    sink=InMemoryEventSink(); ctx=ObservabilityContext(event_sink=sink,metrics_sink=InMemoryMetricsSink(),correlation_id="corr-a")
    ctx.event(event_type=ObservabilityEventType.JOB,operation="job.created",message="created"); ctx.event(event_type=ObservabilityEventType.LLM,operation="llm.completed",message="completed")
    rows=sink.list_events(correlation_id="corr-a",event_type="job")
    assert len(rows)==1 and rows[0].operation=="job.created"

def test_counter_metrics_are_aggregated_by_name_and_labels():
    metrics=InMemoryMetricsSink(); assert metrics.increment_counter("jobs.completed",labels={"status":"success"}).value==1; assert metrics.increment_counter("jobs.completed",labels={"status":"success"}).value==2; assert metrics.increment_counter("jobs.completed",labels={"status":"failed"}).value==1; assert len(metrics.counters())==2

def test_duration_metric_tracks_count_total_min_max_and_average():
    metrics=InMemoryMetricsSink(); metrics.observe_duration("run.duration_ms",100.0); metric=metrics.observe_duration("run.duration_ms",300.0)
    assert metric.count==2 and metric.total_ms==400.0 and metric.min_ms==100.0 and metric.max_ms==300.0 and metric.average_ms==200.0

def test_negative_metric_inputs_are_rejected():
    metrics=InMemoryMetricsSink()
    try: metrics.increment_counter("x",value=-1); assert False
    except ValueError: pass
    try: metrics.observe_duration("x",-0.1); assert False
    except ValueError: pass

def test_context_activation_is_scoped():
    from app.observability.context import get_current_context
    ctx=make_context(); assert get_current_context() is None
    with ctx.activate(): assert get_current_context() is ctx
    assert get_current_context() is None

def test_error_event_can_carry_provider_and_logical_call_identity():
    event=make_context().event(event_type=ObservabilityEventType.LLM,level=ObservabilityLevel.ERROR,operation="llm.failed",message="Provider request failed.",logical_call_id="reasoning:recommendation-1",provider_id="arvan_aiaas")
    assert event.level==ObservabilityLevel.ERROR and event.logical_call_id=="reasoning:recommendation-1" and event.provider_id=="arvan_aiaas"
