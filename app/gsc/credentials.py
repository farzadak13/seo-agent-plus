"""Google credentials, behind one contract.

Three kinds exist because they arrive at different stages of the product:

* ``service_account`` — what a customer grants today by adding one email in
  their own Search Console. No Google verification needed, so it works now.
* ``oauth_refresh_token`` — what a customer grants by clicking "connect".
  Smoother, and Google enforces per-customer isolation on the token itself,
  but it needs the OAuth verification process first.
* ``access_token`` — a raw token with an hour of life. Development only; it
  cannot refresh itself and has no place in a deployment.

**The isolation warning that shapes everything above.** A service account is
shared across customers: every customer grants the same email, so a token
minted from it can read every property that ever granted it. Google is not
isolating tenants for us in that mode — we are. A tenant's scope must come
from our own database, never from what ``sites.list`` happens to return.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

SEARCH_CONSOLE_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
SEARCH_CONSOLE_WRITE = "https://www.googleapis.com/auth/webmasters"

# Refresh a little early: a token that expires mid-request is a failure that
# looks like a permission problem.
EXPIRY_MARGIN_SECONDS = 120


class CredentialKind(StrEnum):
    SERVICE_ACCOUNT = "service_account"
    OAUTH_REFRESH_TOKEN = "oauth_refresh_token"
    ACCESS_TOKEN = "access_token"


class CredentialError(RuntimeError):
    """Raised when a credential cannot be built or cannot mint a token."""


class TokenProvider(Protocol):
    """Returns a currently valid access token, refreshing when needed."""

    @property
    def kind(self) -> CredentialKind:
        ...

    @property
    def isolated_per_tenant(self) -> bool:
        """False when one credential can see more than one tenant's data."""
        ...

    def token(self) -> str:
        ...


@dataclass(frozen=True)
class StaticAccessTokenProvider:
    """A token someone pasted. Expires in an hour and cannot renew itself."""

    access_token: str

    @property
    def kind(self) -> CredentialKind:
        return CredentialKind.ACCESS_TOKEN

    @property
    def isolated_per_tenant(self) -> bool:
        return True

    def token(self) -> str:
        if not self.access_token.strip():
            raise CredentialError("The configured access token is empty.")
        return self.access_token


class ServiceAccountTokenProvider:
    """Mints tokens from a service account key, caching until near expiry.

    Signing and refresh are delegated to google-auth rather than hand-rolled:
    clock skew, key formats and expiry semantics are exactly the places where
    a home-grown implementation is wrong in ways that only show up in
    production.

    The HTTP session is injected so token minting goes through the same egress
    path — and therefore the same proxy — as every other Google call.
    """

    def __init__(
        self,
        *,
        key_json: str | dict,
        session,
        scopes: tuple[str, ...] = (SEARCH_CONSOLE_READONLY,),
    ) -> None:
        info = json.loads(key_json) if isinstance(key_json, str) else dict(key_json)
        for field in ("client_email", "private_key", "token_uri"):
            if field not in info:
                raise CredentialError(
                    f"The service account key has no '{field}'. "
                    "Is this a service account key file?"
                )
        self._info = info
        self._session = session
        self._scopes = list(scopes)
        self._credentials = None

    @property
    def kind(self) -> CredentialKind:
        return CredentialKind.SERVICE_ACCOUNT

    @property
    def isolated_per_tenant(self) -> bool:
        # One key, many customers. See the module docstring.
        return False

    @property
    def client_email(self) -> str:
        return self._info["client_email"]

    def _build(self):
        try:
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover
            raise CredentialError(
                "google-auth is required for service account credentials"
            ) from exc
        try:
            return service_account.Credentials.from_service_account_info(
                self._info, scopes=self._scopes
            )
        except Exception as exc:
            raise CredentialError(f"The service account key was rejected: {exc}") from exc

    def token(self) -> str:
        if self._credentials is None:
            self._credentials = self._build()

        if not self._is_fresh(self._credentials):
            self._refresh(self._credentials)
        return self._credentials.token

    @staticmethod
    def _is_fresh(credentials) -> bool:
        if not credentials.token or credentials.expiry is None:
            return bool(credentials.token) and credentials.expiry is None
        from datetime import datetime, timedelta, timezone

        # google-auth stores expiry as a naive UTC datetime today. Reading it as
        # UTC rather than as local time is the difference between a token that
        # looks fresh and one that is already dead; an aware value is accepted
        # too, so a future google-auth does not silently break the comparison.
        expiry = credentials.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        remaining = expiry - datetime.now(timezone.utc)
        return remaining > timedelta(seconds=EXPIRY_MARGIN_SECONDS)

    def _refresh(self, credentials) -> None:
        try:
            from google.auth.transport.requests import Request
        except ImportError as exc:  # pragma: no cover
            raise CredentialError("google-auth is required to refresh tokens") from exc
        try:
            credentials.refresh(Request(session=self._session))
        except Exception as exc:
            raise CredentialError(
                "Could not mint a Google access token. Reaching the API is not "
                f"enough; this is where a blocked project or region shows up: {exc}"
            ) from exc


class RefreshTokenProvider:
    """OAuth on behalf of one customer. Google isolates this one for us.

    Not reachable until the OAuth consent screen passes Google's verification
    for the Search Console scope, which is a multi-week external process. The
    class exists now so the rest of the system is already written against a
    credential that can be per-tenant.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        session,
        token_uri: str = "https://oauth2.googleapis.com/token",
        scopes: tuple[str, ...] = (SEARCH_CONSOLE_READONLY,),
    ) -> None:
        if not (client_id and client_secret and refresh_token):
            raise CredentialError("client_id, client_secret and refresh_token are all required.")
        self._session = session
        self._scopes = list(scopes)
        self._credentials = None
        self._args = {
            "token": None,
            "refresh_token": refresh_token,
            "token_uri": token_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": self._scopes,
        }

    @property
    def kind(self) -> CredentialKind:
        return CredentialKind.OAUTH_REFRESH_TOKEN

    @property
    def isolated_per_tenant(self) -> bool:
        return True

    def token(self) -> str:
        if self._credentials is None:
            try:
                from google.oauth2.credentials import Credentials
            except ImportError as exc:  # pragma: no cover
                raise CredentialError("google-auth is required for OAuth credentials") from exc
            self._credentials = Credentials(**self._args)

        if not self._credentials.token or self._credentials.expired:
            from google.auth.transport.requests import Request

            try:
                self._credentials.refresh(Request(session=self._session))
            except Exception as exc:
                raise CredentialError(f"Could not refresh the OAuth token: {exc}") from exc
        return self._credentials.token


def build_token_provider(*, kind: str, secret: str, session, **options) -> TokenProvider:
    """Build a provider from the stored credential kind and its secret."""
    resolved = CredentialKind(kind)
    if resolved is CredentialKind.ACCESS_TOKEN:
        return StaticAccessTokenProvider(access_token=secret)
    if resolved is CredentialKind.SERVICE_ACCOUNT:
        return ServiceAccountTokenProvider(key_json=secret, session=session)
    if resolved is CredentialKind.OAUTH_REFRESH_TOKEN:
        missing = [name for name in ("client_id", "client_secret") if not options.get(name)]
        if missing:
            raise CredentialError(f"OAuth credentials need: {', '.join(missing)}")
        return RefreshTokenProvider(
            client_id=options["client_id"],
            client_secret=options["client_secret"],
            refresh_token=secret,
            session=session,
        )
    raise CredentialError(f"Unsupported credential kind: {kind}")


__all__ = [
    "CredentialError",
    "CredentialKind",
    "RefreshTokenProvider",
    "SEARCH_CONSOLE_READONLY",
    "SEARCH_CONSOLE_WRITE",
    "ServiceAccountTokenProvider",
    "StaticAccessTokenProvider",
    "TokenProvider",
    "build_token_provider",
]
