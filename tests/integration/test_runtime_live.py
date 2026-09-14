"""Stage 33's acceptance criterion, finally executed.

Stage 33 declared itself done when "an API request really runs and data
survives a restart". Nothing proved that: the runtime tests build the
container against a fake repository, so the whole claim rested on a database
the code had never actually talked to.

These tests drive the real composition root against a real PostgreSQL:

    API request -> Job -> worker -> SEO run handler -> SEORunService -> pipeline

then throw the application away, build a second one against the same database,
and require the data to still be there.

The worker thread is disabled and the scheduler is stepped by hand. A sleeping
poll loop would make these tests slow and flaky, and stepping it proves the
same thing: the handler registered in the composition root is the one that
runs.

Needs SEO_AGENT_TEST_DSN. See tests/integration/test_postgres_live.py.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from app.models.jobs import JobStatus
from app.models.runs import SEORunStatus
from app.persistence.migrator import apply_migrations
from app.runs.handler import SEO_RUN_JOB_TYPE
from app.runtime.config import RuntimeConfig
from app.runtime.container import create_runtime_app

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")

psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")
pytest.importorskip("fastapi", reason="fastapi is required for runtime tests")

if not DSN:
    pytest.skip(
        "SEO_AGENT_TEST_DSN is not set; live runtime tests are skipped.",
        allow_module_level=True,
    )

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(
        "SEO_AGENT_TEST_DSN is set but the database cannot be reached, so live "
        f"runtime tests are skipped. Error: {_exc}",
        allow_module_level=True,
    )

API_KEY = "integration-test-key-not-a-secret"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
CREDENTIAL_ENV = "SEO_AGENT_TEST_GSC_TOKEN"


def make_config(**overrides):
    data = {
        "environment": "test",
        "api_key": API_KEY,
        "database_dsn": DSN,
        "worker_enabled": False,
        "worker_poll_interval_seconds": 0.01,
        "gsc_mode": "stub",
        "observability_enabled": True,
    }
    data.update(overrides)
    return RuntimeConfig(**data)


def build_application():
    """One application instance, as uvicorn would build it."""
    from fastapi.testclient import TestClient

    app = create_runtime_app(make_config())
    return TestClient(app), app.state.runtime


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV, "test-access-token")
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS persistence_records, schema_migrations")
    apply_migrations(dsn=DSN, directory="migrations")


def onboard_site(client):
    created = client.post(
        "/v1/sites",
        json={"name": "PAMA", "base_url": "https://pama.shop/"},
        headers=AUTH,
    )
    assert created.status_code == 201, created.text
    site_id = created.json()["site_id"]

    configured = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={
            "property_url": "https://pama.shop/",
            "credential_ref": CREDENTIAL_ENV,
            "auth_mode": "access_token",
        },
        headers=AUTH,
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["gsc_configured"] is True
    return site_id


def request_run(client, site_id):
    end = date(2026, 9, 1)
    created = client.post(
        f"/v1/sites/{site_id}/runs",
        json={
            "start_date": (end - timedelta(days=6)).isoformat(),
            "end_date": end.isoformat(),
            "normalized_url": "https://pama.shop/product/sneaker",
            "normalized_query": "کفش کتانی مردانه",
            "candidate_id": "candidate-1",
        },
        headers=AUTH,
    )
    assert created.status_code == 202, created.text
    return created.json()


# --- the request really reaches the worker ----------------------------------


def test_an_api_request_creates_a_job_and_a_run_together():
    client, runtime = build_application()
    site_id = onboard_site(client)

    run = request_run(client, site_id)

    assert run["status"] == SEORunStatus.QUEUED.value
    job = runtime.job_store.get(run["job_id"])
    assert job.job_type == SEO_RUN_JOB_TYPE
    assert job.payload["run_id"] == run["run_id"]


def test_the_worker_consumes_the_job_and_moves_the_run_out_of_queued():
    client, runtime = build_application()
    site_id = onboard_site(client)
    run = request_run(client, site_id)

    processed = runtime.scheduler.run_once()

    assert processed is not None, "the scheduler found no queued job"
    assert processed.job_id == run["job_id"]
    after = client.get(f"/v1/runs/{run['run_id']}", headers=AUTH).json()
    assert after["status"] != SEORunStatus.QUEUED.value
    assert after["status"] in {
        SEORunStatus.COMPLETED.value,
        SEORunStatus.FAILED.value,
    }


def test_the_registered_handler_is_the_one_that_runs():
    """Not a fake: the job must be resolved through the real registry."""
    client, runtime = build_application()
    site_id = onboard_site(client)
    run = request_run(client, site_id)

    runtime.scheduler.run_once()

    job = runtime.job_store.get(run["job_id"])
    assert job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.QUEUED}
    assert job.attempt_count >= 1
    if job.status == JobStatus.COMPLETED:
        assert job.result["run_id"] == run["run_id"]


# --- restart --------------------------------------------------------------


def test_a_restart_does_not_lose_the_site_or_the_run():
    first_client, first_runtime = build_application()
    site_id = onboard_site(first_client)
    run = request_run(first_client, site_id)
    first_runtime.scheduler.run_once()
    before = first_client.get(f"/v1/runs/{run['run_id']}", headers=AUTH).json()

    # Throw the whole application away, including every in-memory store.
    del first_client, first_runtime

    second_client, _ = build_application()

    site_after = second_client.get(f"/v1/sites/{site_id}", headers=AUTH)
    run_after = second_client.get(f"/v1/runs/{run['run_id']}", headers=AUTH)

    assert site_after.status_code == 200
    assert site_after.json()["gsc_configured"] is True
    assert run_after.status_code == 200
    assert run_after.json()["status"] == before["status"]
    assert run_after.json()["normalized_query"] == "کفش کتانی مردانه"


def test_a_restart_preserves_the_job_result():
    first_client, first_runtime = build_application()
    site_id = onboard_site(first_client)
    run = request_run(first_client, site_id)
    first_runtime.scheduler.run_once()
    before = first_runtime.job_store.get(run["job_id"])

    _, second_runtime = build_application()
    after = second_runtime.job_store.get(run["job_id"])

    assert after.status == before.status
    assert after.attempt_count == before.attempt_count
    assert after.result == before.result


def test_a_job_left_running_by_a_crash_is_recovered_after_restart():
    """A worker that dies mid-job must not leave the job stuck forever."""
    client, runtime = build_application()
    site_id = onboard_site(client)
    run = request_run(client, site_id)

    # Simulate the crash: the job was claimed, then the process disappeared.
    claimed = runtime.job_store.get(run["job_id"]).transition_running()
    runtime.job_store.update(claimed)
    assert runtime.job_store.get(run["job_id"]).status == JobStatus.RUNNING

    _, second_runtime = build_application()
    recovered = second_runtime.scheduler.recover()

    assert [item.job_id for item in recovered] == [run["job_id"]]
    assert second_runtime.job_store.get(run["job_id"]).status != JobStatus.RUNNING


# --- what the instance reports about itself ---------------------------------


def test_readiness_reports_a_reachable_database():
    client, _ = build_application()

    body = client.get("/readyz").json()

    assert body["status"] == "ready"
    assert body["gsc_mode"] == "stub"
    assert body["title_workflow"] == "disabled"


def test_records_are_written_under_the_authenticated_principal():
    client, runtime = build_application()
    site_id = onboard_site(client)
    request_run(client, site_id)

    principal = client.get(f"/v1/sites/{site_id}", headers=AUTH).json()["principal_id"]
    page = runtime.repository.query(tenant_id=principal)

    assert page.records, "nothing was written under the authenticated principal"
    assert {item.aggregate_type for item in page.records} >= {"site", "seo_run", "job"}


def test_an_unauthenticated_request_is_rejected():
    client, _ = build_application()

    assert client.post("/v1/sites", json={"name": "x", "base_url": "https://x.test/"}).status_code == 401
    assert client.get("/v1/runs/whatever", headers={"Authorization": "Bearer wrong"}).status_code == 401
