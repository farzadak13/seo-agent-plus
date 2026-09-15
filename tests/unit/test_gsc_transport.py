"""The error body is the diagnosis.

The previous transport discarded it, which made 401, 403-no-permission and
403-out-of-quota look identical from the outside. Those have three different
fixes — a wrong key, an ungranted property, an exhausted project — and only
the body distinguishes them. These tests pin that distinction, plus the proxy
configuration that lets the same code run where googleapis.com is blocked and
where it is not.
"""
from __future__ import annotations

import json

import pytest

from app.gsc.transport import (
    EgressConfig,
    GoogleErrorKind,
    GoogleResponse,
    GoogleTransport,
)


def response(status: int, payload: dict | str | None = None) -> GoogleResponse:
    if payload is None:
        body = b""
    elif isinstance(payload, str):
        body = payload.encode()
    else:
        body = json.dumps(payload).encode()
    return GoogleResponse(status_code=status, body=body, headers={})


def google_error(code: int, message: str, reason: str | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if reason is not None:
        error["errors"] = [{"message": message, "domain": "global", "reason": reason}]
    return {"error": error}


# --- the three that used to look the same -----------------------------------


def test_a_bad_token_is_told_apart_from_a_missing_grant():
    unauthorized = response(401, google_error(401, "Login Required.", "required"))
    forbidden = response(403, google_error(403, "User does not have sufficient permission.", "forbidden"))

    assert unauthorized.classify() is GoogleErrorKind.AUTH_INVALID
    assert forbidden.classify() is GoogleErrorKind.PERMISSION_DENIED


def test_an_exhausted_quota_is_not_reported_as_a_permission_problem():
    """Both arrive as 403. Only the reason says which, and only the body has it."""
    exhausted = response(403, google_error(403, "Quota exceeded.", "quotaExceeded"))

    assert exhausted.classify() is GoogleErrorKind.QUOTA_EXCEEDED
    assert exhausted.classify() is not GoogleErrorKind.PERMISSION_DENIED


def test_a_daily_limit_is_also_quota():
    assert response(403, google_error(403, "Daily Limit Exceeded", "dailyLimitExceeded")).classify() is (
        GoogleErrorKind.QUOTA_EXCEEDED
    )


def test_a_403_rate_limit_is_retryable_but_a_403_permission_is_not():
    rate = response(403, google_error(403, "Rate Limit Exceeded", "rateLimitExceeded"))
    permission = response(403, google_error(403, "Forbidden", "forbidden"))

    assert rate.classify().retryable is True
    assert permission.classify().retryable is False


# --- the rest of the status map ---------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, GoogleErrorKind.OK),
        (204, GoogleErrorKind.OK),
        (400, GoogleErrorKind.INVALID_REQUEST),
        (404, GoogleErrorKind.NOT_FOUND),
        (429, GoogleErrorKind.RATE_LIMITED),
        (500, GoogleErrorKind.TRANSIENT),
        (503, GoogleErrorKind.TRANSIENT),
        (418, GoogleErrorKind.UNKNOWN),
    ],
)
def test_status_codes_map_to_actionable_kinds(status, expected):
    assert response(status).classify() is expected


@pytest.mark.parametrize(
    "kind,retryable",
    [
        (GoogleErrorKind.RATE_LIMITED, True),
        (GoogleErrorKind.QUOTA_EXCEEDED, True),
        (GoogleErrorKind.TRANSIENT, True),
        (GoogleErrorKind.AUTH_INVALID, False),
        (GoogleErrorKind.PERMISSION_DENIED, False),
        (GoogleErrorKind.INVALID_REQUEST, False),
        (GoogleErrorKind.NOT_FOUND, False),
    ],
)
def test_only_the_kinds_worth_retrying_are_retryable(kind, retryable):
    assert kind.retryable is retryable


# --- the body survives ------------------------------------------------------


def test_the_body_is_kept_even_on_an_error():
    payload = google_error(403, "User does not have sufficient permission.", "forbidden")
    kept = response(403, payload)

    assert kept.json() == payload
    assert "sufficient permission" in kept.text


def test_a_diagnostic_line_names_the_cause():
    line = response(403, google_error(403, "Quota exceeded for quota metric.", "quotaExceeded")).diagnostic()

    assert "403" in line
    assert "quota_exceeded" in line
    assert "quotaExceeded" in line
    assert "Quota exceeded" in line


def test_an_empty_body_does_not_crash_classification():
    assert response(403).classify() is GoogleErrorKind.PERMISSION_DENIED
    assert response(403).reason is None
    assert response(403).json() == {}


def test_html_instead_of_json_does_not_crash():
    """A blocked network returns a page, not an API error."""
    html = response(403, "<html><body>access denied</body></html>")

    assert html.json() == {}
    assert html.classify() is GoogleErrorKind.PERMISSION_DENIED
    assert "access denied" in html.text


def test_a_status_style_error_body_is_also_read():
    """Newer endpoints use error.status instead of error.errors[].reason."""
    body = response(429, {"error": {"code": 429, "message": "too many", "status": "RESOURCE_EXHAUSTED"}})

    assert body.reason == "RESOURCE_EXHAUSTED"


# --- egress -----------------------------------------------------------------


class FakeSession:
    def __init__(self, status=200, payload=None, headers=None):
        self.proxies: dict[str, str] = {}
        self.verify = True
        self.calls: list[dict] = []
        self._status = status
        self._payload = payload if payload is not None else {"ok": True}
        self._headers = headers or {"Content-Type": "application/json"}

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        session = self

        class Result:
            status_code = session._status
            content = json_module_dumps(session._payload)
            headers = session._headers

        return Result()


def json_module_dumps(payload) -> bytes:
    return json.dumps(payload).encode()


def test_no_proxy_is_configured_by_default():
    assert EgressConfig().proxies() is None


def test_a_proxy_url_applies_to_both_schemes():
    proxies = EgressConfig(proxy_url="socks5h://127.0.0.1:1080").proxies()

    assert proxies == {
        "http": "socks5h://127.0.0.1:1080",
        "https": "socks5h://127.0.0.1:1080",
    }


def test_a_relative_path_is_resolved_against_the_base_url():
    session = FakeSession()
    transport = GoogleTransport(EgressConfig(), session=session)

    transport("POST", "/webmasters/v3/sites", headers={}, json={})

    assert session.calls[0]["url"] == "https://www.googleapis.com/webmasters/v3/sites"


def test_an_absolute_url_is_left_alone():
    session = FakeSession()
    transport = GoogleTransport(EgressConfig(), session=session)

    transport("POST", "https://oauth2.googleapis.com/token", headers={}, json={})

    assert session.calls[0]["url"] == "https://oauth2.googleapis.com/token"


def test_the_configured_timeout_is_used_when_the_caller_gives_none():
    session = FakeSession()
    transport = GoogleTransport(EgressConfig(timeout_seconds=12.5), session=session)

    transport("GET", "/x", headers={}, json=None)

    assert session.calls[0]["timeout"] == 12.5


def test_a_caller_timeout_wins():
    session = FakeSession()
    transport = GoogleTransport(EgressConfig(timeout_seconds=12.5), session=session)

    transport("GET", "/x", headers={}, json=None, timeout=3)

    assert session.calls[0]["timeout"] == 3


def test_an_error_status_is_returned_rather_than_raised():
    """The client decides what an error means; the transport just reports it."""
    session = FakeSession(status=403, payload=google_error(403, "no", "forbidden"))
    transport = GoogleTransport(EgressConfig(), session=session)

    result = transport("POST", "/x", headers={}, json={})

    assert result.status_code == 403
    assert result.classify() is GoogleErrorKind.PERMISSION_DENIED


def test_headers_come_back_lowercased_for_stable_lookup():
    session = FakeSession(headers={"Retry-After": "30", "Content-Type": "application/json"})
    transport = GoogleTransport(EgressConfig(), session=session)

    result = transport("GET", "/x", headers={}, json=None)

    assert result.headers["retry-after"] == "30"
