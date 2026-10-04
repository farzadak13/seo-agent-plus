"""Stage 37/38 against a real PostgreSQL: approve, execute, roll back, restart.

The in-memory repository has hidden a schema constraint once before. These
tests run the approval path through the real application and the real
database; only the WordPress site is a fake.
"""
from __future__ import annotations

import os

import pytest

from tests.unit.test_stage37_38_actions import (
    NEW_TITLE,
    OLD_TITLE,
    FakeAdapterFactory,
    FakePageAdapter,
    make_pending,
)

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")
pytest.importorskip("fastapi", reason="fastapi is required for runtime tests")

if not DSN:
    pytest.skip(
        "SEO_AGENT_TEST_DSN is not set; live action tests are skipped.",
        allow_module_level=True,
    )

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(
        f"SEO_AGENT_TEST_DSN is set but the database cannot be reached. Error: {_exc}",
        allow_module_level=True,
    )

from app.action.executor import (  # noqa: E402
    EXECUTE_ACTION_JOB_TYPE,
    ROLLBACK_ACTION_JOB_TYPE,
    build_execute_action_handler,
    build_rollback_action_handler,
)
from app.persistence.migrator import apply_migrations  # noqa: E402
from app.runtime.config import RuntimeConfig  # noqa: E402
from app.runtime.container import create_runtime_app  # noqa: E402

API_KEY = "integration-test-key-not-a-secret"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_SITE_SECRET_TEST_USER", "editor")
    monkeypatch.setenv("SEO_AGENT_SITE_SECRET_TEST_PASSWORD", "xxxx xxxx xxxx xxxx")
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS persistence_records, schema_migrations")
    apply_migrations(dsn=DSN, directory="migrations")


def build_application(adapter):
    from fastapi.testclient import TestClient

    app = create_runtime_app(
        RuntimeConfig(
            environment="test",
            api_key=API_KEY,
            database_dsn=DSN,
            worker_enabled=False,
            gsc_mode="stub",
        )
    )
    runtime = app.state.runtime
    factory = FakeAdapterFactory(adapter)
    for job_type, build in (
        (EXECUTE_ACTION_JOB_TYPE, build_execute_action_handler),
        (ROLLBACK_ACTION_JOB_TYPE, build_rollback_action_handler),
    ):
        runtime.handlers.register(
            job_type,
            build(
                action_store=runtime.action_store,
                site_store=runtime.site_store,
                adapter_factory=factory,
            ),
            replace=True,
        )
    return TestClient(app), runtime


def onboard(client, runtime):
    created = client.post(
        "/v1/sites", json={"name": "Shop", "base_url": "https://example.com/"}, headers=AUTH
    )
    assert created.status_code == 201, created.text
    site_id = created.json()["site_id"]
    connected = client.put(
        f"/v1/sites/{site_id}/connections/site-adapter",
        json={
            "adapter_type": "wordpress",
            "config": {},
            "secret_refs": {
                "username": "SEO_AGENT_SITE_SECRET_TEST_USER",
                "application_password": "SEO_AGENT_SITE_SECRET_TEST_PASSWORD",
            },
        },
        headers=AUTH,
    )
    assert connected.status_code == 200, connected.text

    pending = make_pending(tenant="admin")
    pending = pending.model_copy(
        update={"action": pending.action.model_copy(update={"site_id": site_id})}
    )
    runtime.action_store.create(pending)
    return site_id, pending.action_id


def test_approve_execute_and_roll_back_through_the_real_database():
    adapter = FakePageAdapter()
    client, runtime = build_application(adapter)
    site_id, action_id = onboard(client, runtime)

    listed = client.get(f"/v1/sites/{site_id}/actions", headers=AUTH)
    assert listed.status_code == 200, listed.text
    assert [item["action_id"] for item in listed.json()["actions"]] == [action_id]

    approved = client.post(f"/v1/actions/{action_id}/approve", headers=AUTH, json={})
    assert approved.status_code == 202, approved.text
    runtime.scheduler.run_once()

    applied = client.get(f"/v1/actions/{action_id}", headers=AUTH).json()
    assert applied["status"] == "measurement_window_active", applied
    assert adapter.title == NEW_TITLE

    rolled = client.post(f"/v1/actions/{action_id}/rollback", headers=AUTH, json={})
    assert rolled.status_code == 202, rolled.text
    runtime.scheduler.run_once()

    final = client.get(f"/v1/actions/{action_id}", headers=AUTH).json()
    assert final["status"] == "rolled_back", final
    assert adapter.title == OLD_TITLE
    reasons = [event["reason"] for event in final["history"]]
    assert reasons[0] == "title_proposal_generated"
    assert reasons[-1] == "rolled_back"


def test_an_approval_survives_a_restart_before_the_worker_runs():
    adapter = FakePageAdapter()
    client, runtime = build_application(adapter)
    site_id, action_id = onboard(client, runtime)
    assert client.post(f"/v1/actions/{action_id}/approve", headers=AUTH, json={}).status_code == 202

    # A new process: nothing carried over but the database.
    _, restarted = build_application(adapter)
    restarted.scheduler.run_once()

    assert restarted.action_store.get(action_id).status.value == "measurement_window_active"
    assert adapter.title == NEW_TITLE
