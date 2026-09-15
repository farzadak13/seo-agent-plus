"""GSCClient with a real transport and a refreshing credential.

Two changes meet here. The client now takes a token provider instead of a
fixed string, so a run longer than an hour does not die on an expired token.
And when the transport kept the error body, the client uses it: a 403 that is
an exhausted quota gets retried, a 403 that is a missing grant does not.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.gsc.client import GSCClient, GSCClientError
from app.gsc.config import GSCClientConfig
from app.gsc.transport import GoogleResponse


def google_error(code: int, message: str, reason: str | None = None) -> bytes:
    error: dict = {"code": code, "message": message}
    if reason is not None:
        error["errors"] = [{"message": message, "domain": "global", "reason": reason}]
    return json.dumps({"error": error}).encode()


def ok_page(rows: list | None = None) -> GoogleResponse:
    body = {"rows": rows or [], "responseAggregationType": "byPage"}
    return GoogleResponse(status_code=200, body=json.dumps(body).encode(), headers={})


class RecordingTransport:
    """Stands in for GoogleTransport, returning queued responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, path, *, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "path": path, "headers": headers or {}, "json": json})
        return self._responses.pop(0) if self._responses else ok_page()


class CountingProvider:
    def __init__(self, tokens):
        self._tokens = list(tokens)
        self.calls = 0

    def token(self) -> str:
        self.calls += 1
        return self._tokens[min(self.calls - 1, len(self._tokens) - 1)]


def client(transport, *, provider=None, config=None, sleeps=None):
    return GSCClient(
        config or GSCClientConfig(page_size=10, max_retries=2),
        request_fn=transport,
        sleep_fn=(sleeps.append if sleeps is not None else (lambda _: None)),
        token_provider=provider,
    )


def run(instance):
    return instance.query(
        site_id="site-1",
        site_url="https://pama.shop/",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 7),
    )


# --- the token comes from the provider --------------------------------------


def test_the_provider_supplies_the_bearer_token():
    transport = RecordingTransport([ok_page()])
    run(client(transport, provider=CountingProvider(["token-abc"])))

    assert transport.calls[0]["headers"]["Authorization"] == "Bearer token-abc"


def test_the_provider_is_asked_on_every_request_so_a_refresh_is_picked_up():
    """Caching belongs to the provider. The client must not hold a stale token."""
    provider = CountingProvider(["first", "second", "third"])
    # Two full pages force a second request.
    transport = RecordingTransport([ok_page([{"keys": ["a"], "clicks": 1}] * 10), ok_page()])

    run(client(transport, provider=provider))

    assert provider.calls >= 2
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer first"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer second"


def test_a_static_token_still_works_when_no_provider_is_given():
    transport = RecordingTransport([ok_page()])
    instance = GSCClient(
        GSCClientConfig(oauth_access_token="ya29.static"),
        request_fn=transport,
        sleep_fn=lambda _: None,
    )

    run(instance)

    assert transport.calls[0]["headers"]["Authorization"] == "Bearer ya29.static"


def test_no_credential_means_no_authorization_header():
    transport = RecordingTransport([ok_page()])
    run(client(transport))

    assert "Authorization" not in transport.calls[0]["headers"]


# --- the error body decides whether to retry --------------------------------


def test_an_exhausted_quota_is_retried():
    """403 + quotaExceeded is temporary; the same status without it is not."""
    sleeps: list[float] = []
    transport = RecordingTransport(
        [
            GoogleResponse(403, google_error(403, "Quota exceeded.", "quotaExceeded"), {}),
            ok_page(),
        ]
    )

    run(client(transport, provider=CountingProvider(["t"]), sleeps=sleeps))

    assert len(transport.calls) == 2
    assert sleeps, "a retry should have backed off"


def test_a_missing_grant_is_not_retried():
    transport = RecordingTransport(
        [GoogleResponse(403, google_error(403, "User does not have permission.", "forbidden"), {})]
    )

    with pytest.raises(GSCClientError) as raised:
        run(client(transport, provider=CountingProvider(["t"])))

    assert raised.value.retryable is False
    assert len(transport.calls) == 1, "a permission failure must not be retried"


def test_the_failure_message_carries_the_cause():
    """'HTTP 403' sends the reader looking in the wrong place."""
    transport = RecordingTransport(
        [GoogleResponse(403, google_error(403, "User does not have permission.", "forbidden"), {})]
    )

    with pytest.raises(GSCClientError) as raised:
        run(client(transport, provider=CountingProvider(["t"])))

    message = str(raised.value)
    assert "403" in message
    assert "permission_denied" in message
    assert "does not have permission" in message


def test_an_invalid_token_is_reported_as_auth_not_permission():
    transport = RecordingTransport(
        [GoogleResponse(401, google_error(401, "Login Required.", "required"), {})]
    )

    with pytest.raises(GSCClientError) as raised:
        run(client(transport, provider=CountingProvider(["t"])))

    assert "auth_invalid" in str(raised.value)
    assert raised.value.retryable is False


def test_a_rate_limit_is_retried_until_it_clears():
    sleeps: list[float] = []
    transport = RecordingTransport(
        [
            GoogleResponse(429, google_error(429, "Too many requests", "rateLimitExceeded"), {}),
            GoogleResponse(429, google_error(429, "Too many requests", "rateLimitExceeded"), {}),
            ok_page(),
        ]
    )

    run(client(transport, provider=CountingProvider(["t"]), sleeps=sleeps))

    assert len(transport.calls) == 3
    assert sleeps == [1, 2], "backoff should grow"


def test_retries_stop_at_the_configured_limit():
    transport = RecordingTransport(
        [GoogleResponse(503, b"", {}) for _ in range(10)]
    )

    with pytest.raises(GSCClientError):
        run(client(transport, provider=CountingProvider(["t"])))

    assert len(transport.calls) == 3, "max_retries=2 means three attempts"


def test_a_transport_without_a_body_still_classifies_by_status():
    """Older fakes return only status_code; the client must not require more."""

    class Bare:
        status_code = 503

        def json(self):
            return {}

    transport = RecordingTransport([Bare(), Bare(), Bare()])

    with pytest.raises(GSCClientError) as raised:
        run(client(transport, provider=CountingProvider(["t"])))

    assert raised.value.status_code == 503
