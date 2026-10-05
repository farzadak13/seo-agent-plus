"""Fetch one settled day of Search Console data for one site.

Three requests, all for the single day and split by device:

* date x device              -> site totals (no query, so nothing anonymised)
* date x page x device       -> per page
* date x page x query x device -> per page and query

URLs go through the shared URL canonicaliser and queries through the shared
query normaliser, as everywhere else in the system. Two raw values can become
one after that ("كفش" and "کفش"), so rows are merged by summing, never by
keeping one and dropping the other.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from app.normalization.query import normalize_query
from app.normalization.url import canonicalize_url
from app.warehouse.models import (
    DAILY_ROW_CAP,
    Device,
    GSCDay,
    Metrics,
    PageMetrics,
    QueryMetrics,
)


TOTALS_DIMENSIONS = ("date", "device")
PAGE_DIMENSIONS = ("date", "page", "device")
QUERY_DIMENSIONS = ("date", "page", "query", "device")

# (dimensions, day) -> Search Console rows: {"keys": [...], "clicks", "impressions", "position"}
QueryFn = Callable[[tuple[str, ...], date], list[dict[str, Any]]]


class FetchError(ValueError):
    """Search Console answered something that cannot be stored as it is."""


def fetch_day(*, site_id: str, property_url: str, day: date, query: QueryFn) -> GSCDay:
    totals_rows = query(TOTALS_DIMENSIONS, day)
    page_rows = query(PAGE_DIMENSIONS, day)
    query_rows = query(QUERY_DIMENSIONS, day)

    totals: dict[tuple, list[float]] = {}
    for keys, metrics in _rows(totals_rows, TOTALS_DIMENSIONS, day):
        _add(totals, (_device(keys["device"]),), metrics)

    pages: dict[tuple, list[float]] = {}
    for keys, metrics in _rows(page_rows, PAGE_DIMENSIONS, day):
        url = _url(keys["page"])
        if url:
            _add(pages, (_device(keys["device"]), url), metrics)

    queries: dict[tuple, list[float]] = {}
    for keys, metrics in _rows(query_rows, QUERY_DIMENSIONS, day):
        url = _url(keys["page"])
        text = normalize_query(keys["query"])
        if url and text:
            _add(queries, (_device(keys["device"]), url, text), metrics)

    return GSCDay(
        site_id=site_id,
        day=day,
        property_url=property_url,
        totals=[
            Metrics(device=device, clicks=int(c), impressions=int(i), position_sum=p)
            for (device,), (c, i, p) in sorted(totals.items())
        ],
        pages=[
            PageMetrics(device=device, url=url, clicks=int(c), impressions=int(i), position_sum=p)
            for (device, url), (c, i, p) in sorted(pages.items())
        ],
        queries=[
            QueryMetrics(
                device=device, url=url, query=text, clicks=int(c), impressions=int(i), position_sum=p
            )
            for (device, url, text), (c, i, p) in sorted(queries.items())
        ],
        query_rows_capped=len(query_rows) >= DAILY_ROW_CAP,
    )


def _rows(rows: Iterable[dict[str, Any]], dimensions: tuple[str, ...], day: date):
    # Within one answer Search Console's keys are unique, so the same raw keys
    # twice can only be a page sent again (an overlap in pagination). That is
    # one row, not two: an identical repeat is dropped and a conflicting one
    # refused, as the shared normaliser does (app.normalization.gsc). Summing
    # is only for rows that are different raw keys and become one after
    # normalisation, below in fetch_day.
    seen: dict[tuple, tuple] = {}
    for row in rows:
        keys = row.get("keys")
        if not isinstance(keys, list) or len(keys) != len(dimensions):
            raise FetchError("A Search Console row does not match the requested dimensions.")
        named = dict(zip(dimensions, keys))
        if named["date"] != day.isoformat():
            raise FetchError(f"A Search Console row is for {named['date']}, not {day.isoformat()}.")
        clicks = _count(row.get("clicks"))
        impressions = _count(row.get("impressions"))
        position = row.get("position")
        if isinstance(position, bool) or not isinstance(position, (int, float)) or position < 0:
            raise FetchError("A Search Console row has an invalid position.")
        raw_key = tuple(keys)
        values = (clicks, impressions, position)
        if raw_key in seen:
            if seen[raw_key] != values:
                raise FetchError("Search Console sent the same row twice with different numbers.")
            continue
        seen[raw_key] = values
        yield named, (clicks, impressions, float(position) * impressions)


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or int(value) != value:
        raise FetchError("A Search Console row has an invalid count.")
    return int(value)


def _device(value: Any) -> Device:
    try:
        return Device(str(value).upper())
    except ValueError:
        raise FetchError(f"Unknown device from Search Console: {value!r}") from None


def _url(value: Any) -> str:
    try:
        return canonicalize_url(str(value))
    except ValueError:
        return ""  # not a page URL we can address; Search Console rarely sends these


def _add(target: dict[tuple, list[float]], key: tuple, metrics: tuple[int, int, float]) -> None:
    current = target.setdefault(key, [0, 0, 0.0])
    current[0] += metrics[0]
    current[1] += metrics[1]
    current[2] += metrics[2]
