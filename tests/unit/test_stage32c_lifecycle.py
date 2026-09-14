from datetime import date
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import pytest

from app.observability import ObservabilityContext, InMemoryEventSink, InMemoryMetricsSink, get_current_context, instrument_run
from app.jobs import JobScheduler, JobStore, JobHandlerRegistry
from app.persistence.memory import InMemoryRepository
from app.models.jobs import Job
from app.models.runs import SEORun
from app.models.sites import Site
from app.onboarding.site_store import SiteStore
from app.runs.store import RunStore
from app.runs.handler import build_seo_run_handler
from app.pipeline.decision import run_decision_pipeline
from app.reasoning.router import ReasoningRouter
from app.models.fallback import ProviderFallbackPolicy
from app.execution.orchestrator import execute_approved_action, approve_action
from app.models.site_adapter import AdapterOperationResult, AdapterOperation
from tests.unit.test_decision_pipeline_runner import make_response, make_snapshot, make_baseline_observations
from tests.unit.test_execution_orchestrator import make_action, FakeSiteAdapter
from tests.unit.test_reasoning_router import make_input, make_registry, FakeReasoner, RetryableFailure


def context():
    return ObservabilityContext(event_sink=InMemoryEventSink(), metrics_sink=InMemoryMetricsSink(), correlation_id="test")


def pipeline():
    return run_decision_pipeline(response=make_response(), start_date=date(2026,9,5), end_date=date(2026,9,5),
        baseline_observations=make_baseline_observations(), baseline_url_metrics=[], snapshot=make_snapshot(),
        candidate_id="c", normalized_url="https://example.com/page", normalized_query="کفش مردانه")


def scheduler(ctx, work, max_attempts=1):
    repo=InMemoryRepository(); sites=SiteStore(repo); runs=RunStore(repo)
    sites.create(Site(site_id="site-1", principal_id="p", name="Example", base_url="https://example.com"))
    runs.create(SEORun(run_id="r", site_id="site-1", principal_id="p", job_id="j", start_date="2026-09-05",
        end_date="2026-09-05", normalized_url="https://example.com/page", normalized_query="seo", candidate_id="c"))
    class Service:
        def run(self, **kwargs): return work()
    registry=JobHandlerRegistry()
    registry.register("seo_run", build_seo_run_handler(site_store=sites, run_store=runs, run_service=Service()))
    worker=JobScheduler(store=JobStore(repo), handlers=registry, observability=ctx)
    worker.store.create(Job(job_id="j",job_type="seo_run",principal_id="p",payload={"run_id":"r","secret":"DO_NOT_LOG"},max_attempts=max_attempts))
    return worker,runs


def test_real_boundaries_share_trace_and_pair_spans():
    ctx=context()
    def work():
        result=pipeline()
        router=ReasoningRouter(registry=make_registry([("fake",FakeReasoner(provider_id="fake"))]), fallback_policy=ProviderFallbackPolicy())
        router.generate_title_candidates(reasoning_input=make_input())
        execution=execute_approved_action(action=approve_action(make_action()),adapters=[FakeSiteAdapter()])
        assert execution.status.value=="executed"
        return result
    worker,runs=scheduler(ctx,work)
    assert worker.run_once().status.value=="completed"
    events=ctx.event_sink.list_events()
    assert {e.event_type.value for e in events}=={"job","run","pipeline","llm","execution"}
    assert len({e.correlation_id for e in events})==len({e.trace_id for e in events})==1
    assert all(e.job_id=="j" and e.run_id=="r" for e in events)
    assert all(e.site_id=="site-1" for e in events if e.event_type.value!="job")
    grouped=defaultdict(list)
    for e in events: grouped[e.span_id].append(e)
    assert all(len(pair)==2 and pair[0].operation.endswith('.started') and pair[1].operation.endswith('.completed') for pair in grouped.values())
    starts={e.operation:e for e in events if e.operation.endswith('.started')}
    assert starts['run.execute.started'].parent_span_id==starts['job.execute.started'].span_id
    for name in ['pipeline.execute','llm.generate','execution.execute']:
        assert starts[name+'.started'].parent_span_id==starts['run.execute.started'].span_id
    assert starts['llm.provider.started'].parent_span_id==starts['llm.generate.started'].span_id
    assert starts['llm.provider.started'].provider_id=='fake'
    assert starts['execution.execute.started'].action_id==make_action().action_id
    assert 'DO_NOT_LOG' not in str(events)
    assert get_current_context() is None


def test_retry_reaches_service_and_keeps_trace_with_new_spans():
    ctx=context(); calls=[]
    def work():
        calls.append(1)
        if len(calls)==1: raise RuntimeError('DO_NOT_LOG')
        return pipeline()
    worker,runs=scheduler(ctx,work,2)
    assert worker.run_once().status.value=='queued'
    assert worker.run_once().status.value=='completed'
    assert len(calls)==2 and runs.get('r').status.value=='completed'
    events=ctx.event_sink.list_events()
    jobs=[e for e in events if e.operation=='job.execute.started']
    assert len({e.trace_id for e in jobs})==1 and len({e.span_id for e in jobs})==2
    assert [e.attributes['attempt'] for e in jobs]==[1,2]
    assert 'DO_NOT_LOG' not in str(events)


@pytest.mark.parametrize('business_fails',[False,True])
def test_broken_sinks_cannot_change_business_outcome(business_fails):
    class Broken:
        def emit(self,*a,**k): raise RuntimeError('sink')
        def increment_counter(self,*a,**k): raise RuntimeError('sink')
        def observe_duration(self,*a,**k): raise RuntimeError('sink')
    ctx=ObservabilityContext(event_sink=Broken(),metrics_sink=Broken(),correlation_id='x')
    sentinel=ValueError('business'); result=object(); calls=[]
    def work():
        calls.append(1)
        if business_fails: raise sentinel
        return result
    wrapped=instrument_run(work,context=ctx)
    if business_fails:
        with pytest.raises(ValueError) as exc: wrapped()
        assert exc.value is sentinel
    else: assert wrapped() is result
    assert len(calls)==1 and get_current_context() is None


def test_returned_execution_failure_emits_failure_and_preserves_result():
    ctx=context(); adapter=FakeSiteAdapter()
    def reject(**kw):
        return AdapterOperationResult(success=False,operation=AdapterOperation.UPDATE_TITLE,site_id=kw['site_id'],normalized_url=kw['normalized_url'])
    adapter.update_title=reject
    with ctx.activate(): result=execute_approved_action(action=approve_action(make_action()),adapters=[adapter])
    assert result.status.value=='failed' and result.operation_result.success is False
    assert [e.operation for e in ctx.event_sink.list_events()]==['execution.execute.started','execution.execute.failed']
    assert ctx.metrics_sink.counters()[0].labels=={'status':'failed'}


def test_pipeline_rejection_is_not_reported_as_success():
    ctx=context()
    with ctx.activate():
        result=run_decision_pipeline(response=make_response(), start_date=date(2026,9,1),end_date=date(2026,9,5),
            baseline_observations=[],baseline_url_metrics=[],snapshot=make_snapshot(),candidate_id='c',
            normalized_url='https://example.com/page',normalized_query='seo')
    assert result.status.value.startswith('rejected')
    assert ctx.event_sink.list_events()[-1].operation=='pipeline.execute.rejected'


def test_llm_fallback_records_provider_failure_and_success_in_same_trace():
    ctx=context()
    router=ReasoningRouter(registry=make_registry([('first',FakeReasoner(provider_id='first',failure=RetryableFailure('DO_NOT_LOG'))),('second',FakeReasoner(provider_id='second'))]),fallback_policy=ProviderFallbackPolicy())
    with ctx.activate(): router.generate_title_candidates(reasoning_input=make_input(),provider_ids=['first','second'])
    events=ctx.event_sink.list_events()
    assert any(e.operation=='llm.provider.failed' and e.provider_id=='first' for e in events)
    assert any(e.operation=='llm.provider.completed' and e.provider_id=='second' for e in events)
    assert events[-1].operation=='llm.generate.completed'
    assert len({e.logical_call_id for e in events})==1
    assert 'DO_NOT_LOG' not in str(events)


def test_parallel_contexts_do_not_leak():
    def work(index):
        ctx=context().derive(correlation_id=str(index))
        with ctx.activate(): pipeline()
        assert get_current_context() is None
        return {e.correlation_id for e in ctx.event_sink.list_events()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(work,[1,2]))==[{'1'},{'2'}]


def test_worker_restart_recovers_running_run_without_losing_identity():
    ctx=context(); worker,runs=scheduler(ctx,pipeline,2)
    worker.store.update(worker.store.get('j').transition_running())
    runs.update(runs.get('r').start())
    worker.recover()
    assert worker.run_once().status.value=='completed'
    assert runs.get('r').status.value=='completed'
    assert ctx.event_sink.list_events()[0].attributes['attempt']==2


def test_final_failure_marks_both_job_and_run_failed():
    ctx=context()
    def fail(): raise ValueError('private')
    worker,runs=scheduler(ctx,fail)
    assert worker.run_once().status.value=='failed'
    assert runs.get('r').status.value=='failed'
    assert [e.operation for e in ctx.event_sink.list_events()]==[
        'job.execute.started','run.execute.started','run.execute.failed','job.execute.failed']
    assert get_current_context() is None


def test_attempt_recorder_emits_correlated_safe_usage_event():
    from datetime import datetime, timezone
    from app.reasoning.attempt_telemetry import AttemptTelemetryRecorder
    from app.models.llm_attempt import LLMAttemptStatus
    from app.models.reasoning_version import ReasoningVersion
    ctx=context(); recorder=AttemptTelemetryRecorder(attempt_id_factory=lambda:'a')
    now=datetime.now(timezone.utc)
    with ctx.derive(span_id='provider-span',job_id='j',run_id='r').activate():
        result=recorder.record(logical_call_id='call',provider_id='provider',model='model',attempt_number=1,
            started_at=now,completed_at=now,status=LLMAttemptStatus.SUCCESS,failure_type=None,
            version=ReasoningVersion(prompt_version='p',rule_version='r',config_version='c'),
            input_tokens=10,output_tokens=2,total_tokens=12,finish_reason='stop')
    event=ctx.event_sink.list_events()[0]
    assert event.operation=='llm.attempt.completed'
    assert event.logical_call_id=='call' and event.provider_id=='provider'
    assert event.parent_span_id=='provider-span' and event.job_id=='j' and event.run_id=='r'
    assert event.attributes['total_tokens']==12
    assert recorder.events==[result]
