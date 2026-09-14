from app.models.sites import (
    GSCConnectionConfig,
    SecretProvider,
    SecretRef,
    Site,
    SiteAdapterConnection,
    SiteStatus,
)
from app.onboarding.secrets import EnvironmentSecretResolver, SecretResolutionError
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository


def make_site(**overrides):
    data = {
        "site_id": "site-001",
        "principal_id": "principal-1",
        "name": "Example",
        "base_url": "https://example.com",
    }
    data.update(overrides)
    return Site(**data)


def test_site_defaults_to_active():
    assert make_site().status == SiteStatus.ACTIVE


def test_site_store_roundtrip():
    store = SiteStore(InMemoryRepository())
    site = make_site()
    store.create(site)
    assert store.get(site.site_id) == site


def test_site_update_is_versioned_and_persists_connection():
    store = SiteStore(InMemoryRepository())
    site = make_site()
    store.create(site)
    updated = site.model_copy(
        update={
            "gsc": GSCConnectionConfig(
                property_url="https://example.com",
                credential_ref=SecretRef(key="GSC_ACCESS_TOKEN"),
            ),
            "site_adapter": SiteAdapterConnection(
                adapter_type="wordpress",
                config={"username": "seo-agent"},
                secret_refs={"application_password": SecretRef(key="WP_APP_PASSWORD")},
            ),
        }
    )
    saved = store.update(updated)
    loaded = store.get(site.site_id)
    assert saved.gsc is not None
    assert loaded.gsc.credential_ref.key == "GSC_ACCESS_TOKEN"
    assert loaded.site_adapter is not None
    assert loaded.site_adapter.secret_refs["application_password"].key == "WP_APP_PASSWORD"


def test_site_model_never_contains_raw_credentials():
    site = make_site(
        gsc=GSCConnectionConfig(
            property_url="https://example.com",
            credential_ref=SecretRef(key="GSC_ACCESS_TOKEN"),
        )
    )
    dumped = site.model_dump(mode="json")
    assert "super-secret-token" not in str(dumped)
    assert dumped["gsc"]["credential_ref"]["key"] == "GSC_ACCESS_TOKEN"


def test_environment_secret_resolver_reads_secret(monkeypatch):
    monkeypatch.setenv("GSC_ACCESS_TOKEN", "secret-value")
    value = EnvironmentSecretResolver().resolve(SecretRef(key="GSC_ACCESS_TOKEN"))
    assert value == "secret-value"


def test_environment_secret_resolver_rejects_missing_secret(monkeypatch):
    monkeypatch.delenv("GSC_ACCESS_TOKEN", raising=False)
    try:
        EnvironmentSecretResolver().resolve(SecretRef(key="GSC_ACCESS_TOKEN"))
    except SecretResolutionError as exc:
        assert "GSC_ACCESS_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected missing secret to fail")


def test_secret_provider_is_explicit():
    ref = SecretRef(key="TOKEN")
    assert ref.provider == SecretProvider.ENVIRONMENT
