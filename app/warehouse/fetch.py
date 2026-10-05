"""Fetch one settled day of Search Console data for one site.

Three requests, all for the single day and split by device:

* date x device              -> site totals (no query, so nothing anonymised)
* date x page x device       -> per page
* date x page x query x device -> per page and query

Only a final day is ever returned. With ``dataState=final`` Search Console
answers a day that is not final yet with no rows, which looks exactly like a
day without traffic: stored, it would be marked synced with zeros and never
fetched again. So two independent checks come first, and a day that fails
either raises DayNotFinalError, which is not a failure, only "not yet":

* the caller states the newest day it considers settled (``final_through``),
  and anything later is refused before a request is made;
* the totals request asks with ``dataState=all``, the only mode in which
  Search Console reports its first incomplete date; a day on or after it is
  refused. For a day before it, "all" and "final" are the same numbers.

URLs go through the shared URL canonicaliser and queries through the shared
query normaliser, as everywhere else in the system. Two raw values can become
one after that ("كفش" and "کفش"), so rows are merged by summing, never by
keeping one and dropping the other. Rows are validated to the same rules as
the shared GSC normaliser (app.normalization.gsc).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from math import isfinite
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


@dataclass(frozen=True)
class QueryAnswer:
    """Search Console's rows for one request, and its first incomplete date if it gave one."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    first_incomplete_date: date | None = None


# (dimensions, day, data_state) -> the answer. data_state is "final" or "all".
QueryFn = Callable[[tuple[str, ...], date, str], QueryAnswer]


class FetchError(ValueError):
    """Search Console answered something that cannot be stored as it is."""


class DayNotFinalError(RuntimeError):
    """The day's data is not final yet. Not a failure: try again later."""


def fetch_day(
    *, site_id: str, property_url: str, day: date, final_through: date, query: QueryFn
) -> GSCDay:
    if day > final_through:
        raise DayNotFinalError(f"{day.isoformat()} is after the newest settled day, {final_through.isoformat()}.")

    totals_answer = query(TOTALS_DIMENSIONS, day, "all")
    incomplete = totals_answer.first_incomplete_date
    if incomplete is not None and day >= incomplete:
        raise DayNotFinalError(
            f"Search Console is still collecting {day.isoformat()} (incomplete from {incomplete.isoformat()})."
        )
    page_rows = query(PAGE_DIMENSIONS, day, "final").rows
    query_rows = query(QUERY_DIMENSIONS, day, "final").rows

    totals: dict[tuple, list[float]] = {}
    for keys, metrics in _rows(totals_answer.rows, TOTALS_DIMENSIONS, day):
        _add(totals, (_device(keys["device"]),), metrics)

    pages: dict[tuple, list[float]] = {}
    page_raw_count = 0
    for keys, metrics in _rows(page_rows, PAGE_DIMENSIONS, day):
        page_raw_count += 1
        url = _url(keys["page"])
        if url:
            _add(pages, (_device(keys["device"]), url), metrics)

    queries: dict[tuple, list[float]] = {}
    query_raw_count = 0
    for keys, metrics in _rows(query_rows, QUERY_DIMENSIONS, day):
        query_raw_count += 1
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
        # Counted on distinct rows, after dropping repeats from overlapping pages.
        page_rows_capped=page_raw_count >= DAILY_ROW_CAP,
        query_rows_capped=query_raw_count >= DAILY_ROW_CAP,
    )


def _rows(rows: Iterable[dict[str, Any]], dimensions: tuple[str, ...], day: date):
    # Within one answer Search Console's keys are unique, so the same raw keys
    # twice can only be a page sent again (an overlap in pagination). That is
    # one row, not two: an identical repeat is dropped and a conflicting one
    # refused, as the shared normaliser does (app.normalization.gsc). Summing
    # is only for rows that are different raw keys and become one after
    # normalisation, in fetch_day.
    seen: dict[tuple, tuple] = {}
    for row in rows:
        keys = row.get("keys")
        if (
            not isinstance(keys, list)
            or len(keys) != len(dimensions)
            or not all(isinstance(key, str) for key in keys)
        ):
            raise FetchError("A Search Console row does not match the requested dimensions.")
        named = dict(zip(dimensions, keys))
        if named["date"] != day.isoformat():
            raise FetchError(f"A Search Console row is for {named['date']}, not {day.isoformat()}.")
        clicks = _count(row.get("clicks"))
        impressions = _count(row.get("impressions"))
        if clicks > impressions:
            raise FetchError("A Search Console row has more clicks than impressions.")
        position = row.get("position")
        if (
            isinstance(position, bool)
            or not isinstance(position, (int, float))
            or not isfinite(position)
            or position < 0
        ):
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
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
        or int(value) != value
    ):
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
