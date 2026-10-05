"""The Search Console warehouse in PostgreSQL (migration 004).

A day is replaced whole, in one transaction: its old rows are deleted and the
new ones inserted, and the sync ledger records it. Fetching a day again
therefore replaces it and never doubles it, and a failure part-way leaves the
previous version of the day exactly as it was.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timezone

from app.warehouse.models import GSCDay, SyncDayStatus


class PostgresWarehouse:
    def __init__(self, dsn: str, *, connection_factory: Callable | None = None, clock=None) -> None:
        import psycopg

        self._dsn = dsn
        self._connect = connection_factory or psycopg.connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def _transaction(self):
        with self._connect(self._dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    yield cursor

    # --- writing ------------------------------------------------------------

    def replace_day(self, data: GSCDay) -> None:
        with self._transaction() as cursor:
            # Two syncs of the same day (a retry overlapping a slow first try)
            # take turns rather than colliding on the primary keys.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"gsc-day:{data.site_id}:{data.day.isoformat()}",),
            )
            page_ids = self._ids(
                cursor, "gsc_pages", "page_id", "url", data.site_id,
                {row.url for row in data.pages} | {row.url for row in data.queries},
            )
            query_ids = self._ids(
                cursor, "gsc_queries", "query_id", "query", data.site_id,
                {row.query for row in data.queries},
            )
            for table in ("gsc_daily_queries", "gsc_daily_pages", "gsc_daily_totals"):
                cursor.execute(
                    f"DELETE FROM {table} WHERE site_id = %s AND day = %s",  # noqa: S608 - fixed names
                    (data.site_id, data.day),
                )
            cursor.executemany(
                "INSERT INTO gsc_daily_totals (site_id, day, device, clicks, impressions, position_sum)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (data.site_id, data.day, row.device.value, row.clicks, row.impressions, row.position_sum)
                    for row in data.totals
                ],
            )
            cursor.executemany(
                "INSERT INTO gsc_daily_pages"
                " (site_id, day, page_id, device, clicks, impressions, position_sum)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (data.site_id, data.day, page_ids[row.url], row.device.value,
                     row.clicks, row.impressions, row.position_sum)
                    for row in data.pages
                ],
            )
            cursor.executemany(
                "INSERT INTO gsc_daily_queries"
                " (site_id, day, page_id, query_id, device, clicks, impressions, position_sum)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (data.site_id, data.day, page_ids[row.url], query_ids[row.query], row.device.value,
                     row.clicks, row.impressions, row.position_sum)
                    for row in data.queries
                ],
            )
            cursor.execute(
                """
                INSERT INTO gsc_sync_days (
                    site_id, day, property_url, status, totals_rows, page_rows, query_rows,
                    page_rows_capped, query_rows_capped, fetched_at, error
                ) VALUES (%s, %s, %s, 'synced', %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (site_id, day) DO UPDATE SET
                    property_url = EXCLUDED.property_url,
                    status = 'synced',
                    totals_rows = EXCLUDED.totals_rows,
                    page_rows = EXCLUDED.page_rows,
                    query_rows = EXCLUDED.query_rows,
                    page_rows_capped = EXCLUDED.page_rows_capped,
                    query_rows_capped = EXCLUDED.query_rows_capped,
                    fetched_at = EXCLUDED.fetched_at,
                    error = NULL
                """,
                (data.site_id, data.day, data.property_url, len(data.totals), len(data.pages),
                 len(data.queries), data.page_rows_capped, data.query_rows_capped, self._clock()),
            )

    def record_failure(self, *, site_id: str, day: date, property_url: str, error: str) -> None:
        """Note a failed fetch, without disturbing a day already stored.

        A good earlier copy of the day stays as it is and stays 'synced': a
        failed refetch is not a reason to show a gap.
        """
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO gsc_sync_days (site_id, day, property_url, status, fetched_at, error)
                VALUES (%s, %s, %s, 'failed', %s, %s)
                ON CONFLICT (site_id, day) DO UPDATE SET
                    property_url = EXCLUDED.property_url,
                    fetched_at = EXCLUDED.fetched_at,
                    error = EXCLUDED.error
                WHERE gsc_sync_days.status = 'failed'
                """,
                (site_id, day, property_url, self._clock(), error[:500]),
            )

    @staticmethod
    def _ids(cursor, table: str, id_column: str, value_column: str, site_id: str, values: set[str]) -> dict[str, int]:
        """Ids for these values, creating the ones not seen before."""
        if not values:
            return {}
        ordered = sorted(values)
        cursor.executemany(
            f"INSERT INTO {table} (site_id, {value_column}) VALUES (%s, %s)"  # noqa: S608 - fixed names
            f" ON CONFLICT (site_id, {value_column}) DO NOTHING",
            [(site_id, value) for value in ordered],
        )
        cursor.execute(
            f"SELECT {value_column}, {id_column} FROM {table}"  # noqa: S608 - fixed names
            f" WHERE site_id = %s AND {value_column} = ANY(%s)",
            (site_id, ordered),
        )
        return {value: identifier for value, identifier in cursor.fetchall()}

    # --- reading ------------------------------------------------------------

    def synced_days(self, *, site_id: str, property_url: str, start: date, end: date) -> set[date]:
        """Days in the range already stored from this property."""
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT day FROM gsc_sync_days"
                " WHERE site_id = %s AND property_url = %s AND status = 'synced'"
                " AND day BETWEEN %s AND %s",
                (site_id, property_url, start, end),
            )
            return {row[0] for row in cursor.fetchall()}

    def day_totals(self, *, site_id: str, day: date) -> dict[str, tuple[int, int, float]]:
        """{device: (clicks, impressions, position_sum)} for one stored day."""
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT device, clicks, impressions, position_sum FROM gsc_daily_totals"
                " WHERE site_id = %s AND day = %s",
                (site_id, day),
            )
            return {device: (clicks, impressions, position_sum) for device, clicks, impressions, position_sum in cursor.fetchall()}

    def sync_status(self, *, site_id: str, day: date) -> SyncDayStatus | None:
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT property_url, status, totals_rows, page_rows, query_rows,"
                " page_rows_capped, query_rows_capped, fetched_at, error"
                " FROM gsc_sync_days WHERE site_id = %s AND day = %s",
                (site_id, day),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        keys = ("property_url", "status", "totals_rows", "page_rows", "query_rows",
                "page_rows_capped", "query_rows_capped", "fetched_at", "error")
        return SyncDayStatus(site_id=site_id, day=day, **dict(zip(keys, row)))
