# Stage 32-B — Observability Lifecycle Integration

This stage connects the Stage 32 observability core to execution boundaries without
coupling SEO logic to a telemetry implementation.

Delivered:
- generic callable instrumentation
- Job handler instrumentation
- Run instrumentation
- Decision pipeline instrumentation
- Execution instrumentation
- automatic success/failure events
- duration metrics and total counters
- ContextVar propagation across nested operations
- business return values and exceptions remain authoritative
- no credential or secret payload logging

Integration rule:
Observability is non-authoritative. It may record an operation, but it must never
replace the business result or business exception.

Suggested lifecycle:
JOB -> RUN -> PIPELINE -> LLM / EXECUTION

The same ObservabilityContext should be reused across nested boundaries so the
correlation_id and trace_id remain stable while each boundary receives its own
span_id.
