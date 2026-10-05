"""Which days of which site to fetch next. Pure: no I/O, no clock of its own.

The window is the newest settled day back to about 16 months, which is what
Search Console keeps. A little less than 16 months on purpose: a request for
a day Google no longer holds answers with no rows, which would be stored as a
day of zeros.

The settled day is computed in Search Console's own calendar (Pacific time)
and then held back by the usual lag. Pacific is UTC-8 in winter and UTC-7 in
summer; taking UTC-8 always gives the same day or the one before, never a
later one, so it errs towards waiting, and needs no time-zone database.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.ingestion.calendar import GSC_DATA_LAG_DAYS, latest_final_date


WINDOW_DAYS = 480  # ~15.8 months, inside Search Console's 16
PACIFIC_STANDARD = timezone(timedelta(hours=-8))


@dataclass(frozen=True)
class LedgerEntry:
    property_url: str
    status: str  # "synced" | "failed"
    fetched_at: datetime


def search_console_today(now: datetime) -> date:
    return now.astimezone(PACIFIC_STANDARD).date()


def sync_window(
    now: datetime, *, lag_days: int = GSC_DATA_LAG_DAYS, window_days: int = WINDOW_DAYS
) -> tuple[date, date]:
    """(oldest, newest) day the warehouse should hold, both inclusive."""
    newest = latest_final_date(search_console_today(now), lag_days=lag_days)
    return newest - timedelta(days=window_days - 1), newest


def plan_days(
    *,
    window: tuple[date, date],
    property_url: str,
    entries: dict[date, LedgerEntry],
    now: datetime,
    retry_after: timedelta,
    limit: int,
) -> list[date]:
    """The next days to fetch, newest first, at most ``limit``.

    Newest first so a newly connected site has something to show within the
    first minutes. A day already stored from this property is done; one
    stored from a different property (the site switched) is fetched again. A
    failed day waits ``retry_after`` before it is tried again, so one broken
    day cannot use up every round.
    """
    oldest, newest = window
    due: list[date] = []
    day = newest
    while day >= oldest and len(due) < limit:
        entry = entries.get(day)
        if entry is None or entry.property_url != property_url:
            due.append(day)
        elif entry.status == "failed" and now - entry.fetched_at >= retry_after:
            due.append(day)
        day -= timedelta(days=1)
    return due


FAILURE_MESSAGE = "Search Console did not answer for this day. It will be tried again automatically."
FAILING_STREAK = 3  # the newest settled days all failing means something is wrong, not one bad day


def summarize(*, site_id: str, property_url: str, window: tuple[date, date], entries) -> "SyncSummary":
    """What the dashboard shows about a site's sync, from its ledger entries."""
    from app.warehouse.models import SyncFailure, SyncState, SyncSummary

    oldest, newest = window
    days_in_window = (newest - oldest).days + 1
    current = [e for e in entries if e.property_url == property_url and oldest <= e.day <= newest]
    synced = sorted(e.day for e in current if e.status == "synced")
    failed = sorted((e for e in current if e.status == "failed"), key=lambda e: e.day, reverse=True)
    capped = sorted(
        e.day for e in current if e.status == "synced" and (e.page_rows_capped or e.query_rows_capped)
    )

    newest_days = {newest - timedelta(days=n) for n in range(FAILING_STREAK)}
    failed_days = {e.day for e in failed}
    if newest_days <= failed_days:
        state = SyncState.FAILING
    elif len(synced) == days_in_window:
        state = SyncState.UP_TO_DATE
    else:
        state = SyncState.BACKFILLING

    return SyncSummary(
        site_id=site_id,
        state=state,
        property_url=property_url,
        window_start=oldest,
        window_end=newest,
        days_in_window=days_in_window,
        synced_days=len(synced),
        failed_days=len(failed),
        progress=round(len(synced) / days_in_window, 4),
        newest_synced_day=synced[-1] if synced else None,
        oldest_synced_day=synced[0] if synced else None,
        capped_days=capped,
        recent_failures=[SyncFailure(day=e.day, message=FAILURE_MESSAGE) for e in failed[:10]],
    )
