"""HTTP transport for Google APIs.

Two things this fixes, both found by looking at real responses rather than
reasoning about them:

**The error body is evidence, not noise.** The previous transport turned every
HTTP error into an empty body, so 401 (the token is wrong), 403 (this account
was never granted the property) and 403-with-reason-quotaExceeded (the account
is fine, the project ran out of budget) were indistinguishable. Those three
have completely different fixes and only the body says which one happened.

**Egress is configuration.** Development happens where googleapis.com is
unreachable and production happens where it is, so the proxy is a setting on
one session rather than a second code path.
"""
from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

GOOGLE_API_BASE = "https://www.googleapis.com"


class GoogleErrorKind(StrEnum):
    """What went wrong, at the level a caller can actually act on."""

    OK = "ok"
    AUTH_INVALID = "auth_invalid"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        return self in {
            GoogleErrorKind.RATE_LIMITED,
            GoogleErrorKind.QUOTA_EXCEEDED,
            GoogleErrorKind.TRANSIENT,
        }


# Google puts the useful distinction in error.errors[].reason, not in the
# status code. A 403 is permission or quota depending on this field alone.
_QUOTA_REASONS = {
    "quotaexceeded",
    "dailylimitexceeded",
    "usageLimits".lower(),
}
_RATE_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "backenderror"}


@dataclass(frozen=True)
class GoogleResponse:
    """A response that keeps its body no matter the status code."""

    status_code: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        if not self.body:
            return {}
        try:
            return json_module.loads(self.body)
        except ValueError:
            return {}

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def reason(self) -> str | None:
        """Google's machine-readable reason, when it sent one."""
        payload = self.json()
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            candidate = errors[0].get("reason")
            if isinstance(candidate, str):
                return candidate
        status = error.get("status")
        return status if isinstance(status, str) else None

    @property
    def message(self) -> str | None:
        payload = self.json()
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            candidate = payload["error"].get("message")
            if isinstance(candidate, str):
                return candidate
        return None

    def classify(self) -> GoogleErrorKind:
        if self.status_code < 400:
            return GoogleErrorKind.OK

        reason = (self.reason or "").lower()

        if self.status_code == 401:
            return GoogleErrorKind.AUTH_INVALID
        if self.status_code == 403:
            if reason in _QUOTA_REASONS:
                return GoogleErrorKind.QUOTA_EXCEEDED
            if reason in _RATE_REASONS:
                return GoogleErrorKind.RATE_LIMITED
            return GoogleErrorKind.PERMISSION_DENIED
        if self.status_code == 404:
            return GoogleErrorKind.NOT_FOUND
        if self.status_code == 429:
            return GoogleErrorKind.RATE_LIMITED
        if self.status_code in {400, 405, 409, 411, 413, 415, 422}:
            return GoogleErrorKind.INVALID_REQUEST
        if self.status_code >= 500:
            return GoogleErrorKind.TRANSIENT
        return GoogleErrorKind.UNKNOWN

    def diagnostic(self) -> str:
        """One line a human can act on, without leaking the request body."""
        parts = [f"HTTP {self.status_code}", self.classify().value]
        if self.reason:
            parts.append(f"reason={self.reason}")
        if self.message:
            parts.append(self.message[:200])
        return " | ".join(parts)


@dataclass(frozen=True)
class EgressConfig:
    """Where outbound calls go, and through what.

    ``proxy_url`` exists because development happens where googleapis.com is
    blocked. In production it is None and the same code path is used.
    """

    base_url: str = GOOGLE_API_BASE
    proxy_url: str | None = None
    timeout_seconds: float = 30.0
    verify_tls: bool = True

    def proxies(self) -> dict[str, str] | None:
        if not self.proxy_url:
            return None
        return {"http": self.proxy_url, "https": self.proxy_url}


def build_session(config: EgressConfig):
    """One requests session, so the proxy is configured in exactly one place."""
    import requests

    session = requests.Session()
    if config.proxies():
        session.proxies.update(config.proxies())
    session.verify = config.verify_tls
    return session


class GoogleTransport:
    """Callable matching the request_fn contract GSCClient already expects."""

    def __init__(self, config: EgressConfig, *, session=None) -> None:
        self._config = config
        self._session = session if session is not None else build_session(config)

    @property
    def config(self) -> EgressConfig:
        return self._config

    @property
    def session(self):
        """Exposed so credentials mint tokens through this same proxy.

        A token minted over a direct connection while data is fetched through
        a proxy is the failure that looks like a permission error: the fetch
        works from one address and the token was issued to another.
        """
        return self._session

    def __call__(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> GoogleResponse:
        url = path if path.startswith("http") else f"{self._config.base_url}{path}"
        response = self._session.request(
            method,
            url,
            headers=headers or {},
            json=json,
            timeout=timeout if timeout is not None else self._config.timeout_seconds,
        )
        return GoogleResponse(
            status_code=response.status_code,
            body=response.content or b"",
            headers={key.lower(): value for key, value in response.headers.items()},
        )


__all__ = [
    "EgressConfig",
    "GOOGLE_API_BASE",
    "GoogleErrorKind",
    "GoogleResponse",
    "GoogleTransport",
    "build_session",
]
