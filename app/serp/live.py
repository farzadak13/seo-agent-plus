"""Live Google results from a SERP API: Serper and DataForSEO.

Google has no public API for its results (the Custom Search JSON API is
closed to new customers and ends on 2027-01-01), so the snapshot the title
path compares against comes from a commercial SERP API. Two are supported
behind the same port, chosen by SEO_AGENT_SERP_MODE:

* ``serper`` — google.serper.dev. Synchronous, cheap, 2,500 free queries to
  start; prepaid credits expire after six months.
* ``dataforseo`` — api.dataforseo.com, the "live regular" endpoint. Credits
  do not expire, which suits a service that buys them through an intermediary
  once in a while rather than by card every month.

Both ask Google as a searcher in Iran would (country ``ir``, language ``fa``
by default). These modules are the only code that knows each provider's JSON;
everything after them sees a ``SERPQuerySnapshot``.
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.serp import SERPQuerySnapshot, SERPResultItem
from app.normalization.url import canonicalize_url
from app.serp.provider import SERPProviderError


RESULTS = 10

# Google Ads geo-target ids, which DataForSEO uses as location codes.
DATAFORSEO_LOCATIONS = {"ir": 2364}
# DataForSEO's code for "rate limit per minute exceeded": wait, not out of money.
DATAFORSEO_RATE_LIMITED = 40202


class SERPAuthError(SERPProviderError):
    """The key was refused."""


class SERPQuotaError(SERPProviderError):
    """Out of credits at the provider: nothing more today."""


class SERPRateLimitedError(SERPProviderError):
    """Too many requests just now; the next minute may well succeed."""


class SERPUnavailableError(SERPProviderError):
    """The provider could not be reached or failed on its side."""


@dataclass(frozen=True)
class HTTPAnswer:
    status: int
    text: str


# (url, headers, json body, timeout) -> answer. Every status comes back.
Transport = Callable[[str, dict[str, str], Any, float], HTTPAnswer]


def default_transport(url: str, headers: dict[str, str], body: Any, timeout: float) -> HTTPAnswer:
    # Direct, not through SEO_AGENT_GOOGLE_PROXY_URL: these are not Google's
    # hosts, and both answer from the production server as it is.
    import requests

    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise SERPUnavailableError(f"SERP provider unreachable: {type(exc).__name__}") from exc
    return HTTPAnswer(response.status_code, response.content.decode("utf-8", errors="replace"))


def _json(answer: HTTPAnswer) -> Any:
    try:
        return json.loads(answer.text) if answer.text.strip() else None
    except json.JSONDecodeError:
        return None


def _raise_for_status(answer: HTTPAnswer, provider: str) -> None:
    if 200 <= answer.status < 300:
        return
    if answer.status in (401, 403):
        raise SERPAuthError(f"{provider} refused the API key (HTTP {answer.status}).")
    if answer.status == 402:
        raise SERPQuotaError(f"{provider} is out of credits (HTTP 402).")
    if answer.status == 429:
        raise SERPRateLimitedError(f"{provider} is rate-limiting (HTTP 429).")
    raise SERPUnavailableError(f"{provider} answered HTTP {answer.status}.")


def _result_url(value: Any) -> str | None:
    """Canonical, as every URL in the system is: the investigation finds the
    customer's own page by comparing with its canonical URL, so an ``http``,
    a capital letter or a ``utm_`` parameter would make it a competitor."""
    try:
        return canonicalize_url(str(value))
    except ValueError:
        return None


def _snapshot(*, provider: str, query: str, payload: Any, items: list[SERPResultItem], clock) -> SERPQuerySnapshot:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:24]
    return SERPQuerySnapshot(
        query=query,
        provider=provider,
        response_id=f"{provider}:{digest}",
        fetched_at=clock(),
        results=items,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SerperSERPProvider:
    URL = "https://google.serper.dev/search"

    def __init__(
        self,
        *,
        api_key: str,
        country: str = "ir",
        language: str = "fa",
        timeout_seconds: float = 30.0,
        transport: Transport = default_transport,
        clock=_utcnow,
    ) -> None:
        if not api_key.strip():
            raise ValueError("A Serper API key is required.")
        self._api_key = api_key
        self._country = country
        self._language = language
        self._timeout = timeout_seconds
        self._transport = transport
        self._clock = clock

    def __repr__(self) -> str:  # never the key
        return f"SerperSERPProvider(country={self._country!r}, language={self._language!r})"

    @property
    def provider_id(self) -> str:
        return "serper"

    def search(self, *, query: str) -> SERPQuerySnapshot:
        answer = self._transport(
            self.URL,
            {"X-API-KEY": self._api_key, "Content-Type": "application/json"},
            {"q": query, "gl": self._country, "hl": self._language, "num": RESULTS},
            self._timeout,
        )
        _raise_for_status(answer, "Serper")
        payload = _json(answer)
        if not isinstance(payload, dict):
            raise SERPUnavailableError("Serper did not return a JSON object.")
        items = []
        for row in payload.get("organic") or []:
            if not isinstance(row, dict) or not row.get("link") or not row.get("title"):
                continue
            url = _result_url(row["link"])
            if url is None:
                continue
            position = row.get("position")
            items.append(
                SERPResultItem(
                    position=int(position) if isinstance(position, int) and position >= 1 else len(items) + 1,
                    url=url,
                    title=str(row["title"]),
                    snippet=str(row["snippet"]) if row.get("snippet") else None,
                )
            )
        return _snapshot(provider=self.provider_id, query=query, payload=payload.get("organic"),
                         items=items[:RESULTS], clock=self._clock)


class DataForSEOSERPProvider:
    URL = "https://api.dataforseo.com/v3/serp/google/organic/live/regular"

    def __init__(
        self,
        *,
        credentials: str,
        country: str = "ir",
        language: str = "fa",
        timeout_seconds: float = 60.0,
        transport: Transport = default_transport,
        clock=_utcnow,
    ) -> None:
        login, sep, password = credentials.partition(":")
        if not sep or not login.strip() or not password.strip():
            raise ValueError("DataForSEO credentials must be 'login:password'.")
        if country not in DATAFORSEO_LOCATIONS:
            raise ValueError(f"No DataForSEO location code known for country {country!r}.")
        self._authorization = "Basic " + base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        self._location = DATAFORSEO_LOCATIONS[country]
        self._language = language
        self._timeout = timeout_seconds
        self._transport = transport
        self._clock = clock

    def __repr__(self) -> str:  # never the credentials
        return f"DataForSEOSERPProvider(location={self._location}, language={self._language!r})"

    @property
    def provider_id(self) -> str:
        return "dataforseo"

    def search(self, *, query: str) -> SERPQuerySnapshot:
        answer = self._transport(
            self.URL,
            {"Authorization": self._authorization, "Content-Type": "application/json"},
            [{"keyword": query, "location_code": self._location, "language_code": self._language,
              "depth": RESULTS}],
            self._timeout,
        )
        _raise_for_status(answer, "DataForSEO")
        payload = _json(answer)
        if not isinstance(payload, dict):
            raise SERPUnavailableError("DataForSEO did not return a JSON object.")
        # The HTTP status can be 200 for a failed request; DataForSEO's own
        # five-digit codes say what happened, and their first three digits
        # follow HTTP (401xx authorisation, 402xx payment).
        tasks = payload.get("tasks") or []
        task = tasks[0] if tasks and isinstance(tasks[0], dict) else {}
        for level in (payload, task):
            if level.get("status_code") == DATAFORSEO_RATE_LIMITED:
                raise SERPRateLimitedError(f"DataForSEO: {level.get('status_message')}")
            code = str(level.get("status_code", ""))
            if code.startswith("401"):
                raise SERPAuthError(f"DataForSEO refused the credentials: {level.get('status_message')}")
            if code.startswith("402"):
                raise SERPQuotaError(f"DataForSEO: {level.get('status_message')}")
        if task.get("status_code") != 20000:
            raise SERPUnavailableError(
                f"DataForSEO task failed: {task.get('status_code')} {task.get('status_message')}"
            )
        results = task.get("result") or []
        first = results[0] if results and isinstance(results[0], dict) else {}
        rows = first.get("items") or []
        items = []
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "organic":
                continue
            if not row.get("url") or not row.get("title"):
                continue
            url = _result_url(row["url"])
            if url is None:
                continue
            rank = row.get("rank_group")
            items.append(
                SERPResultItem(
                    position=int(rank) if isinstance(rank, int) and rank >= 1 else len(items) + 1,
                    url=url,
                    title=str(row["title"]),
                    snippet=str(row["description"]) if row.get("description") else None,
                )
            )
        organic = [row for row in rows if isinstance(row, dict) and row.get("type") == "organic"]
        return _snapshot(provider=self.provider_id, query=query, payload=organic,
                         items=items[:RESULTS], clock=self._clock)
