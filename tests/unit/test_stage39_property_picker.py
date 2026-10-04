"""Onboarding asks Google which properties exist instead of asking the customer.

The step this removes: a customer types their property URL, and that string has
to match what Google stored byte for byte. A missing trailing slash, ``http``
where Google has ``https``, or a domain property typed as a URL, and every
later request returns 403 — which reads as "you don't have permission", so the
customer re-grants access they already had and it still fails.

The fix has two halves. Google is asked what the credential can see, and a
property Google does not report is refused at configuration time rather than
stored and discovered on the first run.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.gsc.client import GSCClientError
from app.gsc.properties import GSCProperty, list_properties, parse_properties
from app.gsc.transport import GoogleResponse
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.sites import GSCConnectionConfig, SecretRef, validate_property_url
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runs.store import RunStore


# --- a property is stored exactly as Google spells it ------------------------


def test_a_domain_property_can_be_stored_at_all():
    """sc-domain: is not a URL. HttpUrl rejected it, so domain properties —
    which is what Google now recommends — could not be connected."""
    config = GSCConnectionConfig(
        property_url="sc-domain:pama.shop", credential_ref=SecretRef(key="K")
    )

    assert config.property_url == "sc-domain:pama.shop"


def test_a_url_property_is_not_normalised_on_the_way_in():
    """HttpUrl turned 'https://pama.shop' into 'https://pama.shop/'. Whether
    that matches depends on our normaliser agreeing with Google's."""
    config = GSCConnectionConfig(
        property_url="https://pama.shop", credential_ref=SecretRef(key="K")
    )

    assert config.property_url == "https://pama.shop", "stored byte for byte"


def test_a_trailing_slash_is_preserved_because_google_distinguishes_them():
    config = GSCConnectionConfig(
        property_url="https://pama.shop/", credential_ref=SecretRef(key="K")
    )

    assert config.property_url == "https://pama.shop/"


@pytest.mark.parametrize(
    "value",
    ["pama.shop", "sc-domain:https://pama.shop", "sc-domain:pama.shop/men", "sc-domain:", ""],
)
def test_a_property_that_is_neither_shape_is_refused(value):
    with pytest.raises(ValidationError):
        GSCConnectionConfig(property_url=value, credential_ref=SecretRef(key="K"))


def test_the_refusal_says_what_the_two_shapes_are():
    with pytest.raises(ValueError, match="sc-domain:example.com"):
        validate_property_url("pama.shop")


# --- reading the sites.list body --------------------------------------------


def test_the_properties_come_back_spelled_the_way_google_spells_them():
    properties = parse_properties(
        {
            "siteEntry": [
                {"siteUrl": "sc-domain:pama.shop", "permissionLevel": "siteOwner"},
                {"siteUrl": "https://pama.shop/", "permissionLevel": "siteFullUser"},
            ]
        }
    )

    assert [item.site_url for item in properties] == ["https://pama.shop/", "sc-domain:pama.shop"]


def test_a_domain_property_is_labelled_without_its_prefix_but_sent_with_it():
    item = GSCProperty(site_url="sc-domain:pama.shop", permission_level="siteOwner")

    assert item.display_name == "pama.shop", "what a human recognises"
    assert item.site_url == "sc-domain:pama.shop", "what goes back to Google"
    assert item.is_domain_property is True


def test_an_unverified_property_is_listed_but_marked_unreadable():
    """It appears in the response and then refuses search analytics. Offering
    it as a choice moves the 403 to the first run, which is worse."""
    properties = parse_properties(
        {
            "siteEntry": [
                {"siteUrl": "https://unverified.example/", "permissionLevel": "siteUnverifiedUser"},
                {"siteUrl": "https://ok.example/", "permissionLevel": "siteRestrictedUser"},
            ]
        }
    )

    assert [item.readable for item in properties] == [True, False]
    assert properties[0].site_url == "https://ok.example/", "usable choices first"


def test_a_restricted_user_can_still_read():
    assert GSCProperty(site_url="https://a/", permission_level="siteRestrictedUser").readable


def test_an_empty_account_is_an_empty_list_not_an_error():
    assert parse_properties({}) == []
    assert parse_properties({"siteEntry": None}) == []


def test_a_malformed_entry_is_skipped_rather_than_crashing_onboarding():
    properties = parse_properties(
        {"siteEntry": [{"permissionLevel": "siteOwner"}, "nonsense", {"siteUrl": "https://a/"}]}
    )

    assert [item.site_url for item in properties] == ["https://a/"]
    assert properties[0].permission_level == "siteUnverifiedUser", "unknown means unusable"


def test_matching_is_exact_and_not_forgiving():
    """Being forgiving here is the bug: our idea of an equivalent URL is not
    Google's, and a near-miss stored now is a 403 later with nothing to
    point at."""
    item = GSCProperty(site_url="https://pama.shop/", permission_level="siteOwner")

    assert item.matches("https://pama.shop/")
    assert not item.matches("https://pama.shop")
    assert not item.matches("http://pama.shop/")
    assert not item.matches("sc-domain:pama.shop")


# --- the call itself ---------------------------------------------------------


class Transport:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def __call__(self, method, path, *, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "path": path, "headers": headers or {}})
        return self._response


def ok(payload):
    return GoogleResponse(200, json.dumps(payload).encode(), {})


def test_the_list_is_fetched_with_the_credentials_token():
    transport = Transport(ok({"siteEntry": [{"siteUrl": "https://a/", "permissionLevel": "siteOwner"}]}))
    provider = type("P", (), {"token": staticmethod(lambda: "minted")})()

    list_properties(request_fn=transport, token_provider=provider)

    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["path"] == "/webmasters/v3/sites"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer minted"


def test_a_permission_failure_says_so_instead_of_reporting_http_403():
    body = json.dumps(
        {"error": {"code": 403, "message": "no grant", "errors": [{"reason": "forbidden"}]}}
    ).encode()
    transport = Transport(GoogleResponse(403, body, {}))

    with pytest.raises(GSCClientError) as raised:
        list_properties(request_fn=transport)

    assert "permission_denied" in str(raised.value)
    assert raised.value.retryable is False


def test_an_exhausted_quota_is_reported_as_retryable():
    body = json.dumps(
        {"error": {"code": 403, "message": "out", "errors": [{"reason": "quotaExceeded"}]}}
    ).encode()

    with pytest.raises(GSCClientError) as raised:
        list_properties(request_fn=Transport(GoogleResponse(403, body, {})))

    assert raised.value.retryable is True


# --- onboarding through the API ---------------------------------------------


def api(lister=None, *, verified=True):
    from datetime import datetime, timezone

    repository = InMemoryRepository()
    sites = SiteStore(repository)
    dependencies = APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator("test-key"),
        site_store=sites,
        run_store=RunStore(repository),
        gsc_property_lister=lister,
    )
    client = TestClient(create_app(dependencies))
    client.headers["Authorization"] = "Bearer test-key"
    site_id = client.post(
        "/v1/sites", json={"name": "PAMA", "base_url": "https://pama.shop"}
    ).json()["site_id"]
    if verified:
        sites.update(
            sites.get(site_id).model_copy(
                update={"ownership_method": "meta_tag", "ownership_verified_at": datetime.now(timezone.utc)}
            )
        )
    return client, site_id


def listing(*properties):
    calls = []

    def lister(*, auth_mode, credential_ref):
        calls.append({"auth_mode": auth_mode, "credential_ref": credential_ref})
        return list(properties)

    lister.calls = calls
    return lister


def test_the_customer_is_shown_what_to_pick_rather_than_asked_to_type():
    lister = listing(
        GSCProperty(site_url="sc-domain:pama.shop", permission_level="siteOwner"),
        GSCProperty(site_url="https://pama.shop/", permission_level="siteFullUser"),
        # Another customer's, visible to the shared account: never shown here.
        GSCProperty(site_url="sc-domain:competitor.ir", permission_level="siteFullUser"),
    )
    client, site_id = api(lister)

    response = client.post(
        f"/v1/sites/{site_id}/connections/gsc/available",
        json={"credential_ref": "GSC_KEY", "auth_mode": "service_account"},
    )

    assert response.status_code == 200
    listed = response.json()["properties"]
    assert [item["site_url"] for item in listed] == ["sc-domain:pama.shop", "https://pama.shop/"]
    assert listed[0]["display_name"] == "pama.shop", "shown without the sc-domain: prefix"
    assert lister.calls == [{"auth_mode": "service_account", "credential_ref": "GSC_KEY"}]


def test_a_property_google_does_not_report_is_refused_before_it_is_stored():
    """This is the whole point. Stored, it fails on the first run with a 403
    that names nothing."""
    client, site_id = api(listing(GSCProperty("https://pama.shop/", "siteOwner")))

    response = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={"property_url": "https://pama.shop", "credential_ref": "GSC_KEY"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["available"] == ["https://pama.shop/"]
    assert "trailing slash" in detail["message"]
    assert client.get(f"/v1/sites/{site_id}").json()["gsc_configured"] is False, "nothing stored"


def test_the_exact_property_is_accepted_and_stored_unchanged():
    client, site_id = api(listing(GSCProperty("sc-domain:pama.shop", "siteOwner")))

    response = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={"property_url": "sc-domain:pama.shop", "credential_ref": "GSC_KEY"},
    )

    assert response.status_code == 200
    assert response.json()["gsc_property_url"] == "sc-domain:pama.shop", "unchanged"


def test_a_listed_but_unreadable_property_is_refused_too():
    client, site_id = api(listing(GSCProperty("https://pama.shop/", "siteUnverifiedUser")))

    response = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={"property_url": "https://pama.shop/", "credential_ref": "GSC_KEY"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["permission_level"] == "siteUnverifiedUser"


def test_a_google_failure_during_configuration_is_not_reported_as_the_users_mistake():
    def exploding(*, auth_mode, credential_ref):
        raise GSCClientError("GSC request failed: HTTP 500 | transient")

    client, site_id = api(exploding)

    response = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={"property_url": "https://pama.shop/", "credential_ref": "GSC_KEY"},
    )

    assert response.status_code == 502, "502, not 422: nothing the customer typed is wrong"


def test_without_a_lister_the_property_is_taken_on_trust_as_before():
    """Stub mode has nothing to ask. Onboarding must not stop working."""
    client, site_id = api(None)

    response = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={"property_url": "https://pama.shop/", "credential_ref": "GSC_KEY"},
    )

    assert response.status_code == 200


def test_listing_is_unavailable_rather_than_silently_empty_without_a_lister():
    """An empty list would read as 'you have no properties', which is a lie."""
    client, site_id = api(None)

    response = client.post(
        f"/v1/sites/{site_id}/connections/gsc/available",
        json={"credential_ref": "GSC_KEY"},
    )

    assert response.status_code == 503


def test_another_tenants_site_cannot_be_probed_for_properties():
    lister = listing(GSCProperty("https://pama.shop/", "siteOwner"))
    client, site_id = api(lister)
    client.headers["Authorization"] = "Bearer someone-else"

    response = client.post(
        f"/v1/sites/{site_id}/connections/gsc/available",
        json={"credential_ref": "GSC_KEY"},
    )

    assert response.status_code in {401, 403, 404}
    assert lister.calls == [], "Google must not be called on behalf of a stranger"


def test_an_unverified_site_sees_no_properties_of_the_shared_account():
    lister = listing(GSCProperty("https://pama.shop/", "siteOwner"))
    client, site_id = api(lister, verified=False)

    response = client.post(
        f"/v1/sites/{site_id}/connections/gsc/available",
        json={"credential_ref": "GSC_KEY", "auth_mode": "service_account"},
    )

    assert response.status_code == 409
    assert lister.calls == [], "Google is not even asked"


def test_another_domains_property_cannot_be_connected_even_if_listed():
    client, site_id = api(listing(GSCProperty("sc-domain:competitor.ir", "siteOwner")))

    response = client.put(
        f"/v1/sites/{site_id}/connections/gsc",
        json={
            "property_url": "sc-domain:competitor.ir",
            "credential_ref": "GSC_KEY",
            "auth_mode": "service_account",
        },
    )

    assert response.status_code == 422
    assert client.get(f"/v1/sites/{site_id}").json()["gsc_configured"] is False
