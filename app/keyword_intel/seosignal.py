"""SEO Signal adapters. The only code that knows SEO Signal's JSON.

API: https://panel.seosignal.net/openapi/ — every endpoint is a POST with a
JSON body, authenticated by an ``X-Api-Key`` header, answering
``{"ok": true, "data": ...}`` or ``{"error": {"code", "message"}}``.

What it costs, which shapes how it is used:

* Keyword research shares a daily cap (50 requests by default) with the
  site-analysis endpoints. The bulk endpoint answers up to 1000 keywords for
  one request, so volume is always asked in bulk, never per keyword.
* The rank tracker is the account's own data and has no daily cap.

Swapping provider means writing another module like this one, against the
ports in contracts.py; nothing outside this file changes.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from typing import Any

from app.keyword_intel.contracts import (
    KeywordProviderError,
    ProviderAuthError,
    ProviderPlanError,
    ProviderQuotaError,
    ProviderUnavailableError,
)
from app.models.keyword_intel import (
    Competition,
    Device,
    KeywordRankHistory,
    KeywordVolume,
    RankHistory,
    RankPoint,
    RankProject,
    RankProjectSummary,
)
from app.normalization.query import normalize_query


PROVIDER_ID = "seosignal"
BASE_URL = "https://panel.seosignal.net/openapi/v1"
MAX_BULK_KEYWORDS = 1000
MAX_RANK_RANGE_DAYS = 90

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

# (status, parsed body) for a POST of ``body`` to ``url`` with ``headers``.
Transport = Callable[[str, dict[str, str], dict, float], tuple[int, Any]]


def default_transport(url: str, headers: dict[str, str], body: dict, timeout: float):
    import requests

    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderUnavailableError(f"SEO Signal could not be reached: {type(exc).__name__}") from exc
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


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
        status, payload = self._transport(
            f"{self._base_url}/{endpoint}",
            {"X-Api-Key": self._api_key, "Content-Type": "application/json"},
            body,
            self._timeout,
        )
        if status == 200 and isinstance(payload, dict) and payload.get("ok") is True:
            return payload.get("data")
        raise _error(status, payload)


class NoDataError(KeywordProviderError):
    """REQUEST_FAILED: a valid request the provider has no data for."""


def _error(status: int, payload: Any) -> KeywordProviderError:
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    message = f"SEO Signal: {code or f'HTTP {status}'}"
    if code in {"INVALID_API_KEY", "ACCOUNT_DISABLED"} or status == 401:
        return ProviderAuthError(message)
    if code == "PLAN_NOT_ALLOWED":
        return ProviderPlanError(message)
    if code == "RATE_LIMIT_EXCEEDED" or status == 429:
        return ProviderQuotaError(message)
    if code == "REQUEST_FAILED":
        return NoDataError(message)
    if status >= 500 or status == 0:
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
        try:
            data = self._client.call("keyword-research-bulk", {"keywords": sent})
        except NoDataError:
            # None of them has data: an answer, and worth caching as one.
            data = {"keywordList": []}
        rows = data.get("keywordList") if isinstance(data, dict) else None
        found: dict[str, dict] = {}
        for row in rows or []:
            if isinstance(row, dict) and isinstance(row.get("Word"), str):
                # Matched on our normalisation, so "كفش" asked and "کفش"
                # answered (Arabic and Persian kaf) are the same keyword.
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
                )
            )
        return volumes


class SeoSignalRankTracker:
    """RankTrackerProvider over the ranktracker-* endpoints (no daily cap)."""

    def __init__(self, client: SeoSignalClient, *, clock=lambda: datetime.now(timezone.utc)) -> None:
        self._client = client
        self._clock = clock

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    def projects(self) -> list[RankProjectSummary]:
        data = self._client.call("ranktracker-projects", {})
        items = data.get("List") if isinstance(data, dict) else None
        return [
            RankProjectSummary(
                project_id=str(item["Id"]),
                name=str(item.get("Name") or ""),
                domain=str(item.get("MainDomain") or ""),
                active=item.get("IsActive") is not False,
            )
            for item in items or []
            if isinstance(item, dict) and item.get("Id") is not None
        ]

    def project(self, project_id: str) -> RankProject:
        data = self._detail(project_id)
        devices = [
            device
            for device, key in ((Device.MOBILE, "mobile_id"), (Device.DESKTOP, "desktop_id"))
            if data.get(key) is not None
        ]
        return RankProject(
            project_id=str(project_id),
            name=str(data.get("name") or ""),
            domain=str(data.get("main_domain") or ""),
            keywords=[str(k) for k in data.get("keywords") or [] if isinstance(k, str)],
            competitor_domains=[str(d) for d in data.get("competitor_domains") or [] if isinstance(d, str)],
            location=data.get("location") if isinstance(data.get("location"), str) else None,
            devices=devices,
        )

    def rank_history(
        self,
        *,
        project_id: str,
        device: Device,
        start: date,
        end: date,
        domain: str | None = None,
    ) -> RankHistory:
        if end < start:
            raise ValueError("end must not be before start.")
        if (end - start).days + 1 > MAX_RANK_RANGE_DAYS:
            raise ValueError(f"At most {MAX_RANK_RANGE_DAYS} days per request.")
        detail = self._detail(project_id)
        device_id = detail.get("mobile_id" if device == Device.MOBILE else "desktop_id")
        if device_id is None:
            raise KeywordProviderError(f"This project does not track {device.value}.")
        body: dict[str, Any] = {
            "project_id": _project_number(project_id),
            "device_id": device_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        if domain:
            body["domain"] = domain
        data = self._client.call("ranktracker-rank-history", body)
        if not isinstance(data, dict):
            raise KeywordProviderError("SEO Signal returned no rank history.")
        keywords = []
        for item in data.get("keywords") or []:
            if not isinstance(item, dict) or not isinstance(item.get("keyword"), str):
                continue
            points = []
            for point in item.get("history") or []:
                try:
                    day = date.fromisoformat(str(point.get("date")))
                except (TypeError, ValueError, AttributeError):
                    continue
                rank = _int_or_none(point.get("rank"))
                points.append(RankPoint(date=day, rank=rank if rank else None))
            keywords.append(
                KeywordRankHistory(
                    keyword=item["keyword"],
                    search_volume=_int_or_none(item.get("search_volume")),
                    target_url=item.get("target_url") if isinstance(item.get("target_url"), str) else None,
                    points=sorted(points, key=lambda p: p.date),
                )
            )
        return RankHistory(
            project_id=str(project_id),
            domain=str(data.get("domain") or domain or detail.get("main_domain") or ""),
            device=device,
            start=start,
            end=end,
            keywords=keywords,
            provider_id=PROVIDER_ID,
            fetched_at=self._clock(),
        )

    def _detail(self, project_id: str) -> dict:
        data = self._client.call(
            "ranktracker-project-detail", {"project_id": _project_number(project_id)}
        )
        if not isinstance(data, dict):
            raise KeywordProviderError("SEO Signal returned no project detail.")
        return data


def _project_number(project_id: str) -> int:
    try:
        return int(project_id)
    except (TypeError, ValueError):
        raise ValueError("SEO Signal project ids are numbers.") from None
