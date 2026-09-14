from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from functools import wraps
from typing import Any, TypeVar

from app.models.observability import (
    ObservabilityEventType,
)
from app.observability.context import ObservabilityContext

T = TypeVar("T")


@contextmanager
def operation_span(
    context: ObservabilityContext,
    *,
    event_type: ObservabilityEventType,
    operation: str,
    started_message: str,
    completed_message: str,
    failed_message: str,
    attributes: Mapping[str, object] | None = None,
):
    """Emit start/completion/failure events and a duration metric.

    The wrapped operation remains authoritative: observability failures are not
    allowed to replace the business exception.
    """
    from app.observability.lifecycle import lifecycle_span
    with lifecycle_span(context, event_type=event_type, operation=operation,
                        attributes=attributes, activate_child=False,
                        messages={"started": started_message, "completed": completed_message,
                                  "failed": failed_message}):
        yield


def instrument_callable(
    callable_: Callable[..., T],
    *,
    context: ObservabilityContext,
    event_type: ObservabilityEventType,
    operation: str,
    started_message: str,
    completed_message: str,
    failed_message: str,
    attributes_factory: Callable[..., Mapping[str, object] | None] | None = None,
) -> Callable[..., T]:
    """Return an observability-instrumented callable.

    The wrapper does not modify return values, arguments, or exception types.
    """

    @wraps(callable_)
    def wrapped(*args: Any, **kwargs: Any) -> T:
        attributes = (
            attributes_factory(*args, **kwargs)
            if attributes_factory is not None
            else None
        )
        with context.activate():
            with operation_span(
                context,
                event_type=event_type,
                operation=operation,
                started_message=started_message,
                completed_message=completed_message,
                failed_message=failed_message,
                attributes=attributes,
            ):
                return callable_(*args, **kwargs)

    return wrapped


def instrument_job_handler(
    handler: Callable[[dict[str, Any]], T],
    *,
    context: ObservabilityContext,
) -> Callable[[dict[str, Any]], T]:
    return instrument_callable(
        handler,
        context=context,
        event_type=ObservabilityEventType.JOB,
        operation="job.execute",
        started_message="Job execution started.",
        completed_message="Job execution completed.",
        failed_message="Job execution failed.",
        attributes_factory=lambda payload: {
            "job_type": str(payload.get("job_type", "unknown")),
        },
    )


def instrument_run(
    runner: Callable[..., T],
    *,
    context: ObservabilityContext,
) -> Callable[..., T]:
    return instrument_callable(
        runner,
        context=context,
        event_type=ObservabilityEventType.RUN,
        operation="run.execute",
        started_message="SEO run execution started.",
        completed_message="SEO run execution completed.",
        failed_message="SEO run execution failed.",
    )


def instrument_pipeline(
    pipeline: Callable[..., T],
    *,
    context: ObservabilityContext,
) -> Callable[..., T]:
    return instrument_callable(
        pipeline,
        context=context,
        event_type=ObservabilityEventType.PIPELINE,
        operation="pipeline.execute",
        started_message="Decision pipeline started.",
        completed_message="Decision pipeline completed.",
        failed_message="Decision pipeline failed.",
    )


def instrument_execution(
    executor: Callable[..., T],
    *,
    context: ObservabilityContext,
) -> Callable[..., T]:
    return instrument_callable(
        executor,
        context=context,
        event_type=ObservabilityEventType.EXECUTION,
        operation="execution.execute",
        started_message="SEO execution started.",
        completed_message="SEO execution completed.",
        failed_message="SEO execution failed.",
    )

