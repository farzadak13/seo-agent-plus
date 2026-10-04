"""SEO Signal adapter for search volume. The only code that knows its JSON.

API: https://panel.seosignal.net/openapi/ — every endpoint is a POST with a
JSON body, authenticated by an ``X-Api-Key`` header, answering
``{"ok": true, "data": ...}`` or ``{"error": {"code", "message"}}``.

Two things the documentation does not say, found against the live service:

* Bodies begin with UTF-8 byte-order marks (two of them, on the error
  responses seen so far). A JSON parser rejects a BOM, so read naively every
  answer, good or bad, looks like no answer at all.
* Keyword research shares a daily cap (50 requests by default) with the
  site-analysis endpoints. The bulk endpoint answers up to 1000 keywords for
  one request, so volume is always asked in bulk, never per keyword.

Only search volume is used. SEO Signal's rank tracker needs a project
created by hand in its panel for every site, which cannot be part of an
automated onboarding; rankings come from Search Console instead.

Swapping provider means writing another module like this one, against the
port in contracts.py; nothing outside this file changes.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from app.keyword_intel.contracts import (
    KeywordProviderError,
    ProviderAuthError,
    ProviderPlanError,
    ProviderQuotaError,
    ProviderUnavailableError,
)
from app.models.keyword_intel import Competition, KeywordVolume
from app.normalization.query import normalize_query


PROVIDER_ID = "seosignal"
BASE_URL = "https://panel.seosignal.net/openapi/v1"
MAX_BULK_KEYWORDS = 1000

# Competition is reported as a Persian word. Only "معمولی" appears in the
# documentation; the others are the natural neighbours and anything else maps
# to UNKNOWN rather than to a guess.
_COMPETITION = {
    "کم": Competition.LOW,
    "پایین": Competition.LOW,
    "معمولی": Competition.MEDIUM,
    "متوسط": Competition.MEDIUM,
    "زیاد": Competition.HIGH,
    "بالا": Competition.HIGH,
    "سخت": Competition.HIGH,
}


class RawResponse:
    """Status and body text, kept so an unexpected answer can be described."""

    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self.text = text


# POST ``body`` to ``url`` with ``headers``.
Transport = Callable[[str, dict[str, str], dict, float], RawResponse]


def default_transport(url: str, headers: dict[str, str], body: dict, timeout: float) -> RawResponse:
    import requests

    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderUnavailableError(f"SEO Signal could not be reached: {type(exc).__name__}") from exc
    return RawResponse(response.status_code, response.content.decode("utf-8", errors="replace"))


def parse_body(text: str) -> Any:
    """JSON, after the byte-order marks SEO Signal puts in front of it."""
    cleaned = text.lstrip("﻿").strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


class SeoSignalClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = BASE_URL,
        timeout_seconds: float = 30.0,
        transport: Transport = default_transport,
    ) -> None:
        if not api_key.strip():
            raise ValueError("An SEO Signal API key is required.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def __repr__(self) -> str:  # never the key
        return f"SeoSignalClient(base_url={self._base_url!r})"

    def call(self, endpoint: str, body: dict) -> Any:
        raw = self._transport(
            f"{self._base_url}/{endpoint}",
            {"X-Api-Key": self._api_key, "Content-Type": "application/json"},
            body,
            self._timeout,
        )
        payload = parse_body(raw.text)
        if raw.status == 200 and isinstance(payload, dict) and payload.get("ok") is True:
            return payload.get("data")
        raise _error(raw.status, payload, raw.text)


class NoDataError(KeywordProviderError):
    """REQUEST_FAILED: the request was valid but nothing was found."""


def _error(status: int, payload: Any, text: str) -> KeywordProviderError:
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code is None:
        # Not the documented shape at all. Say what did come back, briefly:
        # "HTTP 200" alone sent the last investigation in the wrong direction.
        preview = " ".join(text.lstrip("﻿").split())[:120]
        return KeywordProviderError(
            f"SEO Signal: unexpected answer (HTTP {status}): {preview or 'empty body'}"
        )
    message = f"SEO Signal: {code}"
    if code in {"INVALID_API_KEY", "ACCOUNT_DISABLED"}:
        return ProviderAuthError(message)
    if code == "PLAN_NOT_ALLOWED":
        return ProviderPlanError(message)
    if code == "RATE_LIMIT_EXCEEDED":
        return ProviderQuotaError(message)
    if code == "REQUEST_FAILED":
        return NoDataError(message)
    if status >= 500:
        return ProviderUnavailableError(message)
    return KeywordProviderError(message)


def _competition(label: Any) -> Competition:
    if not isinstance(label, str):
        return Competition.UNKNOWN
    return _COMPETITION.get(label.strip(), Competition.UNKNOWN)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


class SeoSignalSearchVolume:
    """SearchVolumeProvider over keyword-research-bulk."""

    def __init__(self, client: SeoSignalClient, *, clock=lambda: datetime.now(timezone.utc)) -> None:
        self._client = client
        self._clock = clock

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def max_keywords_per_request(self) -> int:
        return MAX_BULK_KEYWORDS

    def check_key(self) -> None:
        """Prove the key is accepted, without asking a real question.

        An empty keyword list is refused as INVALID_PARAMS only after the key
        has been checked, so that answer means the key and plan are fine. A
        refused key or plan raises as usual.
        """
        try:
            self._client.call("keyword-research-bulk", {"keywords": []})
        except (ProviderAuthError, ProviderPlanError, ProviderUnavailableError):
            raise
        except KeywordProviderError as exc:
            if "INVALID_PARAMS" in str(exc):
                return
            raise

    def search_volumes(self, keywords: Sequence[str]) -> list[KeywordVolume]:
        asked = list(keywords)
        if not asked:
            return []
        if len(asked) > MAX_BULK_KEYWORDS:
            raise ValueError(f"At most {MAX_BULK_KEYWORDS} keywords per request.")
        # Sent in our normalised (Persian-letter) form, once each: demand is
        # recorded against what people type, and "كفش" with an Arabic kaf
        # would come back empty while "کفش" has thousands of searches.
        sent: list[str] = []
        for keyword in asked:
            normalized = normalize_query(keyword)
            if normalized and normalized not in sent:
                sent.append(normalized)
        # A whole request answered "nothing found" is less certain than one
        # keyword missing from a list that has others: the answers below are
        # marked unconfirmed, and the cache keeps them for a day, not a month.
        confirmed = True
        try:
            data = self._client.call("keyword-research-bulk", {"keywords": sent})
        except NoDataError:
            data = {"keywordList": []}
            confirmed = False
        rows = data.get("keywordList") if isinstance(data, dict) else None
        found: dict[str, dict] = {}
        for row in rows or []:
            if isinstance(row, dict) and isinstance(row.get("Word"), str):
                found.setdefault(normalize_query(row["Word"]), row)
        now = self._clock()
        volumes = []
        for keyword in asked:
            row = found.get(normalize_query(keyword))
            volumes.append(
                KeywordVolume(
                    keyword=keyword,
                    search_volume=_int_or_none(row.get("SearchVolume")) if row else None,
                    competition=_competition(row.get("Competition")) if row else Competition.UNKNOWN,
                    provider_id=PROVIDER_ID,
                    fetched_at=now,
                    confirmed=confirmed,
                )
            )
        return volumes
