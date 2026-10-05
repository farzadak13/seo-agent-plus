"""Reading the warehouse for the dashboard.

Every query reads only days the sync ledger marks synced *from the site's
current property*. A day still holding an old property's rows (the site
switched, and that day has not been fetched again yet) is left out rather
than shown as if it were the new property's.

Averages are recomputed from the additive columns, never averaged again:
CTR = sum(clicks) / sum(impressions), position = sum(position_sum) /
sum(impressions). Both are None where there were no impressions.

Sorting and grouping take their SQL from fixed tables below, never from the
request, so nothing a customer sends becomes SQL.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, timedelta

from app.warehouse.models import (
    Device,
    Interval,
    MetricValues,
    PerformanceReport,
    SeriesPoint,
    SortKey,
    SortOrder,
    TableReport,
    TableRow,
)


INTERVALS = {Interval.DAY: "day", Interval.WEEK: "week", Interval.MONTH: "month"}
SORTS = {
    SortKey.CLICKS: "clicks",
    SortKey.IMPRESSIONS: "impressions",
    SortKey.CTR: "(clicks::float8 / NULLIF(impressions, 0))",
    SortKey.POSITION: "(position_sum / NULLIF(impressions, 0))",
}
ORDERS = {SortOrder.ASC: "ASC NULLS LAST", SortOrder.DESC: "DESC NULLS LAST"}
MAX_LIMIT = 500

# The days of a site that may be read: synced, from its current property.
_SYNCED = """
    JOIN gsc_sync_days s
      ON s.site_id = d.site_id AND s.day = d.day
     AND s.status = 'synced' AND s.property_url = %(property)s
"""


def _metrics(clicks, impressions, position_sum) -> MetricValues:
    clicks, impressions = int(clicks or 0), int(impressions or 0)
    return MetricValues(
        clicks=clicks,
        impressions=impressions,
        ctr=(clicks / impressions) if impressions else None,
        position=(float(position_sum) / impressions) if impressions else None,
    )


def previous_range(start: date, end: date) -> tuple[date, date]:
    """The range of the same length immediately before."""
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


class WarehouseReports:
    def __init__(self, dsn: str, *, connection_factory: Callable | None = None) -> None:
        import psycopg

        self._dsn = dsn
        self._connect = connection_factory or psycopg.connect

    @contextmanager
    def _cursor(self):
        with self._connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                yield cursor

    # --- the chart ----------------------------------------------------------------

    def performance(
        self, *, site_id: str, property_url: str, start: date, end: date,
        interval: Interval = Interval.DAY, device: Device | None = None,
    ) -> PerformanceReport:
        bucket = INTERVALS[Interval(interval)]
        params = {"site": site_id, "property": property_url, "start": start, "end": end,
                  "device": device.value if device else None}
        device_filter = "AND t.device = %(device)s" if device else ""
        with self._cursor() as cursor:
            # Periods are built from the ledger's synced days, and the numbers
            # attached to them. A synced day without traffic (or without
            # traffic on the chosen device) has no totals row; built from the
            # totals it would vanish, and its period would read as unsynced.
            cursor.execute(
                f"""
                SELECT date_trunc('{bucket}', s.day)::date AS period,
                       coalesce(sum(t.clicks), 0), coalesce(sum(t.impressions), 0),
                       coalesce(sum(t.position_sum), 0), count(DISTINCT s.day)
                  FROM gsc_sync_days s
                  LEFT JOIN gsc_daily_totals t
                    ON t.site_id = s.site_id AND t.day = s.day {device_filter}
                 WHERE s.site_id = %(site)s AND s.status = 'synced' AND s.property_url = %(property)s
                   AND s.day BETWEEN %(start)s AND %(end)s
                 GROUP BY 1 ORDER BY 1
                """,  # noqa: S608 - bucket and filter come from fixed tables
                params,
            )
            raw = cursor.fetchall()
            series = [
                SeriesPoint(period_start=period, days_covered=days, **_metrics(c, i, p).model_dump())
                for period, c, i, p, days in raw
            ]
            cursor.execute(
                """
                SELECT count(*) FROM gsc_sync_days
                 WHERE site_id = %(site)s AND property_url = %(property)s AND status = 'synced'
                   AND day BETWEEN %(start)s AND %(end)s
                """,
                params,
            )
            days_covered = cursor.fetchone()[0]
        # From the raw sums, not rebuilt from each period's average.
        totals = _metrics(
            sum(row[1] or 0 for row in raw),
            sum(row[2] or 0 for row in raw),
            sum(row[3] or 0 for row in raw),
        )
        return PerformanceReport(
            site_id=site_id, start=start, end=end, interval=Interval(interval),
            device=device, totals=totals, days_covered=days_covered,
            days_in_range=(end - start).days + 1, series=series,
        )

    # --- the tables -----------------------------------------------------------------

    def pages(
        self, *, site_id: str, property_url: str, start: date, end: date,
        device: Device | None = None, sort: SortKey = SortKey.CLICKS,
        order: SortOrder = SortOrder.DESC, limit: int = 50, offset: int = 0, compare: bool = False,
    ) -> TableReport:
        return self._table(
            kind="page", site_id=site_id, property_url=property_url, start=start, end=end,
            device=device, sort=sort, order=order, limit=limit, offset=offset, compare=compare,
        )

    def queries(
        self, *, site_id: str, property_url: str, start: date, end: date,
        device: Device | None = None, page_url: str | None = None, sort: SortKey = SortKey.CLICKS,
        order: SortOrder = SortOrder.DESC, limit: int = 50, offset: int = 0, compare: bool = False,
    ) -> TableReport:
        return self._table(
            kind="query", site_id=site_id, property_url=property_url, start=start, end=end,
            device=device, page_url=page_url, sort=sort, order=order, limit=limit,
            offset=offset, compare=compare,
        )

    def _table(
        self, *, kind: str, site_id: str, property_url: str, start: date, end: date,
        device: Device | None, sort: SortKey, order: SortOrder, limit: int, offset: int,
        compare: bool, page_url: str | None = None,
    ) -> TableReport:
        sort, order = SortKey(sort), SortOrder(order)
        sort_sql, order_sql = SORTS[sort], ORDERS[order]
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)
        if kind == "page":
            table, key, label_join, label = "gsc_daily_pages", "d.page_id", "JOIN gsc_pages l ON l.page_id = d.page_id", "l.url"
        else:
            table, key, label_join, label = "gsc_daily_queries", "d.query_id", "JOIN gsc_queries l ON l.query_id = d.query_id", "l.query"
        filters = []
        params = {"site": site_id, "property": property_url, "start": start, "end": end,
                  "limit": limit + 1, "offset": offset}
        if device:
            filters.append("AND d.device = %(device)s")
            params["device"] = device.value
        if page_url is not None:
            filters.append("AND d.page_id = (SELECT page_id FROM gsc_pages WHERE site_id = %(site)s AND url = %(page)s)")
            params["page"] = page_url
        where = " ".join(filters)

        aggregate = f"""
            SELECT {key} AS id, {label} AS label,
                   sum(d.clicks) AS clicks, sum(d.impressions) AS impressions,
                   sum(d.position_sum) AS position_sum
              FROM {table} d {_SYNCED} {label_join}
             WHERE d.site_id = %(site)s AND d.day BETWEEN %(start)s AND %(end)s {where}
             GROUP BY 1, 2
        """  # noqa: S608 - names come from the fixed branches above
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT id, label, clicks, impressions, position_sum FROM ({aggregate}) t"
                f" ORDER BY {sort_sql} {order_sql}, label ASC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            fetched = cursor.fetchall()
            more = len(fetched) > limit
            fetched = fetched[:limit]

            previous: dict[int, MetricValues] = {}
            if compare and fetched:
                prev_start, prev_end = previous_range(start, end)
                cursor.execute(
                    f"""
                    SELECT {key}, sum(d.clicks), sum(d.impressions), sum(d.position_sum)
                      FROM {table} d {_SYNCED}
                     WHERE d.site_id = %(site)s AND d.day BETWEEN %(prev_start)s AND %(prev_end)s
                       AND {key} = ANY(%(ids)s) {where}
                     GROUP BY 1
                    """,  # noqa: S608
                    {**params, "prev_start": prev_start, "prev_end": prev_end,
                     "ids": [row[0] for row in fetched]},
                )
                previous = {identifier: _metrics(c, i, p) for identifier, c, i, p in cursor.fetchall()}

        rows = [
            TableRow(
                key=label,
                current=_metrics(clicks, impressions, position_sum),
                previous=previous.get(identifier) if compare else None,
            )
            for identifier, label, clicks, impressions, position_sum in fetched
        ]
        return TableReport(
            site_id=site_id, start=start, end=end, device=device, sort=sort, order=order,
            rows=rows, next_offset=offset + limit if more else None,
            compared_with=previous_range(start, end) if compare else None,
        )
