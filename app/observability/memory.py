from __future__ import annotations
from collections import defaultdict
from collections.abc import Mapping
from threading import Lock
from app.models.observability import CounterMetric, DurationMetric, ObservabilityEvent

def _label_key(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))

class InMemoryEventSink:
    def __init__(self) -> None:
        self._events: list[ObservabilityEvent] = []
        self._lock = Lock()
    def emit(self, event: ObservabilityEvent) -> ObservabilityEvent:
        with self._lock: self._events.append(event)
        return event
    def list_events(self, *, correlation_id: str | None = None, event_type: str | None = None, limit: int = 100) -> list[ObservabilityEvent]:
        if limit < 1: raise ValueError("limit must be >= 1")
        with self._lock: events=list(self._events)
        if correlation_id is not None: events=[e for e in events if e.correlation_id == correlation_id]
        if event_type is not None: events=[e for e in events if e.event_type.value == event_type]
        return events[-limit:]

class InMemoryMetricsSink:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
        self._durations: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float | int | None]] = {}
        self._lock = Lock()
    def increment_counter(self, name: str, *, value: int = 1, labels: Mapping[str, str] | None = None) -> CounterMetric:
        if not name.strip(): raise ValueError("metric name must not be blank")
        if value < 0: raise ValueError("counter increment cannot be negative")
        key=(name,_label_key(labels))
        with self._lock: self._counters[key]+=value; current=self._counters[key]
        return CounterMetric(name=name,value=current,labels=dict(labels or {}))
    def observe_duration(self, name: str, duration_ms: float, *, labels: Mapping[str, str] | None = None) -> DurationMetric:
        if not name.strip(): raise ValueError("metric name must not be blank")
        if duration_ms < 0: raise ValueError("duration_ms cannot be negative")
        key=(name,_label_key(labels))
        with self._lock:
            bucket=self._durations.setdefault(key,{"count":0,"total_ms":0.0,"min_ms":None,"max_ms":None})
            bucket["count"]=int(bucket["count"])+1; bucket["total_ms"]=float(bucket["total_ms"])+duration_ms
            bucket["min_ms"]=duration_ms if bucket["min_ms"] is None else min(float(bucket["min_ms"]),duration_ms)
            bucket["max_ms"]=duration_ms if bucket["max_ms"] is None else max(float(bucket["max_ms"]),duration_ms)
            snapshot=dict(bucket)
        return DurationMetric(name=name,labels=dict(labels or {}),**snapshot)
    def counters(self) -> list[CounterMetric]:
        with self._lock: rows=list(self._counters.items())
        return [CounterMetric(name=n,value=v,labels=dict(l)) for (n,l),v in sorted(rows)]
    def durations(self) -> list[DurationMetric]:
        with self._lock: rows=[(k,dict(v)) for k,v in self._durations.items()]
        return [DurationMetric(name=n,labels=dict(l),**v) for (n,l),v in sorted(rows)]
