"""Instrumentation at real lifecycle boundaries; no payloads or secrets are logged."""
from contextlib import contextmanager, nullcontext
from functools import wraps
from inspect import signature
from time import perf_counter

from app.models.observability import ObservabilityEventType, ObservabilityLevel
from app.observability.context import get_current_context


def safe_call(fn, *args, **kwargs):
    # Export failures must never alter business return values or exceptions.
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


@contextmanager
def lifecycle_span(context, *, event_type, operation, attributes=None, activate_child=True, messages=None, **identities):
    if context is None:
        yield {}
        return
    messages = messages or {}
    span_id = context.child_span()
    fields = dict(identities)
    fields.update(event_type=event_type, span_id=span_id, parent_span_id=context.span_id)
    state = {"status": "success", "attributes": dict(attributes or {})}
    started = perf_counter()
    safe_call(context.event, **fields, operation=operation + ".started",
              message=messages.get("started", operation + " started."), attributes=state["attributes"])
    try:
        with (context.derive(span_id=span_id).activate() if activate_child else nullcontext()):
            yield state
    except Exception as exc:
        state["status"] = "failed"
        state["attributes"]["error_type"] = type(exc).__name__
        raise
    finally:
        status = state["status"]
        suffix = {"success": "completed", "failed": "failed", "rejected": "rejected"}[status]
        safe_call(context.event, **fields, operation=operation + "." + suffix,
                  message=messages.get(suffix, operation + " " + suffix + "."),
                  level=ObservabilityLevel.ERROR if status == "failed" else ObservabilityLevel.INFO,
                  attributes=state["attributes"])
        safe_call(context.increment, operation + ".total", labels={"status": status})
        safe_call(context.duration, operation + ".duration_ms",
                  (perf_counter() - started) * 1000, labels={"status": status})


def observe(event_type, operation):
    """Use ambient context, preserving callable signatures and business results."""
    def decorate(fn):
        sig = signature(fn)
        @wraps(fn)
        def wrapped(*args, **kwargs):
            context = get_current_context()
            if context is None:
                return fn(*args, **kwargs)
            arguments = sig.bind(*args, **kwargs).arguments
            action = arguments.get("action")
            reasoning_input = arguments.get("reasoning_input")
            identities = {}
            if action is not None:
                identities["action_id"] = action.action_id
            if reasoning_input is not None:
                provider_id = getattr(arguments.get("self"), "provider_id", None)
                if provider_id is not None:
                    identities["provider_id"] = provider_id
                identities["logical_call_id"] = (arguments.get("logical_call_id") or
                    "reasoning:" + reasoning_input.recommendation_id)
            with lifecycle_span(context, event_type=event_type, operation=operation,
                                **identities) as state:
                result = fn(*args, **kwargs)
                status = getattr(result, "status", None)
                if status is not None:
                    status = getattr(status, "value", status)
                    state["attributes"]["result_status"] = str(status)
                    if status == "failed":
                        state["status"] = "failed"
                    elif str(status).startswith("rejected") or status == "blocked":
                        state["status"] = "rejected"
                return result
        return wrapped
    return decorate
