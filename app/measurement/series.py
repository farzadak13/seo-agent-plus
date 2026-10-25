"""Daily observations of one page and query, built from the warehouse.

Pure: the caller reads the rows, this says what each day means. A day is

* OBSERVED when the query has a row that day;
* MISSING_EXPECTED_ZERO when the day is stored but the page itself had no
  impressions at all. Page totals include the queries Search Console hides
  for privacy, so a page with none had none on any query;
* MISSING_UNKNOWN otherwise: the day is not stored, the page's rows reached
  the daily cap, or the page was seen while this query was not. The last is
  the anonymised-query case, and calling it zero would invent a drop.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from app.models.observations import DailyObservation, DataStatus


def window_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def build_observations(
    *,
    site_id: str,
    normalized_url: str,
    normalized_query: str,
    start: date,
    end: date,
    stored_days: Iterable[date],
    capped_days: Iterable[date],
    query_rows: Mapping[date, tuple[int, int, float]],
    page_days: Iterable[date],
) -> list[DailyObservation]:
    stored = set(stored_days)
    capped = set(capped_days)
    seen = set(page_days)
    observations = []
    for day in window_days(start, end):
        row = query_rows.get(day)
        if day in stored and row is not None and row[1] > 0:
            clicks, impressions, position_sum = row
            observations.append(
                DailyObservation(
                    site_id=site_id,
                    normalized_url=normalized_url,
                    normalized_query=normalized_query,
                    date=day,
                    impressions=impressions,
                    clicks=clicks,
                    avg_position=position_sum / impressions,
                    data_status=DataStatus.OBSERVED,
                )
            )
            continue
        if day in stored and day not in capped and day not in seen:
            status = DataStatus.MISSING_EXPECTED_ZERO
        else:
            status = DataStatus.MISSING_UNKNOWN
        observations.append(
            DailyObservation(
                site_id=site_id,
                normalized_url=normalized_url,
                normalized_query=normalized_query,
                date=day,
                data_status=status,
            )
        )
    return observations
