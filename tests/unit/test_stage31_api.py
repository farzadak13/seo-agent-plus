from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runs.store import RunStore
from tests.unit.ownership_helpers import mark_verified


API_KEY = "stage31-secret"


def make_client():
    repository = InMemoryRepository()
    site_store = SiteStore(repository)
    run_store = RunStore(repository)
    handlers = JobHandlerRegistry()
    scheduler = JobScheduler(
        store=JobStore(repository),
        handlers=handlers,
    )
    app = create_app(
        APIDependencies(
            scheduler=scheduler,
            authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
            site_store=site_store,
            run_store=run_store,
            site_id_factory=lambda: "site-api-001",
            run_id_factory=lambda: "run-api-001",
            job_id_factory=lambda: "job-api-031",
        )
    )
    return TestClient(app), scheduler, site_store, run_store


def headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def test_create_site_requires_authentication():
    client, *_ = make_client()
    response = client.post(
        "/v1/sites",
        json={"name": "Example", "base_url": "https://example.com"},
    )
    assert response.status_code == 401


def test_create_site():
    client, *_ = make_client()
    response = client.post(
        "/v1/sites",
        headers=headers(),
        json={"name": "Example", "base_url": "https://example.com"},
    )
    assert response.status_code == 201
    assert response.json()["site_id"] == "site-api-001"
    assert response.json()["gsc_configured"] is False


def test_get_site():
    client, *_ = make_client()
    client.post(
        "/v1/sites",
        headers=headers(),
        json={"name": "Example", "base_url": "https://example.com"},
    )
    response = client.get("/v1/sites/site-api-001", headers=headers())
    assert response.status_code == 200
    assert response.json()["name"] == "Example"


def test_configure_gsc():
    client, _, site_store, _ = make_client()
    client.post(
        "/v1/sites",
        headers=headers(),
        json={"name": "Example", "base_url": "https://example.com"},
    )
    mark_verified(site_store, "site-api-001")
    response = client.put(
        "/v1/sites/site-api-001/connections/gsc",
        headers=headers(),
        json={
            "property_url": "https://example.com",
            "credential_ref": "GSC_ACCESS_TOKEN",
        },
    )
    assert response.status_code == 200
    assert response.json()["gsc_configured"] is True


def test_configure_site_adapter():
    client, *_ = make_client()
    client.post(
        "/v1/sites",
        headers=headers(),
        json={"name": "Example", "base_url": "https://example.com"},
    )
    response = client.put(
        "/v1/sites/site-api-001/connections/site-adapter",
        headers=headers(),
        json={
            "adapter_type": "wordpress",
            "config": {"username": "seo-agent"},
            "secret_refs": {"application_password": "SEO_AGENT_SITE_SECRET_WP_PASSWORD"},
        },
    )
    assert response.status_code == 200
    assert response.json()["site_adapter_configured"] is True


def test_create_run_requires_gsc():
    client, *_ = make_client()
    client.post(
        "/v1/sites",
        headers=headers(),
        json={"name": "Example", "base_url": "https://example.com"},
    )
    response = client.post(
        "/v1/sites/site-api-001/runs",
        headers=headers(),
        json={
            "start_date": "2026-09-01",
            "end_date": "2026-09-12",
            "normalized_url": "https://example.com/page",
            "normalized_query": "کفش",
            "candidate_id": "candidate-1",
        },
    )
    assert response.status_code == 409


def test_create_and_get_run():
    client, scheduler, site_store, run_store = make_client()
    client.post(
        "/v1/sites",
        headers=headers(),
        json={"name": "Example", "base_url": "https://example.com"},
    )
    mark_verified(site_store, "site-api-001")
    client.put(
        "/v1/sites/site-api-001/connections/gsc",
        headers=headers(),
        json={
            "property_url": "https://example.com",
            "credential_ref": "GSC_ACCESS_TOKEN",
        },
    )
    response = client.post(
        "/v1/sites/site-api-001/runs",
        headers=headers(),
        json={
            "start_date": "2026-09-01",
            "end_date": "2026-09-12",
            "normalized_url": "https://example.com/page",
            "normalized_query": "کفش",
            "candidate_id": "candidate-1",
        },
    )
    assert response.status_code == 202
    assert response.json()["run_id"] == "run-api-001"
    assert response.json()["status"] == "queued"

    fetched = client.get("/v1/runs/run-api-001", headers=headers())
    assert fetched.status_code == 200
    assert fetched.json()["job_id"] == "job-api-031"
    assert scheduler.store.get("job-api-031").payload == {"run_id": "run-api-001"}
    assert run_store.get("run-api-001").site_id == "site-api-001"
