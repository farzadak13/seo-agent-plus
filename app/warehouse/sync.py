"""Sync one day of one site: fetch it, store it, or record why not."""
from __future__ import annotations

from datetime import date

from app.warehouse.fetch import QueryFn, fetch_day


class DaySync:
    def __init__(self, store) -> None:
        self._store = store

    def sync_day(self, *, site_id: str, property_url: str, day: date, query: QueryFn) -> dict:
        try:
            data = fetch_day(site_id=site_id, property_url=property_url, day=day, query=query)
        except Exception as exc:
            self._store.record_failure(
                site_id=site_id, day=day, property_url=property_url,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._store.replace_day(data)
        return {
            "site_id": site_id,
            "day": day.isoformat(),
            "totals_rows": len(data.totals),
            "page_rows": len(data.pages),
            "query_rows": len(data.queries),
            "query_rows_capped": data.query_rows_capped,
        }
