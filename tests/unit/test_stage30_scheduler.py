from app.jobs import JobHandlerRegistry, JobScheduler
from app.jobs.store import JobStore
from app.models.jobs import JobStatus
from app.persistence.memory import InMemoryRepository
from app.models.jobs import Job


def make_job(job_id="job-001", job_type="echo"):
    return Job(
        job_id=job_id,
        job_type=job_type,
        principal_id="principal-1",
        payload={"value": 42},
    )


def make_scheduler():
    store = JobStore(InMemoryRepository())
    registry = JobHandlerRegistry()
    return store, registry, JobScheduler(store=store, handlers=registry)


def test_successful_job_is_executed():
    store, registry, scheduler = make_scheduler()
    registry.register("echo", lambda payload: {"value": payload["value"]})
    store.create(make_job())

    result = scheduler.run_once()

    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.result == {"value": 42}
    assert store.get("job-001").status == JobStatus.COMPLETED


def test_missing_handler_fails_job_without_raising():
    store, _, scheduler = make_scheduler()
    store.create(make_job(job_type="missing"))

    result = scheduler.run_once()

    assert result is not None
    assert result.status == JobStatus.FAILED
    assert "No handler registered" in (result.error or "")


def test_empty_queue_returns_none():
    _, _, scheduler = make_scheduler()
    assert scheduler.run_once() is None


def test_recovery_runs_before_new_execution():
    store, registry, scheduler = make_scheduler()
    registry.register("echo", lambda payload: {"ok": True})
    job = make_job()
    job = job.model_copy(update={"max_attempts": 2})
    store.create(job)
    store.update(job.transition_running())

    scheduler.recover()
    result = scheduler.run_once()

    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.attempt_count == 2


def test_handler_registry_rejects_duplicate_registration():
    registry = JobHandlerRegistry()
    registry.register("echo", lambda payload: {})

    try:
        registry.register("echo", lambda payload: {})
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("Expected duplicate registration to fail")


def test_handler_exception_is_retried_until_max_attempts():
    store, registry, scheduler = make_scheduler()
    calls = {"count": 0}

    def failing_handler(payload):
        calls["count"] += 1
        raise RuntimeError("temporary failure")

    registry.register("retry", failing_handler)
    job = make_job(job_type="retry").model_copy(update={"max_attempts": 3})
    store.create(job)

    first = scheduler.run_once()
    assert first is not None
    assert first.status == JobStatus.QUEUED
    assert first.attempt_count == 1
    assert first.error == "temporary failure"

    second = scheduler.run_once()
    assert second is not None
    assert second.status == JobStatus.QUEUED
    assert second.attempt_count == 2

    third = scheduler.run_once()
    assert third is not None
    assert third.status == JobStatus.FAILED
    assert third.attempt_count == 3
    assert third.error == "temporary failure"
    assert calls["count"] == 3


def test_handler_exception_fails_immediately_when_max_attempts_is_one():
    store, registry, scheduler = make_scheduler()

    registry.register("fail", lambda payload: (_ for _ in ()).throw(
        RuntimeError("permanent failure")
    ))
    store.create(make_job(job_type="fail"))

    result = scheduler.run_once()

    assert result is not None
    assert result.status == JobStatus.FAILED
    assert result.attempt_count == 1
    assert result.error == "permanent failure"
