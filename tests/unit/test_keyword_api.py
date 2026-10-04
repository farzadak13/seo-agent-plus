"""Keyword data through the API: per-customer budget, rankings only for a proven domain."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.keyword_intel.seosignal import SeoSignalRankTracker, SeoSignalSearchVolume
from app.models.sites import GSCConnectionConfig, SecretProvider, SecretRef, Site
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runtime.keywords import KeywordIntel
from tests.unit.test_keyword_intel import NOW, FakeSeoSignal, client as seosignal_client


AUTH = {"Authorization": "Bearer k"}


def make(site=None, *, tenant_budget=10, daily_budget=40, ranks=True):
    repository = InMemoryRepository()
    fake = FakeSeoSignal()
    seo = seosignal_client(fake)
    keywords = KeywordIntel(
        volumes=SeoSignalSearchVolume(seo, clock=lambda: NOW),
        ranks=SeoSignalRankTracker(seo, clock=lambda: NOW) if ranks else None,
        repository=repository,
        daily_budget=daily_budget,
        tenant_daily_budget=tenant_budget,
        clock=lambda: NOW,
    )
    sites = SiteStore(repository)
    if site is not None:
        sites.create(site)
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
            site_store=sites,
            keywords=keywords,
        )
    )
    return TestClient(app), fake


def tennisino(**changes):
    return Site(site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/", **changes)


def test_volume_lookup_through_the_api():
    client, fake = make()
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
    client, fake = make(tenant_budget=1)
    client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["کفش مردانه"]})
    again = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["کفش مردانه"]})
    assert again.json()["pending"] == [] and again.json()["requests_made"] == 0


def test_too_many_keywords_in_one_request_are_refused():
    client, _ = make()
    response = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["k"] * 201})
    assert response.status_code == 422


def test_rankings_need_a_proven_domain():
    client, fake = make(tennisino())
    response = client.get("/v1/sites/s1/rankings", headers=AUTH)
    assert response.status_code == 409
    assert fake.calls == [], "the provider is not even asked"


@pytest.mark.parametrize(
    "proof",
    [
        {"ownership_method": "meta_tag", "ownership_verified_at": datetime(2026, 10, 1, tzinfo=timezone.utc)},
        {"gsc": GSCConnectionConfig(
            property_url="sc-domain:tennisino.com",
            credential_ref=SecretRef(provider=SecretProvider.DATABASE, key="tenant:p1/google_refresh_token"),
            auth_mode="oauth_refresh_token",
        )},
    ],
)
def test_a_proven_site_sees_its_own_domains_rankings(proof):
    client, _ = make(tennisino(**proof))
    response = client.get("/v1/sites/s1/rankings?device=desktop&days=7", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["domain"] == "tennisino.com"
    assert body["keywords"][0]["keyword"] == "راکت تنیس"


def test_a_domain_without_a_project_says_so():
    other = Site(
        site_id="s1", principal_id="p1", name="O", base_url="https://other.ir/",
        ownership_method="meta_tag", ownership_verified_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    client, _ = make(other)
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


def test_a_google_grant_on_someone_elses_domain_proves_nothing():
    """The attack the review found: a site named after a competitor's domain,
    holding the customer's own property, must not unlock the competitor's
    rankings from the operator's shared rank-tracker account."""
    impostor = Site(
        site_id="s1", principal_id="p1", name="X", base_url="https://tennisino.com/",
        gsc=GSCConnectionConfig(
            property_url="sc-domain:mysite.ir",
            credential_ref=SecretRef(provider=SecretProvider.DATABASE, key="tenant:p1/google_refresh_token"),
            auth_mode="oauth_refresh_token",
        ),
    )
    client, fake = make(impostor)
    assert client.get("/v1/sites/s1/rankings", headers=AUTH).status_code == 409
    assert fake.calls == []


def test_an_overlong_keyword_is_refused():
    client, _ = make()
    response = client.post("/v1/keywords/volume", headers=AUTH, json={"keywords": ["k" * 201]})
    assert response.status_code == 422
