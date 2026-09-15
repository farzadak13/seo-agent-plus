"""Credential kinds, and the isolation property that decides the design.

A service account is shared: every customer grants the same email, so a token
minted from it can read every property that ever granted it. Google is not
separating tenants in that mode — we are. These tests make that property
explicit and machine-readable rather than a paragraph someone may not read,
so the code that scopes a tenant can assert on it.
"""
from __future__ import annotations

import json

import pytest

from app.gsc.credentials import (
    SEARCH_CONSOLE_READONLY,
    CredentialError,
    CredentialKind,
    RefreshTokenProvider,
    ServiceAccountTokenProvider,
    StaticAccessTokenProvider,
    build_token_provider,
)


def service_account_info(**overrides) -> dict:
    info = {
        "type": "service_account",
        "project_id": "hoshyarseo",
        "client_email": "seoagent-gsc@hoshyarseo.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    info.update(overrides)
    return info


class FakeSession:
    """Stands in for a requests.Session. close() exists because google-auth's
    Request closes the session it was handed when it is garbage collected."""

    def close(self):
        return None


# --- isolation --------------------------------------------------------------


def test_a_service_account_is_not_isolated_per_tenant():
    """One key, many customers: scoping a tenant is our job, not Google's."""
    provider = ServiceAccountTokenProvider(
        key_json=json.dumps(service_account_info()), session=FakeSession()
    )

    assert provider.isolated_per_tenant is False


def test_an_oauth_refresh_token_is_isolated_per_tenant():
    provider = RefreshTokenProvider(
        client_id="id", client_secret="secret", refresh_token="refresh", session=FakeSession()
    )

    assert provider.isolated_per_tenant is True


def test_a_pasted_access_token_belongs_to_whoever_issued_it():
    assert StaticAccessTokenProvider(access_token="ya29.x").isolated_per_tenant is True


# --- kinds ------------------------------------------------------------------


def test_each_provider_reports_its_kind():
    session = FakeSession()

    assert StaticAccessTokenProvider(access_token="x").kind is CredentialKind.ACCESS_TOKEN
    assert (
        ServiceAccountTokenProvider(key_json=service_account_info(), session=session).kind
        is CredentialKind.SERVICE_ACCOUNT
    )
    assert (
        RefreshTokenProvider(
            client_id="a", client_secret="b", refresh_token="c", session=session
        ).kind
        is CredentialKind.OAUTH_REFRESH_TOKEN
    )


def test_the_default_scope_is_read_only():
    """Nothing in this product needs write access to Search Console."""
    assert SEARCH_CONSOLE_READONLY.endswith("webmasters.readonly")


# --- rejecting unusable input ------------------------------------------------


@pytest.mark.parametrize("missing", ["client_email", "private_key", "token_uri"])
def test_a_key_file_missing_a_required_field_is_refused_at_construction(missing):
    info = service_account_info()
    del info[missing]

    with pytest.raises(CredentialError, match=missing):
        ServiceAccountTokenProvider(key_json=json.dumps(info), session=FakeSession())


def test_an_oauth_client_without_all_three_parts_is_refused():
    with pytest.raises(CredentialError, match="required"):
        RefreshTokenProvider(
            client_id="id", client_secret="", refresh_token="refresh", session=FakeSession()
        )


def test_an_empty_access_token_fails_when_used_not_when_built():
    provider = StaticAccessTokenProvider(access_token="   ")

    with pytest.raises(CredentialError, match="empty"):
        provider.token()


def test_a_key_given_as_a_dict_works_the_same_as_a_string():
    from_dict = ServiceAccountTokenProvider(key_json=service_account_info(), session=FakeSession())
    from_text = ServiceAccountTokenProvider(
        key_json=json.dumps(service_account_info()), session=FakeSession()
    )

    assert from_dict.client_email == from_text.client_email


# --- the factory the runtime will call --------------------------------------


def test_the_factory_builds_each_kind():
    session = FakeSession()

    assert isinstance(
        build_token_provider(kind="access_token", secret="ya29.x", session=session),
        StaticAccessTokenProvider,
    )
    assert isinstance(
        build_token_provider(
            kind="service_account", secret=json.dumps(service_account_info()), session=session
        ),
        ServiceAccountTokenProvider,
    )
    assert isinstance(
        build_token_provider(
            kind="oauth_refresh_token",
            secret="refresh",
            session=session,
            client_id="id",
            client_secret="secret",
        ),
        RefreshTokenProvider,
    )


def test_the_factory_refuses_an_unknown_kind():
    with pytest.raises(ValueError):
        build_token_provider(kind="magic", secret="x", session=FakeSession())


def test_oauth_through_the_factory_needs_the_client_pair():
    with pytest.raises(CredentialError, match="client_id"):
        build_token_provider(kind="oauth_refresh_token", secret="refresh", session=FakeSession())


# --- token caching ----------------------------------------------------------


class FakeCredentials:
    def __init__(self, expiry=None, token="token-1"):
        self.token = token
        self.expiry = expiry
        self.refreshes = 0

    def refresh(self, request):
        self.refreshes += 1
        self.token = f"token-{self.refreshes + 1}"
        from datetime import datetime, timedelta, timezone

        self.expiry = datetime.now(timezone.utc) + timedelta(hours=1)


def test_a_token_well_inside_its_lifetime_is_reused():
    from datetime import datetime, timedelta, timezone

    provider = ServiceAccountTokenProvider(key_json=service_account_info(), session=FakeSession())
    credentials = FakeCredentials(expiry=datetime.now(timezone.utc) + timedelta(hours=1))
    provider._credentials = credentials

    assert provider.token() == "token-1"
    assert provider.token() == "token-1"
    assert credentials.refreshes == 0


def test_a_token_about_to_expire_is_refreshed_before_it_is_used():
    """Expiring mid-request produces a 401 that reads like a permission fault."""
    from datetime import datetime, timedelta, timezone

    provider = ServiceAccountTokenProvider(key_json=service_account_info(), session=FakeSession())
    credentials = FakeCredentials(expiry=datetime.now(timezone.utc) + timedelta(seconds=30))
    provider._credentials = credentials

    assert provider.token() == "token-2"
    assert credentials.refreshes == 1


def test_an_already_expired_token_is_refreshed():
    from datetime import datetime, timedelta, timezone

    provider = ServiceAccountTokenProvider(key_json=service_account_info(), session=FakeSession())
    credentials = FakeCredentials(expiry=datetime.now(timezone.utc) - timedelta(minutes=5))
    provider._credentials = credentials

    assert provider.token() == "token-2"
    assert credentials.refreshes == 1


def test_a_refusal_to_mint_says_where_to_look():
    """The message has to point at the project and region, not just echo the driver."""
    provider = ServiceAccountTokenProvider(key_json=service_account_info(), session=FakeSession())

    class Exploding(FakeCredentials):
        def refresh(self, request):
            raise RuntimeError("invalid_grant")

    provider._credentials = Exploding(expiry=None, token="")

    with pytest.raises(CredentialError, match="blocked project or region"):
        provider.token()


def test_the_real_google_auth_request_accepts_our_session():
    """Token minting must go through our session, so the proxy applies to it too."""
    from google.auth.transport.requests import Request

    session = FakeSession()
    request = Request(session=session)

    assert request.session is session
