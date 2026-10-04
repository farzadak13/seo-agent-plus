"""Per-site credentials are stored encrypted and bound to where they belong."""
import pytest
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.sites import SecretProvider, SecretRef, Site
from app.onboarding.secrets import EnvironmentSecretResolver, SecretResolutionError
from app.onboarding.site_store import SiteStore
from app.onboarding.vault import (
    SECRET_AGGREGATE_TYPE,
    CompositeSecretResolver,
    Keyring,
    SecretVault,
    VaultConfigurationError,
    generate_key,
)
from app.persistence.memory import InMemoryRepository
from app.runtime.adapters import SiteAdapterFactory


def make_vault(*keys):
    repository = InMemoryRepository()
    return repository, SecretVault(repository, Keyring(list(keys) or [generate_key()]))


def test_a_value_round_trips_and_is_not_stored_in_clear():
    repository, vault = make_vault()
    ref = vault.put(tenant_id="t1", site_id="s1", name="application_password", value="abcd efgh")

    assert ref == SecretRef(provider=SecretProvider.DATABASE, key="s1/application_password")
    assert vault.get("s1/application_password") == "abcd efgh"
    [record] = repository.list(aggregate_type=SECRET_AGGREGATE_TYPE)
    assert "abcd" not in str(record.payload)
    assert record.tenant_id == "t1"


def test_a_value_moved_to_another_site_does_not_decrypt():
    repository, vault = make_vault()
    vault.put(tenant_id="t1", site_id="s1", name="application_password", value="secret")
    [record] = repository.list(aggregate_type=SECRET_AGGREGATE_TYPE)
    repository.create(
        record.model_copy(
            update={"record_id": "moved", "aggregate_id": "s2/application_password", "site_id": "s2"}
        )
    )
    with pytest.raises(SecretResolutionError):
        vault.get("s2/application_password")


def test_a_value_moved_to_another_tenant_does_not_decrypt():
    key = generate_key()
    repository, vault = make_vault(key)
    vault.put(tenant_id="t1", site_id="s1", name="username", value="editor")
    [record] = repository.list(aggregate_type=SECRET_AGGREGATE_TYPE)
    # The repository refuses to re-own a record, so build the forgery directly
    # in a database of its own, as someone with write access to the table could.
    forged = InMemoryRepository()
    forged.create(record.model_copy(update={"tenant_id": "t2"}))
    with pytest.raises(SecretResolutionError):
        SecretVault(forged, Keyring([key])).get("s1/username")


def test_replacing_a_value_keeps_one_record_with_a_new_version():
    repository, vault = make_vault()
    vault.put(tenant_id="t1", site_id="s1", name="username", value="old")
    vault.put(tenant_id="t1", site_id="s1", name="username", value="new")
    assert vault.get("s1/username") == "new"


def test_rotation_reads_old_values_and_writes_with_the_new_key():
    old, new = generate_key(), generate_key()
    repository = InMemoryRepository()
    SecretVault(repository, Keyring([old])).put(
        tenant_id="t1", site_id="s1", name="username", value="editor"
    )
    rotated = SecretVault(repository, Keyring([new, old]))
    assert rotated.get("s1/username") == "editor"

    # Without the old key the value is unreadable, and says why.
    with pytest.raises(SecretResolutionError, match="no longer configured"):
        SecretVault(repository, Keyring([new])).get("s1/username")


def test_a_bad_key_is_refused_at_startup():
    with pytest.raises(VaultConfigurationError):
        Keyring(["c2hvcnQ="])


def test_keyring_is_absent_without_configuration(monkeypatch):
    monkeypatch.delenv("SEO_AGENT_SECRET_KEYS", raising=False)
    assert Keyring.from_environment() is None


def test_composite_resolver_keeps_environment_references_working(monkeypatch):
    monkeypatch.setenv("WP_USER", "from-env")
    _, vault = make_vault()
    vault.put(tenant_id="t1", site_id="s1", name="application_password", value="from-db")
    resolver = CompositeSecretResolver(EnvironmentSecretResolver(), vault)

    assert resolver.resolve(SecretRef(key="WP_USER")) == "from-env"
    assert (
        resolver.resolve(SecretRef(provider=SecretProvider.DATABASE, key="s1/application_password"))
        == "from-db"
    )
    with pytest.raises(SecretResolutionError):
        CompositeSecretResolver(EnvironmentSecretResolver(), None).resolve(
            SecretRef(provider=SecretProvider.DATABASE, key="s1/application_password")
        )


# ---- through the API -------------------------------------------------------


API_KEY = "vault-test-key"


def make_client(with_vault=True):
    repository = InMemoryRepository()
    vault = SecretVault(repository, Keyring([generate_key()])) if with_vault else None
    site_store = SiteStore(repository)
    site_store.create(
        Site(site_id="s1", principal_id="principal-1", name="Shop", base_url="https://shop.example/")
    )
    resolver = CompositeSecretResolver(EnvironmentSecretResolver(), vault)
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
            site_store=site_store,
            adapter_factory=SiteAdapterFactory(resolver),
            vault=vault,
        )
    )
    return TestClient(app), repository, site_store, resolver


HEADERS = {"Authorization": f"Bearer {API_KEY}"}
WORDPRESS = {
    "adapter_type": "wordpress",
    "config": {},
    "secrets": {"username": "editor", "application_password": "abcd efgh ijkl mnop"},
}


def test_a_customer_connects_their_site_without_server_access():
    client, repository, site_store, resolver = make_client()

    response = client.put("/v1/sites/s1/connections/site-adapter", headers=HEADERS, json=WORDPRESS)

    assert response.status_code == 200, response.text
    assert "abcd" not in response.text
    refs = site_store.get("s1").site_adapter.secret_refs
    assert refs["application_password"].provider == SecretProvider.DATABASE
    assert resolver.resolve(refs["application_password"]) == "abcd efgh ijkl mnop"
    for record in repository.list():
        assert "abcd efgh" not in str(record.payload)


def test_values_are_refused_when_the_server_has_no_key():
    client, *_ = make_client(with_vault=False)
    response = client.put("/v1/sites/s1/connections/site-adapter", headers=HEADERS, json=WORDPRESS)
    assert response.status_code == 503


def test_the_same_credential_cannot_be_both_a_value_and_a_reference():
    client, *_ = make_client()
    body = {**WORDPRESS, "secret_refs": {"username": "SEO_AGENT_SITE_SECRET_WP_USER"}}
    response = client.put("/v1/sites/s1/connections/site-adapter", headers=HEADERS, json=body)
    assert response.status_code == 422


def test_a_reference_to_an_arbitrary_server_variable_is_refused():
    client, *_ = make_client()
    body = {
        "adapter_type": "wordpress",
        "config": {},
        "secret_refs": {"username": "SEO_AGENT_SITE_SECRET_U", "application_password": "SEO_AGENT_SECRET_KEYS"},
    }
    response = client.put("/v1/sites/s1/connections/site-adapter", headers=HEADERS, json=body)
    assert response.status_code == 422


@pytest.mark.parametrize("base_url", ["https://attacker.example/", "https://shop.example.attacker.io/"])
def test_credentials_cannot_be_pointed_at_another_host(base_url):
    client, *_ = make_client()
    body = {**WORDPRESS, "config": {"base_url": base_url}}
    response = client.put("/v1/sites/s1/connections/site-adapter", headers=HEADERS, json=body)
    assert response.status_code == 422


def test_an_api_host_on_the_sites_own_domain_is_allowed():
    client, *_ = make_client()
    body = {**WORDPRESS, "config": {"base_url": "https://api.shop.example/"}}
    response = client.put("/v1/sites/s1/connections/site-adapter", headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
