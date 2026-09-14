from __future__ import annotations
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4
from app.models.observability import ObservabilityEvent, ObservabilityEventType, ObservabilityLevel
from app.observability.contracts import EventSink, MetricsSink

_current_context: ContextVar[ObservabilityContext | None] = ContextVar("seo_agent_observability_context", default=None)

class ObservabilityContext:
    def __init__(self, *, event_sink: EventSink, metrics_sink: MetricsSink, correlation_id: str, trace_id: str | None = None, site_id: str | None = None, job_id: str | None = None, run_id: str | None = None, span_id: str | None = None) -> None:
        if not correlation_id.strip(): raise ValueError("correlation_id must not be blank")
        self.event_sink=event_sink; self.metrics_sink=metrics_sink; self.correlation_id=correlation_id
        self.trace_id=trace_id or uuid4().hex; self.site_id=site_id; self.job_id=job_id; self.run_id=run_id; self.span_id=span_id
    def derive(self, **identities) -> ObservabilityContext:
        values = {name: getattr(self, name) for name in
                  ("correlation_id", "trace_id", "site_id", "job_id", "run_id", "span_id")}
        values.update(identities)
        return ObservabilityContext(event_sink=self.event_sink, metrics_sink=self.metrics_sink, **values)

    def child_span(self) -> str: return uuid4().hex
    def event(self, *, event_type: ObservabilityEventType, operation: str, message: str, level: ObservabilityLevel = ObservabilityLevel.INFO, span_id: str | None = None, action_id: str | None = None, logical_call_id: str | None = None, provider_id: str | None = None, parent_span_id: str | None = None, attributes: Mapping[str, object] | None = None) -> ObservabilityEvent:
        event=ObservabilityEvent(event_id=uuid4().hex,event_type=event_type,level=level,correlation_id=self.correlation_id,trace_id=self.trace_id,span_id=span_id or self.child_span(),parent_span_id=parent_span_id,site_id=self.site_id,job_id=self.job_id,run_id=self.run_id,action_id=action_id,logical_call_id=logical_call_id,provider_id=provider_id,operation=operation,message=message,attributes=dict(attributes or {}))
        return self.event_sink.emit(event)
    def increment(self,name: str,*,value: int=1,labels: Mapping[str,str]|None=None): return self.metrics_sink.increment_counter(name,value=value,labels=labels)
    def duration(self,name: str,duration_ms: float,*,labels: Mapping[str,str]|None=None): return self.metrics_sink.observe_duration(name,duration_ms,labels=labels)
    @contextmanager
    def activate(self) -> Iterator[ObservabilityContext]:
        token=_current_context.set(self)
        try: yield self
        finally: _current_context.reset(token)

def get_current_context() -> ObservabilityContext | None: return _current_context.get()

