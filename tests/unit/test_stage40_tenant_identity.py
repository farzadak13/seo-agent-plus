"""One key, one tenant.

Until this, every request authenticated as the same principal: one key in the
environment, hard-coded to the id "api-key". Every endpoint called
``_ensure_owner`` and every call compared that constant against itself, so they
all passed. The checks were written correctly and enforced nothing — one
customer's key could read another customer's sites, and nothing in the code
looked wrong.

The first test below is the one that matters. It would have passed trivially
before, for the wrong reason.
"""
from __future__ import annotations

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator, TenantAPIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.tenants import APIKeyRecord, Tenant, TenantStatus
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runs.store import RunStore
from app.tenancy import keys as key_format
from app.tenancy.store import APIKeyStore, TenantStore


# --- the key format ----------------------------------------------------------


def test_a_key_round_trips_through_its_own_format():
    issued = key_format.issue()

    key_id, secret = key_format.parse(issued.token)

    assert key_id == issued.key_id
    assert secret == issued.secret
    assert key_format.matches(key_id=key_id, secret=secret, expected_digest=issued.digest)


def test_a_secret_containing_the_separator_still_parses():
    """token_urlsafe emits '_' and '-'. Splitting on every separator would
    truncate roughly half of all keys, and only sometimes."""
    token = "seo_abc123_part_one_two-three_"

    key_id, secret = key_format.parse(token)

    assert key_id == "abc123"
    assert secret == "part_one_two-three_"


@pytest.mark.parametrize(
    "token", ["", "abc", "seo_only", "wrong_abc_def", "seo__nosecret", "seo_abc_"]
)
def test_anything_not_shaped_like_a_key_is_rejected(token):
    assert key_format.looks_like_ours(token) is False
    with pytest.raises(key_format.InvalidKeyFormat):
        key_format.parse(token)


def test_a_digest_is_bound_to_its_own_key_id():
    """Otherwise a digest copied from one row authenticates against another."""
    issued = key_format.issue()

    assert not key_format.matches(
        key_id="someone-elses-id", secret=issued.secret, expected_digest=issued.digest
    )


def test_two_issued_keys_never_collide():
    tokens = {key_format.issue().token for _ in range(200)}

    assert len(tokens) == 200


def test_the_key_id_is_recoverable_without_the_secret():
    """It goes in logs and support tickets on purpose: it says which key is in
    use without saying anything that lets someone use it."""
    issued = key_format.issue()

    assert issued.key_id in issued.token
    assert issued.secret not in issued.key_id


# --- authentication ----------------------------------------------------------


def make(*, tenant_status=TenantStatus.ACTIVE, admin_key="admin-secret"):
    repository = InMemoryRepository()
    tenants = TenantStore(repository)
    api_keys = APIKeyStore(repository)
    tenants.create(Tenant(tenant_id="t-one", name="One", status=tenant_status))
    tenants.create(Tenant(tenant_id="t-two", name="Two"))
    issued = {}
    for tenant_id in ("t-one", "t-two"):
        key = key_format.issue()
        api_keys.create(
            APIKeyRecord(key_id=key.key_id, tenant_id=tenant_id, digest=key.digest)
        )
        issued[tenant_id] = key
    authenticator = TenantAPIKeyAuthenticator(
        key_store=api_keys, tenant_store=tenants, admin_key=admin_key
    )
    return repository, tenants, api_keys, authenticator, issued


def bearer(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_a_key_authenticates_as_its_own_tenant():
    _, _, _, auth, issued = make()

    assert auth.authenticate(bearer(issued["t-one"].token)) == "t-one"
    assert auth.authenticate(bearer(issued["t-two"].token)) == "t-two"


def test_the_configured_key_is_a_tenant_called_admin_not_a_master_key():
    _, _, _, auth, _ = make()

    assert auth.authenticate(bearer("admin-secret")) == "admin"


def test_a_revoked_key_stops_working_on_the_very_next_request():
    """Nothing is cached, and this is why. A cache would buy one round trip
    and sell the only reason revocation exists."""
    _, _, api_keys, auth, issued = make()
    token = issued["t-one"].token
    assert auth.authenticate(bearer(token)) == "t-one"

    api_keys.revoke(issued["t-one"].key_id)

    with pytest.raises(HTTPException) as raised:
        auth.authenticate(bearer(token))
    assert raised.value.status_code == 401


def test_a_suspended_tenant_is_told_so_rather_than_told_the_key_is_wrong():
    """The key is real and the customer can act on this; sending them to
    regenerate a working key wastes their afternoon."""
    _, _, _, auth, issued = make(tenant_status=TenantStatus.SUSPENDED)

    with pytest.raises(HTTPException) as raised:
        auth.authenticate(bearer(issued["t-one"].token))

    assert raised.value.status_code == 403
    assert "suspended" in str(raised.value.detail).lower()


def test_a_real_key_id_with_a_wrong_secret_is_refused():
    _, _, _, auth, issued = make()
    forged = f"seo_{issued['t-one'].key_id}_not-the-secret"

    with pytest.raises(HTTPException) as raised:
        auth.authenticate(bearer(forged))
    assert raised.value.status_code == 401


def test_every_rejection_reads_the_same():
    """Which check failed is information about somebody else's key: whether a
    key id exists, whether it was revoked, whether the secret was close."""
    _, _, api_keys, auth, issued = make()
    api_keys.revoke(issued["t-two"].key_id)

    messages = set()
    for token in (
        "seo_0000000000000000_nothing",
        f"seo_{issued['t-one'].key_id}_wrong",
        issued["t-two"].token,
    ):
        with pytest.raises(HTTPException) as raised:
            auth.authenticate(bearer(token))
        messages.add((raised.value.status_code, str(raised.value.detail)))

    assert len(messages) == 1, messages


def test_no_credentials_and_the_wrong_scheme_are_both_refused():
    _, _, _, auth, _ = make()

    with pytest.raises(HTTPException):
        auth.authenticate(None)
    with pytest.raises(HTTPException):
        auth.authenticate(HTTPAuthorizationCredentials(scheme="Basic", credentials="x"))


def test_without_an_admin_key_nothing_outside_the_format_is_accepted():
    _, _, _, auth, _ = make(admin_key=None)

    with pytest.raises(HTTPException):
        auth.authenticate(bearer("admin-secret"))


def test_a_key_for_a_tenant_that_no_longer_exists_is_refused():
    repository = InMemoryRepository()
    api_keys = APIKeyStore(repository)
    tenants = TenantStore(repository)
    orphan = key_format.issue()
    api_keys.create(
        APIKeyRecord(key_id=orphan.key_id, tenant_id="t-gone", digest=orphan.digest)
    )
    auth = TenantAPIKeyAuthenticator(key_store=api_keys, tenant_store=tenants)

    with pytest.raises(HTTPException) as raised:
        auth.authenticate(bearer(orphan.token))
    assert raised.value.status_code == 401


# --- the property the whole change exists for --------------------------------


def api(authenticator, repository):
    dependencies = APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=authenticator,
        site_store=SiteStore(repository),
        run_store=RunStore(repository),
    )
    return TestClient(create_app(dependencies))


def test_one_tenant_cannot_see_another_tenants_site():
    """Before this change both keys resolved to the same principal, so this
    read succeeded and the ownership check passed while doing it."""
    repository, _, _, auth, issued = make()
    client = api(auth, repository)

    created = client.post(
        "/v1/sites",
        json={"name": "One's site", "base_url": "https://one.example"},
        headers={"authorization": f"bearer {issued['t-one'].token}"},
    )
    assert created.status_code == 201
    site_id = created.json()["site_id"]
    assert created.json()["principal_id"] == "t-one"

    mine = client.get(
        f"/v1/sites/{site_id}",
        headers={"authorization": f"bearer {issued['t-one'].token}"},
    )
    assert mine.status_code == 200

    theirs = client.get(
        f"/v1/sites/{site_id}",
        headers={"authorization": f"bearer {issued['t-two'].token}"},
    )
    assert theirs.status_code in {403, 404}, "another tenant must not read this site"


def test_another_tenant_cannot_configure_a_site_it_does_not_own():
    repository, _, _, auth, issued = make()
    client = api(auth, repository)
    site_id = client.post(
        "/v1/sites",
        json={"name": "One's site", "base_url": "https://one.example"},
        headers={"authorization": f"bearer {issued['t-one'].token}"},
    ).json()["site_id"]

    response = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={"property_url": "https://one.example/", "credential_ref": "SOMETHING"},
        headers={"authorization": f"bearer {issued['t-two'].token}"},
    )

    assert response.status_code in {403, 404}


def test_the_admin_key_is_also_just_one_tenant():
    repository, _, _, auth, issued = make()
    client = api(auth, repository)
    site_id = client.post(
        "/v1/sites",
        json={"name": "One's site", "base_url": "https://one.example"},
        headers={"authorization": f"bearer {issued['t-one'].token}"},
    ).json()["site_id"]

    response = client.get(
        f"/v1/sites/{site_id}", headers={"authorization": "bearer admin-secret"}
    )

    assert response.status_code in {403, 404}, "the configured key is not a master key"


# --- the stored record -------------------------------------------------------


def test_the_key_itself_is_never_stored():
    repository = InMemoryRepository()
    api_keys = APIKeyStore(repository)
    issued = key_format.issue()
    api_keys.create(
        APIKeyRecord(key_id=issued.key_id, tenant_id="t-one", digest=issued.digest)
    )

    everything = repository.list(aggregate_type="api_key")
    serialised = str([record.payload for record in everything])

    assert issued.secret not in serialised
    assert issued.token not in serialised


def test_revoking_keeps_the_earlier_version_rather_than_overwriting_it():
    """When a key was revoked answers 'was this request legitimate at the
    time'. Overwriting the row turns that into a guess."""
    repository = InMemoryRepository()
    api_keys = APIKeyStore(repository)
    issued = key_format.issue()
    api_keys.create(
        APIKeyRecord(key_id=issued.key_id, tenant_id="t-one", digest=issued.digest)
    )

    revoked = api_keys.revoke(issued.key_id)

    assert revoked.revoked_at is not None
    assert api_keys.find(issued.key_id).active is False
    # The store is append-only: the revocation is version 2 of the same
    # aggregate, so version 1 — the key as it stood while it worked — is
    # still on disk rather than replaced in place.
    current = repository.get(aggregate_type="api_key", aggregate_id=issued.key_id)
    assert current.version == 2


def test_revoking_twice_is_not_an_error_and_keeps_the_first_time():
    repository = InMemoryRepository()
    api_keys = APIKeyStore(repository)
    issued = key_format.issue()
    api_keys.create(
        APIKeyRecord(key_id=issued.key_id, tenant_id="t-one", digest=issued.digest)
    )

    first = api_keys.revoke(issued.key_id)
    second = api_keys.revoke(issued.key_id)

    assert first.revoked_at == second.revoked_at


def test_keys_are_listed_per_tenant_and_not_across_tenants():
    repository = InMemoryRepository()
    api_keys = APIKeyStore(repository)
    for tenant_id in ("t-one", "t-one", "t-two"):
        issued = key_format.issue()
        api_keys.create(
            APIKeyRecord(key_id=issued.key_id, tenant_id=tenant_id, digest=issued.digest)
        )

    assert len(api_keys.list_for_tenant("t-one")) == 2
    assert len(api_keys.list_for_tenant("t-two")) == 1


def test_a_stretched_hash_is_not_used_and_that_is_the_point():
    """PBKDF2 protects a password someone chose. These keys are 256 bits from
    `secrets`, so stretching buys nothing and costs a tenth of a second of CPU
    on every request. The old authenticator is kept for the admin key only."""
    issued = key_format.issue()

    assert len(issued.digest) == 64, "sha-256 hex"
    # And the stretched one still works where it belongs.
    legacy = APIKeyAuthenticator("a-password-like-key")
    assert legacy.authenticate(bearer("a-password-like-key")) == "api-key"
