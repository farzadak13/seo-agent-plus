"""Step 2: which days to fetch, a sync round across sites, and the sync status."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.gsc.client import GSCClientError
from app.models.sites import GSCConnectionConfig, SecretProvider, SecretRef, Site
from app.warehouse.fetch import QueryAnswer
from app.warehouse.models import SyncDayStatus, SyncState
from app.warehouse.planner import (
    WINDOW_DAYS,
    LedgerEntry,
    plan_days,
    search_console_today,
    summarize,
    sync_window,
)
from app.warehouse.runner import WarehouseSyncRunner


NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)  # 04:00 on the 5th in Pacific
PROPERTY = "sc-domain:tennisino.com"


# ---- the window ---------------------------------------------------------------


def test_the_window_ends_at_the_settled_day_in_search_consoles_calendar():
    oldest, newest = sync_window(NOW)
    assert newest == date(2026, 10, 2)  # 5th in Pacific, minus the 3-day lag
    assert (newest - oldest).days + 1 == WINDOW_DAYS


def test_late_utc_evening_is_still_the_earlier_pacific_day():
    # 02:00 UTC on the 6th is still the 5th in California.
    assert search_console_today(datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc)) == date(2026, 10, 5)


# ---- planning -------------------------------------------------------------------


def window_of(days):
    newest = date(2026, 10, 2)
    return newest - timedelta(days=days - 1), newest


def test_newest_days_first_and_never_more_than_asked():
    days = plan_days(window=window_of(30), property_url=PROPERTY, entries={}, now=NOW,
                     retry_after=timedelta(hours=6), limit=3)
    assert days == [date(2026, 10, 2), date(2026, 10, 1), date(2026, 9, 30)]


def test_stored_days_are_skipped_and_the_planner_reaches_back_past_them():
    entries = {date(2026, 10, 2): LedgerEntry(PROPERTY, "synced", NOW),
               date(2026, 10, 1): LedgerEntry(PROPERTY, "synced", NOW)}
    days = plan_days(window=window_of(30), property_url=PROPERTY, entries=entries, now=NOW,
                     retry_after=timedelta(hours=6), limit=1)
    assert days == [date(2026, 9, 30)]


def test_a_day_stored_from_another_property_is_fetched_again():
    entries = {date(2026, 10, 2): LedgerEntry("https://tennisino.com/", "synced", NOW)}
    days = plan_days(window=window_of(1), property_url=PROPERTY, entries=entries, now=NOW,
                     retry_after=timedelta(hours=6), limit=5)
    assert days == [date(2026, 10, 2)]


def test_a_failed_day_waits_before_it_is_tried_again():
    failed_at = NOW - timedelta(hours=1)
    entries = {date(2026, 10, 2): LedgerEntry(PROPERTY, "failed", failed_at)}
    kwargs = dict(window=window_of(1), property_url=PROPERTY, entries=entries,
                  retry_after=timedelta(hours=6), limit=5)
    assert plan_days(now=NOW, **kwargs) == []
    assert plan_days(now=failed_at + timedelta(hours=6), **kwargs) == [date(2026, 10, 2)]


# ---- the summary --------------------------------------------------------------


def status(day, state="synced", *, prop=PROPERTY, capped=False):
    return SyncDayStatus(
        site_id="s1", day=day, property_url=prop, status=state, totals_rows=1, page_rows=1,
        query_rows=1, page_rows_capped=capped, query_rows_capped=False, fetched_at=NOW,
        error="GSCClientError: internal detail" if state == "failed" else None,
    )


def test_a_partly_fetched_window_is_backfilling_with_its_progress():
    window = window_of(4)
    summary = summarize(site_id="s1", property_url=PROPERTY, window=window,
                        entries=[status(date(2026, 10, 2)), status(date(2026, 10, 1), capped=True)])
    assert summary.state == SyncState.BACKFILLING
    assert (summary.synced_days, summary.days_in_window, summary.progress) == (2, 4, 0.5)
    assert summary.newest_synced_day == date(2026, 10, 2)
    assert summary.capped_days == [date(2026, 10, 1)]


def test_a_full_window_is_up_to_date():
    window = window_of(2)
    summary = summarize(site_id="s1", property_url=PROPERTY, window=window,
                        entries=[status(date(2026, 10, 2)), status(date(2026, 10, 1))])
    assert summary.state == SyncState.UP_TO_DATE and summary.progress == 1.0


def test_the_newest_days_all_failing_is_failing_and_the_customer_sees_no_internals():
    window = window_of(5)
    entries = [status(date(2026, 10, 2) - timedelta(days=n), "failed") for n in range(3)]
    summary = summarize(site_id="s1", property_url=PROPERTY, window=window, entries=entries)
    assert summary.state == SyncState.FAILING
    assert all("internal" not in f.message for f in summary.recent_failures)


def test_days_from_the_previous_property_do_not_count():
    summary = summarize(site_id="s1", property_url=PROPERTY, window=window_of(1),
                        entries=[status(date(2026, 10, 2), prop="https://old/")])
    assert summary.synced_days == 0


# ---- a round across sites --------------------------------------------------------


def make_site(site_id="s1", **changes):
    values = dict(
        site_id=site_id, principal_id="p1", name="T", base_url="https://tennisino.com/",
        gsc=GSCConnectionConfig(
            property_url=PROPERTY,
            credential_ref=SecretRef(provider=SecretProvider.DATABASE, key="tenant:p1/google_refresh_token"),
            auth_mode="oauth_refresh_token",
        ),
    )
    values.update(changes)
    return Site(**values)


class FakeWarehouse:
    def __init__(self):
        self.stored, self.failures = [], []

    def ledger(self, *, site_id, start, end):
        return [status(d.day) for d in self.stored if d.site_id == site_id]

    def replace_day(self, data):
        self.stored.append(data)

    def record_failure(self, **kwargs):
        self.failures.append(kwargs)


def ok_query(dimensions, day, data_state):
    return QueryAnswer(rows=[])


def runner(store, sites, query=ok_query, days=3):
    return WarehouseSyncRunner(
        store=store, sites=lambda: sites, eligible=lambda site: site.site_id != "blocked",
        query_for=lambda site: query, clock=lambda: NOW, days_per_round=days,
    )


def test_a_round_syncs_the_newest_days_of_each_eligible_site():
    store = FakeWarehouse()
    report = runner(store, [make_site("s1"), make_site("blocked")]).run_round()
    assert report.synced == 3 and report.sites == 1
    assert [d.day for d in store.stored] == [date(2026, 10, 2), date(2026, 10, 1), date(2026, 9, 30)]


def test_the_next_round_continues_where_the_last_stopped():
    store = FakeWarehouse()
    sync = runner(store, [make_site()])
    sync.run_round()
    sync.run_round()
    assert len({d.day for d in store.stored}) == 6


def test_a_refused_credential_stops_that_site_for_the_round_only():
    store = FakeWarehouse()

    def refused(dimensions, day, data_state):
        raise GSCClientError("forbidden", status_code=403)

    report = runner(store, [make_site()], query=refused, days=5).run_round()
    assert report.failed == 1, "not five identical failures"
    assert report.sites_stopped == ["s1"]


def test_an_ordinary_failure_moves_on_to_the_next_day():
    store = FakeWarehouse()
    calls = {"n": 0}

    def flaky(dimensions, day, data_state):
        calls["n"] += 1
        if day == date(2026, 10, 2):
            raise GSCClientError("server error", status_code=500)
        return QueryAnswer(rows=[])

    report = runner(store, [make_site()], query=flaky).run_round()
    assert (report.failed, report.synced) == (1, 2)


def test_a_day_google_has_not_finished_is_skipped_without_failure():
    store = FakeWarehouse()

    def collecting(dimensions, day, data_state):
        return QueryAnswer(rows=[], first_incomplete_date=date(2026, 10, 2))

    report = runner(store, [make_site()], query=collecting).run_round()
    assert report.not_final == 1 and report.failed == 0 and store.failures == []


def test_a_site_without_search_console_is_left_alone():
    store = FakeWarehouse()
    report = runner(store, [make_site(gsc=None)]).run_round()
    assert report.sites == 0 and store.stored == []


# ---- the shared eligibility rule ---------------------------------------------------


def test_eligibility_follows_the_rule_analyses_use():
    from app.onboarding.ownership import site_may_read_search_console

    assert site_may_read_search_console(make_site()) is True
    shared = GSCConnectionConfig(property_url=PROPERTY, credential_ref=SecretRef(key="GSC_SERVICE_ACCOUNT_JSON"),
                                 auth_mode="service_account")
    assert site_may_read_search_console(make_site(gsc=shared)) is False, "unproven, shared account"
    assert site_may_read_search_console(
        make_site(gsc=shared, ownership_verified_at=NOW, ownership_method="meta_tag")
    ) is True
    assert site_may_read_search_console(make_site(status="paused")) is False


# ---- the endpoint --------------------------------------------------------------------


def api(site, warehouse=None):
    from app.api.app import APIDependencies, create_app
    from app.api.auth import APIKeyAuthenticator
    from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
    from app.onboarding.site_store import SiteStore
    from app.persistence.memory import InMemoryRepository

    repository = InMemoryRepository()
    sites = SiteStore(repository)
    sites.create(site)
    app = create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator("k", principal_id="p1"),
        site_store=sites, warehouse=warehouse, clock=lambda: NOW,
    ))
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer k"
    return client


def test_the_status_shows_progress_for_the_owner():
    store = FakeWarehouse()
    runner(store, [make_site()]).run_round()
    body = api(make_site(), store).get("/v1/sites/s1/sync").json()
    assert body["state"] == "backfilling"
    assert body["synced_days"] == 3 and body["newest_synced_day"] == "2026-10-02"


@pytest.mark.parametrize(
    "site, state",
    [(make_site(gsc=None), "not_connected"),
     (make_site(gsc=GSCConnectionConfig(property_url=PROPERTY, credential_ref=SecretRef(key="GSC_KEY"),
                                        auth_mode="service_account")), "not_eligible")],
)
def test_the_status_says_why_nothing_is_syncing(site, state):
    assert api(site, FakeWarehouse()).get("/v1/sites/s1/sync").json()["state"] == state


def test_the_status_is_unavailable_when_the_warehouse_is_off():
    assert api(make_site()).get("/v1/sites/s1/sync").status_code == 503


def test_another_tenants_site_is_not_found():
    other = make_site(principal_id="p2")
    assert api(other, FakeWarehouse()).get("/v1/sites/s1/sync").status_code == 404


def test_the_warehouse_needs_live_search_console():
    from app.runtime.config import RuntimeConfig

    with pytest.raises(ValueError, match="GSC_MODE=live"):
        RuntimeConfig(api_key="k", database_dsn="d", warehouse_sync_enabled=True)


def test_an_analysis_follows_the_same_rule_as_the_history():
    # A customer's own Google grant on a property that does not cover the site.
    from app.runs.store import RunStore
    from app.api.app import APIDependencies, create_app
    from app.api.auth import APIKeyAuthenticator
    from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
    from app.onboarding.site_store import SiteStore
    from app.persistence.memory import InMemoryRepository

    repository = InMemoryRepository()
    sites = SiteStore(repository)
    foreign = GSCConnectionConfig(
        property_url="sc-domain:other.ir",
        credential_ref=SecretRef(provider=SecretProvider.DATABASE, key="tenant:p1/google_refresh_token"),
        auth_mode="oauth_refresh_token",
    )
    sites.create(make_site(gsc=foreign))
    sites.create(make_site("s2", status="paused"))
    client = TestClient(create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator("k", principal_id="p1"),
        site_store=sites, run_store=RunStore(repository),
    )))
    run = {"start_date": "2026-09-01", "end_date": "2026-09-07", "normalized_url": "https://tennisino.com/x",
           "normalized_query": "q", "candidate_id": "c"}
    headers = {"Authorization": "Bearer k"}
    assert client.post("/v1/sites/s1/runs", json=run, headers=headers).status_code == 409
    assert client.post("/v1/sites/s2/runs", json=run, headers=headers).status_code == 409
