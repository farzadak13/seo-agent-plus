"""A tenant proves a site is theirs before the shared account's data reaches them."""
import socket

import pytest
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.sites import Site
from app.net.guard import UnsafeAddressError, ensure_public_url
from app.onboarding.ownership import (
    METHOD_CONNECTOR,
    METHOD_META_TAG,
    OwnershipVerifier,
    find_tokens,
    meta_tag,
    property_matches_site,
)
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository


@pytest.mark.parametrize(
    "prop, site, expected",
    [
        ("sc-domain:tennisino.com", "https://tennisino.com/", True),
        ("sc-domain:tennisino.com", "https://www.tennisino.com/", True),
        ("https://tennisino.com/", "https://tennisino.com/", True),
        ("https://www.tennisino.com/", "https://tennisino.com/", True),
        ("https://tennisino.com/blog/", "https://tennisino.com/", True),
        ("sc-domain:competitor.ir", "https://tennisino.com/", False),
        ("https://tennisino.com.evil.io/", "https://tennisino.com/", False),
        # Proving a subdomain says nothing about the rest of the domain.
        ("sc-domain:example.com", "https://shop.example.com/", False),
    ],
)
def test_which_property_belongs_to_which_site(prop, site, expected):
    assert property_matches_site(prop, site) is expected


def test_the_meta_tag_is_found_in_a_real_head():
    html = "<html><head><title>x</title>" + meta_tag("abc123") + "</head><body></body></html>"
    assert find_tokens(html) == ["abc123"]


def make_site(**changes):
    return Site(
        site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/",
        verification_token="token-1", **changes,
    )


def test_meta_tag_proves_ownership():
    verifier = OwnershipVerifier(fetch_html=lambda url: "<head>" + meta_tag("token-1") + "</head>")
    assert verifier.verify(make_site()) == (METHOD_META_TAG, [])


def test_a_different_token_does_not():
    verifier = OwnershipVerifier(fetch_html=lambda url: "<head>" + meta_tag("someone-else") + "</head>")
    method, reasons = verifier.verify(make_site())
    assert method is None and reasons


class Factory:
    def __init__(self, status):
        self._status = status

    def build(self, site):
        status = self._status

        class Adapter:
            def status(self):
                if isinstance(status, Exception):
                    raise status
                return status

        return Adapter()


def connected_site():
    from app.models.sites import SiteAdapterConnection

    return make_site(site_adapter=SiteAdapterConnection(adapter_type="hoshyarseo"))


def test_an_editor_connection_on_the_same_site_proves_ownership():
    verifier = OwnershipVerifier(
        adapter_factory=Factory({"can_verify_ownership": True, "home_url": "https://www.tennisino.com/"}),
        fetch_html=lambda url: "",
    )
    assert verifier.verify(connected_site())[0] == METHOD_CONNECTOR


def test_an_author_account_does_not():
    verifier = OwnershipVerifier(
        adapter_factory=Factory({"can_verify_ownership": False, "home_url": "https://tennisino.com/"}),
        fetch_html=lambda url: "",
    )
    method, reasons = verifier.verify(connected_site())
    assert method is None
    assert any("Editor" in reason for reason in reasons)


def test_a_connector_answering_for_another_address_does_not():
    verifier = OwnershipVerifier(
        adapter_factory=Factory({"can_verify_ownership": True, "home_url": "https://other.com/"}),
        fetch_html=lambda url: "",
    )
    assert verifier.verify(connected_site())[0] is None


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"])
def test_internal_addresses_are_never_fetched(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 443))]
    )
    with pytest.raises(UnsafeAddressError):
        ensure_public_url("https://looks-public.example/")


def test_a_public_address_is_allowed(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    ensure_public_url("https://example.com/")


def test_the_api_issues_a_token_then_verifies_with_it():
    repository = InMemoryRepository()
    sites = SiteStore(repository)
    sites.create(Site(site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/"))
    served = {"html": ""}
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
            site_store=sites,
            ownership_verifier=OwnershipVerifier(fetch_html=lambda url: served["html"]),
        )
    )
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer k"

    issued = client.get("/v1/sites/s1/ownership").json()
    assert issued["verified"] is False
    assert client.get("/v1/sites/s1/ownership").json()["meta_tag"] == issued["meta_tag"], "stable"

    failed = client.post("/v1/sites/s1/ownership/verify").json()
    assert failed["verified"] is False and failed["reasons"]

    served["html"] = "<head>" + issued["meta_tag"] + "</head>"
    passed = client.post("/v1/sites/s1/ownership/verify").json()
    assert passed["verified"] is True and passed["method"] == METHOD_META_TAG
    assert client.get("/v1/sites/s1").json()["ownership_verified"] is True


# ---- the ways round it -----------------------------------------------------


def test_a_site_on_a_path_cannot_prove_the_whole_domain():
    # A multisite sub-blog, or a page on shared hosting.
    site = Site(
        site_id="s1", principal_id="p1", name="T", base_url="https://victim.com/blog2/",
        verification_token="token-1",
    )
    verifier = OwnershipVerifier(fetch_html=lambda url: "<head>" + meta_tag("token-1") + "</head>")
    method, reasons = verifier.verify(site)
    assert method is None and "domain itself" in reasons[0]


def test_the_meta_tag_is_read_from_the_root_only():
    seen = []
    verifier = OwnershipVerifier(fetch_html=lambda url: seen.append(url) or "")
    verifier.verify(make_site())
    assert seen == ["https://tennisino.com/"]


def test_a_connector_on_a_subdomain_cannot_prove_the_domain():
    from app.models.sites import SiteAdapterConnection

    site = make_site(
        site_adapter=SiteAdapterConnection(
            adapter_type="hoshyarseo", config={"base_url": "https://user1.tennisino.com/"}
        )
    )
    verifier = OwnershipVerifier(
        adapter_factory=Factory({"can_verify_ownership": True, "home_url": "https://tennisino.com/"}),
        fetch_html=lambda url: "",
    )
    method, reasons = verifier.verify(site)
    assert method is None
    assert any("own root" in reason for reason in reasons)


def test_a_wordpress_living_on_a_path_cannot_prove_the_domain():
    verifier = OwnershipVerifier(
        adapter_factory=Factory({"can_verify_ownership": True, "home_url": "https://tennisino.com/blog2/"}),
        fetch_html=lambda url: "",
    )
    assert verifier.verify(connected_site())[0] is None


def test_a_redirect_to_another_host_is_not_followed(monkeypatch):
    from app.net.guard import _CheckedRedirects

    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    handler = _CheckedRedirects("https://tennisino.com/")
    with pytest.raises(UnsafeAddressError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://attacker.example/")


def test_the_refusal_does_not_list_other_customers_properties():
    from datetime import datetime, timezone

    from app.gsc.properties import GSCProperty

    repository = InMemoryRepository()
    sites = SiteStore(repository)
    sites.create(
        Site(
            site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/",
            ownership_method="meta_tag", ownership_verified_at=datetime.now(timezone.utc),
        )
    )
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
            site_store=sites,
            gsc_property_lister=lambda **_: [
                GSCProperty("https://tennisino.com/", "siteOwner"),
                GSCProperty("sc-domain:competitor.ir", "siteOwner"),
            ],
        )
    )
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer k"

    # Its own domain, misspelled (no trailing slash): refused, listing only its own.
    response = client.put(
        "/v1/sites/s1/connections/gsc",
        json={"property_url": "https://tennisino.com", "credential_ref": "K", "auth_mode": "service_account"},
    )
    assert response.status_code == 422
    assert "competitor.ir" not in response.text
    assert response.json()["detail"]["available"] == ["https://tennisino.com/"]


# ---- b, c, d ---------------------------------------------------------------


def _runs_client(site):
    from app.runs.store import RunStore

    repository = InMemoryRepository()
    sites = SiteStore(repository)
    sites.create(site)
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
            site_store=sites,
            run_store=RunStore(repository),
        )
    )
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer k"
    return client


RUN = {
    "start_date": "2026-09-01", "end_date": "2026-09-07",
    "normalized_url": "https://tennisino.com/x", "normalized_query": "q", "candidate_id": "c",
}


def _gsc_site(property_url, *, verified):
    from datetime import datetime, timezone

    from app.models.sites import GSCConnectionConfig, SecretRef

    return Site(
        site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/",
        gsc=GSCConnectionConfig(
            property_url=property_url, credential_ref=SecretRef(key="GSC_SERVICE_ACCOUNT_JSON"),
            auth_mode="service_account",
        ),
        ownership_method="meta_tag" if verified else None,
        ownership_verified_at=datetime.now(timezone.utc) if verified else None,
    )


def test_every_analysis_rechecks_ownership():
    # Configured before the check existed: the analysis is refused until proved.
    client = _runs_client(_gsc_site("https://tennisino.com/", verified=False))
    assert client.post("/v1/sites/s1/runs", json=RUN).status_code == 409


def test_an_analysis_of_another_domains_property_is_refused():
    client = _runs_client(_gsc_site("sc-domain:competitor.ir", verified=True))
    assert client.post("/v1/sites/s1/runs", json=RUN).status_code == 409


def test_a_verified_site_on_its_own_property_runs():
    client = _runs_client(_gsc_site("https://tennisino.com/", verified=True))
    assert client.post("/v1/sites/s1/runs", json=RUN).status_code == 202


@pytest.mark.parametrize(
    "config",
    [
        {"base_url": "https://tennisino.com/~user/"},
        {"base_url": "https://tennisino.com/?x=1"},
        {"api_prefix": "/~user/fake"},
    ],
)
def test_a_connector_off_the_standard_root_cannot_prove_ownership(config):
    from app.models.sites import SiteAdapterConnection

    site = make_site(site_adapter=SiteAdapterConnection(adapter_type="hoshyarseo", config=config))
    verifier = OwnershipVerifier(
        adapter_factory=Factory({"can_verify_ownership": True, "home_url": "https://tennisino.com/"}),
        fetch_html=lambda url: "",
    )
    assert verifier.verify(site)[0] is None


def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    # The customer's DNS answers "public" to the check, then "loopback".
    from app.net.guard import _PinnedHTTPConnection

    answers = iter(["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 80))],
    )
    connected = []
    monkeypatch.setattr(socket, "create_connection", lambda address, *a: connected.append(address))

    ensure_public_url("http://rebind.example/")  # first answer: public
    connection = _PinnedHTTPConnection("rebind.example", 80)
    with pytest.raises(UnsafeAddressError):
        connection.connect()  # second answer: loopback, refused before connecting
    assert connected == []


def test_a_redirect_from_https_to_http_is_not_followed(monkeypatch):
    from app.net.guard import _CheckedRedirects

    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    )
    with pytest.raises(UnsafeAddressError):
        _CheckedRedirects("https://tennisino.com/").redirect_request(
            None, None, 301, "Moved", {}, "http://tennisino.com/"
        )


def test_a_site_written_from_a_stale_copy_is_refused():
    from app.onboarding.site_store import SiteChangedError

    sites = SiteStore(InMemoryRepository())
    original = sites.create(make_site())
    sites.update(original.model_copy(update={"name": "renamed by someone else"}))
    with pytest.raises(SiteChangedError):
        sites.update(original.model_copy(update={"ownership_method": "meta_tag"}))
    assert sites.get("s1").name == "renamed by someone else"
