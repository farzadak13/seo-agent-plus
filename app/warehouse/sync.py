"""Sync one day of one site: fetch it, store it, or record why not."""
from __future__ import annotations

from datetime import date

from app.warehouse.fetch import DayNotFinalError, QueryFn, fetch_day
from app.warehouse.models import DaySyncResult


class DaySync:
    def __init__(self, store) -> None:
        self._store = store

    def sync_day(
        self, *, site_id: str, property_url: str, day: date, final_through: date, query: QueryFn
    ) -> DaySyncResult:
        try:
            data = fetch_day(
                site_id=site_id, property_url=property_url, day=day,
                final_through=final_through, query=query,
            )
        except DayNotFinalError:
            # Not a failure, only "not yet": nothing is recorded, so the day
            # stays unsynced and is asked for again once it has settled.
            raise
        except Exception as exc:
            self._store.record_failure(
                site_id=site_id, day=day, property_url=property_url,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._store.replace_day(data)
        return DaySyncResult(
            site_id=site_id,
            day=day,
            totals_rows=len(data.totals),
            page_rows=len(data.pages),
            query_rows=len(data.queries),
            page_rows_capped=data.page_rows_capped,
            query_rows_capped=data.query_rows_capped,
        )
