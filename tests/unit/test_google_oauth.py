"""Sign in with Google: single use, bound to the browser, Search Console required."""
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.gsc.credentials import SEARCH_CONSOLE_READONLY
from app.onboarding.google_oauth import (
    GoogleOAuthConfig,
    GoogleOAuthService,
    OAuthError,
    tenant_credential_ref,
)
from app.onboarding.vault import Keyring, SecretVault, generate_key
from app.persistence.memory import InMemoryRepository


NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
CONFIG = GoogleOAuthConfig(
    client_id="client-1.apps.googleusercontent.com",
    client_secret="client-secret",
    public_base_url="https://hoshyarseo.ir",
)


def id_token(email):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


class Google:
    def __init__(self, *, status=200, scope=None, refresh_token="refresh-1"):
        self.forms = []
        self.status = status
        self.scope = scope if scope is not None else f"openid email {SEARCH_CONSOLE_READONLY}"
        self.refresh_token = refresh_token

    def __call__(self, form):
        self.forms.append(form)
        body = {"scope": self.scope, "id_token": id_token("owner@tennisino.com")}
        if self.refresh_token:
            body["refresh_token"] = self.refresh_token
        return self.status, body


def make(google=None, clock=lambda: NOW):
    repository = InMemoryRepository()
    vault = SecretVault(repository, Keyring([generate_key()]))
    google = google or Google()
    service = GoogleOAuthService(
        config=CONFIG, repository=repository, vault=vault, exchange=google, clock=clock
    )
    return service, vault, google


def state_of(link):
    return parse_qs(urlparse(link).query)["state"][0]


def test_the_whole_flow_stores_an_encrypted_token_for_the_tenant():
    service, vault, google = make()
    link = service.start("tenant-1")
    assert link.startswith("https://hoshyarseo.ir/v1/oauth/google/begin?state=")

    tenant, google_url, nonce = service.begin(state_of(link))
    assert tenant == "tenant-1"
    query = parse_qs(urlparse(google_url).query)
    assert query["redirect_uri"] == ["https://hoshyarseo.ir/v1/oauth/google/callback"]
    assert SEARCH_CONSOLE_READONLY in query["scope"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]

    connection = service.complete(state_id=state_of(link), code="code-1", error=None, browser_nonce=nonce)

    assert connection.email == "owner@tennisino.com"
    assert connection.credential_ref == tenant_credential_ref("tenant-1")
    assert vault.get(connection.credential_ref.key) == "refresh-1"
    assert google.forms[0]["code_verifier"], "PKCE verifier sent"
    assert service.connection("tenant-1").email == "owner@tennisino.com"


def test_a_link_sent_to_someone_else_does_not_complete_in_their_browser():
    service, _, _ = make()
    state = state_of(service.start("attacker"))
    service.begin(state)  # opened by the victim, who never got the attacker's cookie

    with pytest.raises(OAuthError, match="another browser"):
        service.complete(state_id=state, code="victims-code", error=None, browser_nonce=None)
    assert service.connection("attacker") is None


def test_the_begin_link_continues_once():
    service, _, _ = make()
    state = state_of(service.start("tenant-1"))
    service.begin(state)
    with pytest.raises(OAuthError, match="already been used"):
        service.begin(state)


def test_a_messenger_preview_does_not_spend_the_link():
    service, _, _ = make()
    state = state_of(service.start("tenant-1"))
    assert service.preview(state) == "tenant-1"
    assert service.preview(state) == "tenant-1"
    service.begin(state)  # the person pressing continue still can


def test_a_callback_cannot_be_replayed():
    service, _, _ = make()
    state = state_of(service.start("tenant-1"))
    _, _, nonce = service.begin(state)
    service.complete(state_id=state, code="c", error=None, browser_nonce=nonce)
    with pytest.raises(OAuthError, match="already used"):
        service.complete(state_id=state, code="c", error=None, browser_nonce=nonce)


def test_an_old_link_expires():
    times = iter([NOW, NOW + timedelta(minutes=11)])
    service, _, _ = make(clock=lambda: next(times))
    state = state_of(service.start("tenant-1"))
    with pytest.raises(OAuthError, match="expired"):
        service.begin(state)


def test_search_console_unticked_on_googles_screen_is_refused():
    service, _, _ = make(Google(scope="openid email"))
    state = state_of(service.start("tenant-1"))
    _, _, nonce = service.begin(state)
    with pytest.raises(OAuthError, match="Search Console"):
        service.complete(state_id=state, code="c", error=None, browser_nonce=nonce)
    assert service.connection("tenant-1") is None


def test_a_declined_consent_is_reported_and_spends_the_state():
    service, _, google = make()
    state = state_of(service.start("tenant-1"))
    _, _, nonce = service.begin(state)
    with pytest.raises(OAuthError, match="not granted"):
        service.complete(state_id=state, code=None, error="access_denied", browser_nonce=nonce)
    assert google.forms == []


def test_an_unknown_state_is_refused():
    service, _, _ = make()
    with pytest.raises(OAuthError):
        service.begin("made-up")


def test_the_secret_never_appears_in_repr():
    assert "client-secret" not in repr(CONFIG)


# ---- through the API -------------------------------------------------------


def api_client(google=None):
    from fastapi.testclient import TestClient

    from app.api.app import APIDependencies, create_app
    from app.api.auth import APIKeyAuthenticator
    from app.gsc.properties import GSCProperty
    from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
    from app.models.sites import Site
    from app.onboarding.site_store import SiteStore
    from app.runs.store import RunStore

    repository = InMemoryRepository()
    vault = SecretVault(repository, Keyring([generate_key()]))
    service = GoogleOAuthService(
        config=CONFIG, repository=repository, vault=vault, exchange=google or Google()
    )
    sites = SiteStore(repository)
    sites.create(Site(site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/"))
    listed = []

    def lister(*, auth_mode, credential_ref):
        listed.append((auth_mode, credential_ref))
        # What Google reports for this person: their own properties only.
        return [GSCProperty("sc-domain:tennisino.com", "siteOwner")]

    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
            site_store=sites,
            run_store=RunStore(repository),
            gsc_property_lister=lister,
            google_oauth=service,
            tenant_name=lambda tenant_id: "Tennisino Shop",
        )
    )
    client = TestClient(app, base_url="https://hoshyarseo.ir")
    return client, sites, listed


AUTH = {"Authorization": "Bearer k"}


def press_continue(client, state, **headers):
    return client.post(
        "/v1/oauth/google/begin", data={"state": state}, headers=headers, follow_redirects=False
    )


def connect_google(client):
    link = client.post("/v1/google/connect", headers=AUTH).json()["connect_url"]
    begin = client.get(link.removeprefix("https://hoshyarseo.ir"))
    assert begin.status_code == 200
    state = state_of(link)
    pressed = press_continue(client, state, **{"sec-fetch-site": "same-origin"})
    assert pressed.status_code == 303
    assert pressed.headers["location"].startswith("https://accounts.google.com/")
    return client.get(f"/v1/oauth/google/callback?state={state}&code=code-1"), pressed


def test_the_customer_signs_in_and_sees_which_account_is_being_connected():
    client, _, _ = api_client()
    link = client.post("/v1/google/connect", headers=AUTH).json()["connect_url"]
    page = client.get(link.removeprefix("https://hoshyarseo.ir"))
    assert "Tennisino Shop" in page.text, "the account is named before going to Google"
    assert page.headers["x-frame-options"] == "DENY"
    assert "set-cookie" not in page.headers, "opening the page spends nothing"

    pressed = press_continue(client, state_of(link))
    assert "hoshyarseo_oauth" in pressed.headers["set-cookie"]
    assert "HttpOnly" in pressed.headers["set-cookie"] and "Secure" in pressed.headers["set-cookie"]
    done = client.get(f"/v1/oauth/google/callback?state={state_of(link)}&code=code-1")

    assert done.status_code == 200, done.text
    assert "owner@tennisino.com" in done.text
    status = client.get("/v1/google/connection", headers=AUTH).json()
    assert status["connected"] is True and status["email"] == "owner@tennisino.com"


def test_a_signed_in_customer_connects_a_property_without_any_ownership_step():
    client, sites, listed = api_client()
    connect_google(client)

    available = client.post(
        "/v1/sites/s1/connections/gsc/available", headers=AUTH, json={"use_google_account": True}
    )
    assert available.status_code == 200, available.text
    assert [p["site_url"] for p in available.json()["properties"]] == ["sc-domain:tennisino.com"]

    configured = client.put(
        "/v1/sites/s1/connections/gsc",
        headers=AUTH,
        json={"property_url": "sc-domain:tennisino.com", "use_google_account": True},
    )
    assert configured.status_code == 200, configured.text
    gsc = sites.get("s1").gsc
    assert gsc.auth_mode == "oauth_refresh_token"
    assert gsc.credential_ref.provider.value == "database"
    assert listed[0][0] == "oauth_refresh_token"

    run = client.post(
        "/v1/sites/s1/runs",
        headers=AUTH,
        json={
            "start_date": "2026-09-01", "end_date": "2026-09-07",
            "normalized_url": "https://tennisino.com/x", "normalized_query": "q", "candidate_id": "c",
        },
    )
    assert run.status_code == 202, run.text


def test_using_google_before_connecting_it_says_so():
    client, _, _ = api_client()
    response = client.post(
        "/v1/sites/s1/connections/gsc/available", headers=AUTH, json={"use_google_account": True}
    )
    assert response.status_code == 409
    assert "/v1/google/connect" in response.json()["detail"]


def test_a_callback_without_the_browser_cookie_shows_an_error_page():
    client, _, _ = api_client()
    link = client.post("/v1/google/connect", headers=AUTH).json()["connect_url"]
    press_continue(client, state_of(link))
    client.cookies.clear()

    page = client.get(f"/v1/oauth/google/callback?state={state_of(link)}&code=c")

    assert page.status_code == 400
    assert 'dir="rtl"' in page.text
    assert client.get("/v1/google/connection", headers=AUTH).json()["connected"] is False


def test_sign_in_is_off_until_configured():
    from fastapi.testclient import TestClient

    from app.api.app import APIDependencies, create_app
    from app.api.auth import APIKeyAuthenticator
    from app.jobs import JobHandlerRegistry, JobScheduler, JobStore

    repository = InMemoryRepository()
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="p1"),
        )
    )
    assert TestClient(app).post("/v1/google/connect", headers=AUTH).status_code == 503


def test_half_a_configuration_is_refused_at_startup():
    from app.runtime.config import RuntimeConfig

    with pytest.raises(ValueError, match="together"):
        RuntimeConfig(api_key="k", database_dsn="d", google_oauth_client_id="id")


def test_another_site_cannot_press_continue_for_the_customer():
    client, _, _ = api_client()
    link = client.post("/v1/google/connect", headers=AUTH).json()["connect_url"]
    pressed = press_continue(client, state_of(link), **{"sec-fetch-site": "cross-site"})
    assert pressed.status_code == 400
    assert "set-cookie" not in pressed.headers
