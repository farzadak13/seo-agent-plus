"""Wiring the Search Console warehouse into the running service.

Kept here, not in app.warehouse: which credential a site uses, who may be
synced, and how Google is reached are decisions of the composition root.
"""
from __future__ import annotations

import logging
import threading
from datetime import date

from app.gsc.client import GSCClient
from app.gsc.config import GSCClientConfig
from app.gsc.credentials import build_token_provider
from app.onboarding.ownership import site_may_read_search_console
from app.runtime.properties import google_client_options
from app.warehouse.fetch import QueryAnswer, QueryFn
from app.warehouse.runner import WarehouseSyncRunner
from app.warehouse.store import PostgresWarehouse


log = logging.getLogger(__name__)

ROUND_INTERVAL_SECONDS = 60.0


def build_query_factory(settings, *, secret_resolver, transport):
    """site -> QueryFn: one token provider per site and round, then plain requests."""

    def query_for(site) -> QueryFn:
        secret = secret_resolver.resolve(site.gsc.credential_ref)
        provider = build_token_provider(
            kind=site.gsc.auth_mode,
            secret=secret,
            session=getattr(transport, "session", None),
            **google_client_options(settings),
        )

        def query(dimensions, day: date, data_state: str) -> QueryAnswer:
            client = GSCClient(
                GSCClientConfig(data_state=data_state, timeout_seconds=settings.gsc_timeout_seconds),
                request_fn=transport,
                token_provider=provider,
            )
            response = client.query(
                site_id=site.site_id,
                site_url=site.gsc.property_url,
                start_date=day,
                end_date=day,
                dimensions=tuple(dimensions),
            )
            payload = response.raw_payload
            incomplete = (payload.get("metadata") or {}).get("first_incomplete_date")
            return QueryAnswer(
                rows=list(payload.get("rows") or []),
                first_incomplete_date=date.fromisoformat(incomplete) if incomplete else None,
            )

        return query

    return query_for


def build_warehouse(config, *, site_store, secret_resolver, transport):
    """(store, runner), or (None, None) when the warehouse is switched off."""
    if not config.warehouse_sync_enabled:
        return None, None
    store = PostgresWarehouse(config.database_dsn)
    runner = WarehouseSyncRunner(
        store=store,
        sites=site_store.list_all,
        eligible=site_may_read_search_console,
        query_for=build_query_factory(config, secret_resolver=secret_resolver, transport=transport),
    )
    return store, runner


class WarehouseLoop:
    """Runs a sync round every interval on a background thread, until stopped.

    ``should_run`` is asked before every round. The container passes "is the
    job worker still running": the worker's database lease is what makes this
    the only process syncing, and a worker that has died has released it, so
    another server may now hold it and be syncing the same sites.
    """

    def __init__(
        self,
        runner: WarehouseSyncRunner,
        *,
        interval_seconds: float = ROUND_INTERVAL_SECONDS,
        should_run=lambda: True,
    ) -> None:
        self._runner = runner
        self._interval = interval_seconds
        self._should_run = should_run
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gsc-warehouse", daemon=True)
        self._thread.start()

    def stop(self, timeout_seconds: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_seconds)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._should_run():
                    self._runner.run_round()
                else:
                    log.warning("warehouse: the job worker is not running; skipping this round")
            except Exception:
                # A round that breaks must not end the loop: the next one may
                # well succeed (the database back, a token refreshed).
                log.exception("warehouse round failed")
            self._stop.wait(self._interval)
