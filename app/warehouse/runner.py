"""One round of warehouse sync across every eligible site.

The sync ledger is the work list. No job records are created: the general
job queue reads every job it has ever held on each poll, and a 16-month
backfill is hundreds of days per site.

Each round takes, for each site, the next few days the planner names, and
syncs them one by one. A day that is not final yet is simply skipped. A day
that fails is recorded by DaySync and the round moves on; but when Google
refuses the credential itself, every further day would fail the same way,
so that site is left for this round.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.gsc.client import GSCClientError
from app.gsc.credentials import CredentialError
from app.warehouse.fetch import DayNotFinalError, QueryFn
from app.warehouse.planner import LedgerEntry, plan_days, sync_window
from app.warehouse.sync import DaySync


log = logging.getLogger(__name__)

DAYS_PER_ROUND = 10  # x 3 requests: far inside Search Console's per-site quota
RETRY_AFTER = timedelta(hours=6)


@dataclass
class RoundReport:
    synced: int = 0
    failed: int = 0
    not_final: int = 0
    sites: int = 0
    sites_stopped: list[str] = field(default_factory=list)


class WarehouseSyncRunner:
    def __init__(
        self,
        *,
        store,
        sites: Callable[[], Iterable],
        eligible: Callable[[object], bool],
        query_for: Callable[[object], QueryFn],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        days_per_round: int = DAYS_PER_ROUND,
        retry_after: timedelta = RETRY_AFTER,
    ) -> None:
        self._store = store
        self._sync = DaySync(store)
        self._sites = sites
        self._eligible = eligible
        self._query_for = query_for
        self._clock = clock
        self._days_per_round = days_per_round
        self._retry_after = retry_after

    def run_round(self) -> RoundReport:
        report = RoundReport()
        now = self._clock()
        window = sync_window(now)
        for site in self._sites():
            if site.gsc is None or not self._eligible(site):
                continue
            report.sites += 1
            property_url = site.gsc.property_url
            entries = {
                entry.day: LedgerEntry(entry.property_url, entry.status.value, entry.fetched_at)
                for entry in self._store.ledger(site_id=site.site_id, start=window[0], end=window[1])
            }
            days = plan_days(
                window=window, property_url=property_url, entries=entries, now=now,
                retry_after=self._retry_after, limit=self._days_per_round,
            )
            if not days:
                continue
            try:
                query = self._query_for(site)
            except Exception as exc:
                log.warning("warehouse: cannot query site %s: %s", site.site_id, type(exc).__name__)
                report.sites_stopped.append(site.site_id)
                continue
            for day in days:
                try:
                    self._sync.sync_day(
                        site_id=site.site_id, property_url=property_url, day=day,
                        final_through=window[1], query=query,
                    )
                    report.synced += 1
                except DayNotFinalError:
                    report.not_final += 1
                except Exception as exc:  # recorded in the ledger by DaySync
                    report.failed += 1
                    if _credential_refused(exc):
                        log.warning(
                            "warehouse: Google refused the credential of site %s; stopping it this round",
                            site.site_id,
                        )
                        report.sites_stopped.append(site.site_id)
                        break
        if report.synced or report.failed:
            log.info(
                "warehouse round: %d synced, %d failed, %d not final, %d sites",
                report.synced, report.failed, report.not_final, report.sites,
            )
        return report


def _credential_refused(exc: Exception) -> bool:
    if isinstance(exc, CredentialError):
        return True
    return isinstance(exc, GSCClientError) and exc.status_code in {401, 403}
