"""Disconnecting Google: revoked where it was issued, then forgotten, sites detached."""
import pytest

from app.onboarding.google_oauth import GoogleOAuthService, OAuthError
from app.onboarding.secrets import SecretResolutionError
from app.onboarding.vault import Keyring, SecretVault, generate_key
from app.persistence.memory import InMemoryRepository
from tests.unit.test_google_oauth import (
    AUTH,
    CONFIG,
    NOW,
    Google,
    api_client,
    connect_google,
    state_of,
)


def connected_service(revoke_status=200, revoke_error=None):
    revoked = []

    def revoke(token):
        revoked.append(token)
        if revoke_error:
            raise revoke_error
        return revoke_status

    repository = InMemoryRepository()
    vault = SecretVault(repository, Keyring([generate_key()]))
    service = GoogleOAuthService(
        config=CONFIG, repository=repository, vault=vault, exchange=Google(), revoke=revoke,
        clock=lambda: NOW,
    )
    state = state_of(service.start("tenant-1"))
    _, _, nonce = service.begin(state)
    connection = service.complete(state_id=state, code="c", error=None, browser_nonce=nonce)
    return service, vault, connection, revoked


def test_disconnecting_revokes_at_google_then_forgets():
    service, vault, connection, revoked = connected_service()

    assert service.disconnect("tenant-1") is not None

    assert revoked == ["refresh-1"]
    assert service.connection("tenant-1") is None
    with pytest.raises(SecretResolutionError, match="disconnected"):
        vault.get(connection.credential_ref.key)


def test_a_failed_revocation_changes_nothing():
    service, vault, connection, _ = connected_service(revoke_error=ConnectionError("down"))

    with pytest.raises(OAuthError, match="nothing was changed"):
        service.disconnect("tenant-1")

    assert service.connection("tenant-1") is not None
    assert vault.get(connection.credential_ref.key) == "refresh-1"


def test_an_unexpected_answer_from_google_changes_nothing():
    service, _, _, _ = connected_service(revoke_status=503)
    with pytest.raises(OAuthError):
        service.disconnect("tenant-1")
    assert service.connection("tenant-1") is not None


def test_a_token_already_revoked_by_the_customer_still_disconnects():
    service, _, _, _ = connected_service(revoke_status=400)
    service.disconnect("tenant-1")
    assert service.connection("tenant-1") is None


def test_disconnecting_twice_is_harmless():
    service, _, _, revoked = connected_service()
    service.disconnect("tenant-1")
    assert service.disconnect("tenant-1") is None
    assert revoked == ["refresh-1"]


def test_the_api_disconnects_and_detaches_the_sites_using_it():
    client, sites, _ = api_client()
    connect_google(client)
    client.put(
        "/v1/sites/s1/connections/gsc",
        headers=AUTH,
        json={"property_url": "sc-domain:tennisino.com", "use_google_account": True},
    )
    client.app.state.dependencies.google_oauth._revoke = lambda token: 200

    response = client.delete("/v1/google/connection", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json()["sites_detached"] == ["s1"]
    assert sites.get("s1").gsc is None
    assert client.get("/v1/google/connection", headers=AUTH).json()["connected"] is False


def test_the_api_reports_a_failed_revocation():
    client, _, _ = api_client()
    connect_google(client)

    def unreachable(token):
        raise ConnectionError("down")

    client.app.state.dependencies.google_oauth._revoke = unreachable
    response = client.delete("/v1/google/connection", headers=AUTH)

    assert response.status_code == 502
    assert client.get("/v1/google/connection", headers=AUTH).json()["connected"] is True
