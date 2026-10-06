"""Sign in with Google: who the person is, nothing more.

Separate from connecting Search Console (app.onboarding.google_oauth): this
asks only for "openid email", reads no Search Console data, and is not tied
to a tenant before it starts, because who the tenant is, is what it finds out.

The same defences as that flow: a single-use state with a PKCE verifier,
bound to the browser that started it by an HttpOnly cookie, spent before the
code is exchanged, and expiring after ten minutes.
"""
from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict

from app.accounts.service import AttemptLimiter, address_key
from app.models.persistence import PersistenceRecord
from app.onboarding.google_oauth import (
    AUTHORIZATION_ENDPOINT,
    GoogleOAuthConfig,
    _challenge,
    default_token_exchange,
)
from app.persistence.contracts import Repository

LOGIN_CALLBACK_PATH = "/v1/auth/google/callback"
LOGIN_COOKIE = "hoshyarseo_login"
STATE_TTL = timedelta(minutes=10)
STATE_AGGREGATE = "login_state"


class GoogleLoginError(Exception):
    """The Google sign-in did not complete. The message is safe to show."""


class LoginState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str
    code_verifier: str
    browser_nonce: str
    created_at: datetime
    used: bool = False


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str | None
    email_verified: bool


class GoogleLogin:
    def __init__(
        self,
        *,
        config: GoogleOAuthConfig,
        repository: Repository,
        exchange: Callable[[dict], tuple[int, dict]] = default_token_exchange,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        starts_per_address: AttemptLimiter | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._exchange = exchange
        self._clock = clock
        # Each start writes a record, and starting needs no account: without a
        # limit, anyone could fill the disk by fetching the start URL in a loop.
        # "is None", not "or": an empty limiter has length 0 and is falsy.
        if starts_per_address is None:
            starts_per_address = AttemptLimiter(limit=20, window_seconds=10 * 60)
        self._starts = starts_per_address

    @property
    def redirect_uri(self) -> str:
        return self._config.public_base_url.rstrip("/") + LOGIN_CALLBACK_PATH

    def start(self, address: str = "unknown") -> tuple[str, str]:
        """(Google URL to send the browser to, nonce to set as its cookie)."""
        address = address_key(address)
        if not self._starts.attempt(address):
            raise GoogleLoginError("Too many sign-in attempts. Wait a few minutes and try again.")
        state = LoginState(
            state_id=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(64),
            browser_nonce=secrets.token_urlsafe(32),
            created_at=self._clock(),
        )
        self._repository.create(self._record(state, version=1))
        query = {
            "client_id": self._config.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "prompt": "select_account",
            "state": state.state_id,
            "code_challenge": _challenge(state.code_verifier),
            "code_challenge_method": "S256",
        }
        return AUTHORIZATION_ENDPOINT + "?" + urlencode(query), state.browser_nonce

    def complete(
        self, *, state_id: str, code: str | None, error: str | None, browser_nonce: str | None
    ) -> GoogleIdentity:
        record = self._repository.get(aggregate_type=STATE_AGGREGATE, aggregate_id=state_id or "-")
        if record is None:
            raise GoogleLoginError("This sign-in link is not valid. Try again.")
        state = LoginState.model_validate(record.payload)
        if state.used:
            raise GoogleLoginError("This sign-in was already used. Try again.")
        if self._clock() - state.created_at > STATE_TTL:
            raise GoogleLoginError("This sign-in took too long. Try again.")
        if not browser_nonce or not secrets.compare_digest(browser_nonce, state.browser_nonce):
            raise GoogleLoginError("This sign-in was started in another browser. Try again.")
        # Spent before anything else: a replayed callback gets nowhere.
        self._repository.replace(
            self._record(state.model_copy(update={"used": True}), version=record.version + 1),
            expected_version=record.version,
        )
        if error or not code:
            raise GoogleLoginError("Google sign-in was cancelled.")
        try:
            status, body = self._exchange(
                {
                    "code": code,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": state.code_verifier,
                }
            )
        except Exception as exc:
            raise GoogleLoginError("Google could not be reached. Try again.") from exc
        if status != 200:
            raise GoogleLoginError("Google refused the sign-in. Try again.")
        claims = _claims(body.get("id_token"))
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise GoogleLoginError("Google did not say who signed in.")
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        return GoogleIdentity(subject=subject, email=email, email_verified=claims.get("email_verified") is True)

    @staticmethod
    def _record(state: LoginState, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"login-state:{state.state_id}:v{version}",
            aggregate_type=STATE_AGGREGATE,
            aggregate_id=state.state_id,
            version=version,
            payload=state.model_dump(mode="json"),
        )


def _claims(id_token) -> dict:
    """The id_token's claims. It came from Google's token endpoint over TLS in
    answer to our own request with our client secret and PKCE verifier, which
    OpenID Connect accepts in place of checking the signature."""
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        return {}
    payload = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}
