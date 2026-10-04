"""Sign in with Google: the customer grants read-only Search Console access.

This replaces the step customers found hardest to trust — adding an unknown
service-account email with full access to their Search Console. Instead they
click "connect", sign in to Google, and approve read-only access on Google's
own screen. They can revoke it from their Google account at any time.

It also ends the cross-tenant problem at the source: Google answers with the
properties *this person* can see, so nothing has to be filtered and no
ownership proof is needed.

The flow, and what each step defends against:

1. ``start`` (authenticated API call) records a single-use state with a PKCE
   verifier and a browser nonce, and returns a link to our own ``begin``
   page — not to Google.
2. ``begin`` (the customer's browser) can be opened once. It sets the nonce
   as an HttpOnly cookie and shows which HoshyarSEO account is about to be
   connected, then sends the browser to Google. Without this, anyone could
   send a victim their own Google link and receive the victim's Search
   Console in their own account; the cookie ties the callback to the browser
   that began, and the page names the account so a lure is visible.
3. ``complete`` (Google's redirect back) requires the state unused and
   unexpired, the cookie to match, and Search Console actually granted —
   Google lets a person untick a scope. The refresh token goes into the
   vault, encrypted and bound to the tenant; it is never returned.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field

from app.gsc.credentials import SEARCH_CONSOLE_READONLY
from app.models.persistence import PersistenceRecord
from app.models.sites import SecretProvider, SecretRef
from app.persistence.contracts import Repository


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email", SEARCH_CONSOLE_READONLY)
STATE_TTL = timedelta(minutes=10)
CALLBACK_PATH = "/v1/oauth/google/callback"
BEGIN_PATH = "/v1/oauth/google/begin"
COOKIE_NAME = "hoshyarseo_oauth"
REFRESH_TOKEN_NAME = "google_refresh_token"

STATE_AGGREGATE = "google_oauth_state"
CONNECTION_AGGREGATE = "google_connection"


class OAuthError(RuntimeError):
    """The sign-in did not complete. The message is safe to show the customer."""


class GoogleOAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1, repr=False)
    public_base_url: str = Field(min_length=1)

    @property
    def redirect_uri(self) -> str:
        return self.public_base_url.rstrip("/") + CALLBACK_PATH


class OAuthState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str
    tenant_id: str
    code_verifier: str = Field(repr=False)
    browser_nonce: str = Field(repr=False)
    created_at: datetime
    begun: bool = False
    used: bool = False


class GoogleConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    email: str | None = None
    scopes: list[str]
    credential_ref: SecretRef
    connected_at: datetime


def tenant_credential_ref(tenant_id: str) -> SecretRef:
    return SecretRef(provider=SecretProvider.DATABASE, key=f"tenant:{tenant_id}/{REFRESH_TOKEN_NAME}")


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def default_token_exchange(form: dict) -> tuple[int, dict]:
    import requests

    response = requests.post(TOKEN_ENDPOINT, data=form, timeout=30)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


class GoogleOAuthService:
    def __init__(
        self,
        *,
        config: GoogleOAuthConfig,
        repository: Repository,
        vault,
        exchange: Callable[[dict], tuple[int, dict]] = default_token_exchange,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._config = config
        self._repository = repository
        self._vault = vault
        self._exchange = exchange
        self._clock = clock

    @property
    def client_id(self) -> str:
        return self._config.client_id

    @property
    def client_secret(self) -> str:
        return self._config.client_secret

    # --- 1. start -----------------------------------------------------------

    def start(self, tenant_id: str) -> str:
        state = OAuthState(
            state_id=secrets.token_urlsafe(32),
            tenant_id=tenant_id,
            code_verifier=secrets.token_urlsafe(64),
            browser_nonce=secrets.token_urlsafe(32),
            created_at=self._clock(),
        )
        self._repository.create(self._state_record(state, version=1))
        return self._config.public_base_url.rstrip("/") + BEGIN_PATH + "?" + urlencode(
            {"state": state.state_id}
        )

    # --- 2. begin -----------------------------------------------------------

    def begin(self, state_id: str) -> tuple[str, str, str]:
        """Return (tenant_id, google_url, browser_nonce). Usable once."""
        state, version = self._live_state(state_id)
        if state.begun:
            raise OAuthError("This link has already been opened. Start the connection again.")
        self._replace_state(state.model_copy(update={"begun": True}), version)
        query = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # A refresh token, every time: without prompt=consent Google
            # returns one only on the very first grant.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state.state_id,
            "code_challenge": _challenge(state.code_verifier),
            "code_challenge_method": "S256",
        }
        return state.tenant_id, AUTHORIZATION_ENDPOINT + "?" + urlencode(query), state.browser_nonce

    # --- 3. complete --------------------------------------------------------

    def complete(
        self, *, state_id: str, code: str | None, error: str | None, browser_nonce: str | None
    ) -> GoogleConnection:
        state, version = self._live_state(state_id)
        if not state.begun or state.used:
            raise OAuthError("This sign-in was already used. Start the connection again.")
        if not browser_nonce or not secrets.compare_digest(browser_nonce, state.browser_nonce):
            raise OAuthError(
                "This sign-in was started in another browser. Start it again from your own account."
            )
        # Spent before anything else, so a replayed callback cannot try again.
        self._replace_state(state.model_copy(update={"used": True}), version)

        if error:
            raise OAuthError("Access was not granted on Google's screen.")
        if not code:
            raise OAuthError("Google did not return an authorization code.")

        status, body = self._exchange(
            {
                "code": code,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "redirect_uri": self._config.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": state.code_verifier,
            }
        )
        if status != 200:
            raise OAuthError(f"Google refused the sign-in ({body.get('error', status)}).")
        granted = str(body.get("scope", "")).split()
        if SEARCH_CONSOLE_READONLY not in granted:
            raise OAuthError(
                "Search Console access was not ticked on Google's screen. Connect again and allow it."
            )
        refresh_token = body.get("refresh_token")
        if not refresh_token:
            raise OAuthError("Google did not return a long-lived token. Connect again.")

        ref = self._vault.put(
            tenant_id=state.tenant_id,
            site_id=f"tenant:{state.tenant_id}",
            name=REFRESH_TOKEN_NAME,
            value=refresh_token,
        )
        connection = GoogleConnection(
            tenant_id=state.tenant_id,
            email=_email_from_id_token(body.get("id_token")),
            scopes=granted,
            credential_ref=ref,
            connected_at=self._clock(),
        )
        self._save_connection(connection)
        return connection

    def connection(self, tenant_id: str) -> GoogleConnection | None:
        record = self._repository.get(aggregate_type=CONNECTION_AGGREGATE, aggregate_id=tenant_id)
        return None if record is None else GoogleConnection.model_validate(record.payload)

    # --- persistence --------------------------------------------------------

    def _live_state(self, state_id: str) -> tuple[OAuthState, int]:
        record = self._repository.get(aggregate_type=STATE_AGGREGATE, aggregate_id=state_id or "-")
        if record is None:
            raise OAuthError("This connection link is not valid. Start the connection again.")
        state = OAuthState.model_validate(record.payload)
        if self._clock() - state.created_at > STATE_TTL:
            raise OAuthError("This connection link has expired. Start the connection again.")
        return state, record.version

    def _replace_state(self, state: OAuthState, version: int) -> None:
        # expected_version makes each transition single-use under concurrency.
        self._repository.replace(self._state_record(state, version=version + 1), expected_version=version)

    @staticmethod
    def _state_record(state: OAuthState, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"oauth-state:{state.state_id}:v{version}",
            aggregate_type=STATE_AGGREGATE,
            aggregate_id=state.state_id,
            version=version,
            payload=state.model_dump(mode="json"),
            tenant_id=state.tenant_id,
        )

    def _save_connection(self, connection: GoogleConnection) -> None:
        current = self._repository.get(
            aggregate_type=CONNECTION_AGGREGATE, aggregate_id=connection.tenant_id
        )
        version = 1 if current is None else current.version + 1
        record = PersistenceRecord(
            record_id=f"google-connection:{connection.tenant_id}:v{version}",
            aggregate_type=CONNECTION_AGGREGATE,
            aggregate_id=connection.tenant_id,
            version=version,
            payload=connection.model_dump(mode="json"),
            tenant_id=connection.tenant_id,
        )
        if current is None:
            self._repository.create(record)
        else:
            self._repository.replace(record, expected_version=current.version)


def _email_from_id_token(id_token: str | None) -> str | None:
    """The email claim, for display only.

    The token came straight from Google's token endpoint over TLS in answer to
    our own request, which OpenID Connect accepts as validation; nothing here
    is used for authorization.
    """
    if not id_token or id_token.count(".") != 2:
        return None
    payload = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    email = claims.get("email")
    return email if isinstance(email, str) else None
