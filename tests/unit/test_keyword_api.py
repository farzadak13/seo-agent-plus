"""Keyword demand through the API, within a per-customer budget."""
import pytest
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.keyword_intel.seosignal import SeoSignalSearchVolume
from app.persistence.memory import InMemoryRepository
from app.runtime.keywords import KeywordIntel
from tests.unit.test_keyword_intel import NOW, FakeSeoSignal, client as seosignal_client


AUTH = {"Authorization": "Bearer k"}


def make(*, tenant_budget=10, daily_budget=40):
    repository = InMemoryRepository()
    fake = FakeSeoSignal()
    keywords = KeywordIntel(
        volumes=SeoSignalSearchVolume(seosignal_client(fake), clock=lambda: NOW),
        repository=repository,
        daily_budget=daily_budget,
        tenant_daily_budget=tenant_budget,
        clock=lambda: NOW,
    )
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
            keywords=keywords,
        )
    )
    return TestClient(app), fake


def test_volume_lookup_through_the_api():
    client, _ = make()
    response = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["کفش مردانه"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["volumes"][0]["search_volume"] == 12000
    assert body["volumes"][0]["competition"] == "medium"
    assert body["requests_made"] == 1


def test_one_customer_cannot_spend_everyones_budget():
    client, fake = make(tenant_budget=1)
    client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["کفش مردانه"]})

    second = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["کفش ورزشی"]})

    assert second.json()["pending"] == ["کفش ورزشی"]
    assert len(fake.calls) == 1


def test_cached_answers_cost_a_customer_nothing():
    client, _ = make(tenant_budget=1)
    client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["کفش مردانه"]})
    again = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["کفش مردانه"]})
    assert again.json()["pending"] == [] and again.json()["requests_made"] == 0


def test_too_many_keywords_in_one_request_are_refused():
    client, _ = make()
    response = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["k"] * 201})
    assert response.status_code == 422


def test_an_overlong_keyword_is_refused():
    client, _ = make()
    response = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["k" * 201]})
    assert response.status_code == 422


def test_rank_tracking_is_not_offered():
    client, _ = make()
    assert client.get("/v1/sites/s1/rankings", headers=AUTH).status_code == 404


def test_keyword_data_is_off_until_configured():
    repository = InMemoryRepository()
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
        )
    )
    response = TestClient(app).post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["x"]})
    assert response.status_code == 503


def test_the_provider_is_configuration_not_code():
    from app.runtime.config import RuntimeConfig

    with pytest.raises(ValueError, match="SEOSIGNAL_API_KEY_REF"):
        RuntimeConfig(api_key="k", database_dsn="d", keyword_provider="seosignal")
    with pytest.raises(ValueError):
        RuntimeConfig(api_key="k", database_dsn="d", keyword_provider="somethingelse")
