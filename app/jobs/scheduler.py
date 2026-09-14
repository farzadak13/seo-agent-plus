from __future__ import annotations

import hashlib
from app.observability.context import ObservabilityContext
from app.observability.lifecycle import lifecycle_span
from app.models.observability import ObservabilityEventType

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from app.jobs.store import JobStore
from app.models.jobs import Job


class JobHandler(Protocol):
    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class JobHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(
        self,
        job_type: str,
        handler: JobHandler,
        *,
        replace: bool = False,
    ) -> None:
        normalized_type = job_type.strip()
        if not normalized_type:
            raise ValueError("job_type cannot be empty")
        if not replace and normalized_type in self._handlers:
            raise ValueError(
                f"Handler already registered for job type: {normalized_type}"
            )
        self._handlers[normalized_type] = handler

    def resolve(self, job_type: str) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as exc:
            raise KeyError(
                f"No handler registered for job type: {job_type}"
            ) from exc

    def contains(self, job_type: str) -> bool:
        return job_type in self._handlers


class JobScheduler:
    """
    Single-worker polling scheduler.

    The scheduler owns execution orchestration but not SEO decisions. Actual
    domain work is injected as a JobHandler. This keeps API and scheduling
    independent from the SEO decision engine.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        handlers: JobHandlerRegistry,
        observability: ObservabilityContext | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._observability = observability
        self._store = store
        self._handlers = handlers
        self._sleep_fn = sleep_fn

    @property
    def store(self) -> JobStore:
        return self._store

    def recover(self) -> list[Job]:
        return self._store.recover_running_jobs()

    def run_once(self) -> Job | None:
        queued = self._store.list_queued(limit=1)
        if not queued:
            return None

        job = queued[0]
        running = job.transition_running()
        self._store.update(running)

        try:
            context = None
            if self._observability is not None:
                identity = hashlib.sha256(running.job_id.encode()).hexdigest()
                context = self._observability.derive(
                    correlation_id=identity, trace_id=identity, job_id=running.job_id,
                    run_id=running.payload.get("run_id"), site_id=None, span_id=None,
                )
            with lifecycle_span(context, event_type=ObservabilityEventType.JOB,
                                operation="job.execute",
                                attributes={"job_type": running.job_type,
                                            "attempt": running.attempt_count}):
                handler = self._handlers.resolve(running.job_type)
                result = handler(dict(running.payload))
            completed = running.complete(result)
        except Exception as exc:
            completed = running.retry_or_fail(str(exc))

        self._store.update(completed)
        return completed

    def run_forever(
        self,
        *,
        poll_interval_seconds: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative")

        event = stop_event or threading.Event()
        self.recover()

        while not event.is_set():
            processed = self.run_once()
            if processed is None:
                self._sleep_fn(poll_interval_seconds)

