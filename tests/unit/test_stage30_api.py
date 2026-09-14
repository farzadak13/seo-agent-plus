from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.persistence.memory import InMemoryRepository


API_KEY = "stage30-secret"


def make_client():
    repository = InMemoryRepository()
    store = JobStore(repository)
    registry = JobHandlerRegistry()
    registry.register(
        "echo",
        lambda payload: {"echo": payload},
    )
    scheduler = JobScheduler(
        store=store,
        handlers=registry,
    )
    app = create_app(
        APIDependencies(
            scheduler=scheduler,
            authenticator=APIKeyAuthenticator(
                API_KEY,
                principal_id="principal-1",
            ),
            job_id_factory=lambda: "job-api-001",
        )
    )
    return TestClient(app), scheduler


def headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def test_health_does_not_require_authentication():
    client, _ = make_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_job_requires_authentication():
    client, _ = make_client()

    response = client.post(
        "/v1/jobs",
        json={"job_type": "echo", "payload": {"x": 1}},
    )

    assert response.status_code == 401


def test_create_job_returns_accepted_job():
    client, _ = make_client()

    response = client.post(
        "/v1/jobs",
        headers=headers(),
        json={"job_type": "echo", "payload": {"x": 1}},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == "job-api-001"
    assert body["status"] == "queued"
    assert body["principal_id"] == "principal-1"


def test_get_job_returns_owner_job():
    client, scheduler = make_client()
    response = client.post(
        "/v1/jobs",
        headers=headers(),
        json={"job_type": "echo", "payload": {"x": 1}},
    )
    assert response.status_code == 202

    response = client.get(
        "/v1/jobs/job-api-001",
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert scheduler.store.get("job-api-001").payload == {"x": 1}


def test_get_unknown_job_returns_404():
    client, _ = make_client()

    response = client.get(
        "/v1/jobs/does-not-exist",
        headers=headers(),
    )

    assert response.status_code == 404


def test_cancel_queued_job():
    client, _ = make_client()
    client.post(
        "/v1/jobs",
        headers=headers(),
        json={"job_type": "echo"},
    )

    response = client.post(
        "/v1/jobs/job-api-001/cancel",
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_completed_job_can_be_read_after_scheduler_run():
    client, scheduler = make_client()
    client.post(
        "/v1/jobs",
        headers=headers(),
        json={"job_type": "echo", "payload": {"x": 7}},
    )
    scheduler.run_once()

    response = client.get(
        "/v1/jobs/job-api-001",
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["result"] == {"echo": {"x": 7}}


def test_foreign_principal_cannot_read_job():
    client, scheduler = make_client()
    client.post(
        "/v1/jobs",
        headers=headers(),
        json={"job_type": "echo"},
    )

    foreign_app = create_app(
        APIDependencies(
            scheduler=scheduler,
            authenticator=APIKeyAuthenticator(
                "other-secret",
                principal_id="principal-2",
            ),
        )
    )
    foreign_client = TestClient(foreign_app)

    response = foreign_client.get(
        "/v1/jobs/job-api-001",
        headers={"Authorization": "Bearer other-secret"},
    )

    assert response.status_code == 404
