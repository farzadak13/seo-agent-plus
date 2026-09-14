from app.jobs.store import JobStore
from app.models.jobs import Job, JobStatus
from app.persistence.memory import InMemoryRepository


def make_store():
    return JobStore(InMemoryRepository())


def make_job(**overrides):
    data = {
        "job_id": "job-001",
        "job_type": "echo",
        "principal_id": "principal-1",
        "payload": {"value": 1},
    }
    data.update(overrides)
    return Job(**data)


def test_create_and_get_roundtrip():
    store = make_store()
    job = make_job()

    store.create(job)
    loaded = store.get(job.job_id)

    assert loaded == job
    assert loaded.status == JobStatus.QUEUED


def test_update_increments_persistence_version():
    store = make_store()
    job = make_job()
    store.create(job)

    running = job.transition_running()
    store.update(running)

    assert store.get(job.job_id).status == JobStatus.RUNNING


def test_completed_job_persists_result():
    store = make_store()
    running = make_job().transition_running()
    store.create(make_job())
    store.update(running)

    completed = running.complete({"ok": True})
    store.update(completed)

    loaded = store.get("job-001")
    assert loaded.status == JobStatus.COMPLETED
    assert loaded.result == {"ok": True}


def test_failed_job_persists_error():
    store = make_store()
    job = make_job()
    store.create(job)
    running = job.transition_running()
    store.update(running)

    failed = running.fail("boom")
    store.update(failed)

    assert store.get("job-001").status == JobStatus.FAILED
    assert store.get("job-001").error == "boom"


def test_cancelled_job_persists():
    store = make_store()
    job = make_job()
    store.create(job)

    cancelled = job.cancel()
    store.update(cancelled)

    assert store.get("job-001").status == JobStatus.CANCELLED


def test_queue_is_fifo_by_created_at():
    store = make_store()
    first = make_job(job_id="job-1")
    second = make_job(job_id="job-2")
    store.create(first)
    store.create(second)

    queued = store.list_queued(limit=10)

    assert [job.job_id for job in queued] == ["job-1", "job-2"]


def test_running_job_is_recovered_to_queue_when_attempts_remain():
    store = make_store()
    job = make_job(max_attempts=2)
    store.create(job)
    running = job.transition_running()
    store.update(running)

    recovered = store.recover_running_jobs()

    assert len(recovered) == 1
    assert recovered[0].status == JobStatus.QUEUED
    assert recovered[0].attempt_count == 1
    assert store.get("job-001").status == JobStatus.QUEUED


def test_running_job_is_failed_after_attempt_limit():
    store = make_store()
    job = make_job(max_attempts=1)
    store.create(job)
    running = job.transition_running()
    store.update(running)

    recovered = store.recover_running_jobs()

    assert recovered[0].status == JobStatus.FAILED
    assert "maximum attempts" in (recovered[0].error or "")
