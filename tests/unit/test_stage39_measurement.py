"""Stage 39: a verified change is measured, settled, and taught to its own tenant's learning."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.action.approval import approve, check_rollback
from app.action.executor import build_execute_action_handler, build_rollback_action_handler
from app.action.store import ManagedActionStore
from app.learning.store import FeedbackStore, feedback_id_for
from app.measurement.runner import OUTCOME_AGGREGATE_TYPE, MeasurementRunner, measurement_windows
from app.measurement.series import build_observations
from app.models.actions import ActionStatus
from app.models.observations import DataStatus
from app.models.sites import GSCConnectionConfig, SecretProvider, SecretRef
from app.onboarding.site_store import SiteStore
from app.persistence.contracts import PersistenceConflictError
from app.runtime.config import RuntimeConfig
from tests.unit.test_learning_engine import event
from tests.unit.test_stage34_live_wiring import TransactionalFakeRepository, create_run, setup_api
from tests.unit.test_stage37_38_actions import (
    URL,
    FakeAdapterFactory,
    FakePageAdapter,
    make_pending,
    make_site,
)


ACTION_ID = "action:run-1"
QUERY = "کفش مردانه"
TENANT = "principal-1"
APPLIED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)  # 04:00 on the 1st in Pacific
BASELINE = (date(2026, 7, 4), date(2026, 7, 31))
CURRENT = (date(2026, 8, 9), date(2026, 9, 5))
SETTLED_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)  # newest final day: Sep 7
AFTER_DEADLINE = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)  # newest final day: Sep 19


def days(start, end):
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


EVERY_DAY = days(BASELINE[0], CURRENT[1])


def daily(before, after, impressions=100, position=5.0):
    """(clicks, impressions, position_sum) for every compared day."""
    rows = {day: (before, impressions, position * impressions) for day in days(*BASELINE)}
    rows.update({day: (after, impressions, position * impressions) for day in days(*CURRENT)})
    return rows


PROPERTY = "sc-domain:example.com"


class FakeWarehouse:
    def __init__(self, rows, *, stored=EVERY_DAY, capped=(), properties=None):
        self.rows = rows
        self.stored = set(stored)
        self.capped = set(capped)
        self.properties = properties or {}  # day -> the property it was stored from
        self.on_read = None

    def ledger(self, *, site_id, start, end):
        return [
            SimpleNamespace(
                day=day,
                property_url=self.properties.get(day, PROPERTY),
                status=SimpleNamespace(value="synced"),
                page_rows_capped=day in self.capped,
                fetched_at=datetime(2026, 9, 9, tzinfo=timezone.utc) + timedelta(minutes=index),
            )
            for index, day in enumerate(sorted(self.stored))
            if start <= day <= end
        ]

    def query_days(self, *, site_id, url, query, start, end):
        if self.on_read is not None:
            self.on_read()
        return {day: row for day, row in self.rows.items() if start <= day <= end and day in self.stored}

    def page_days(self, *, site_id, url, start, end):
        return {day for day in self.rows if start <= day <= end and day in self.stored}


def world(warehouse, now=SETTLED_NOW, site=None):
    """One title change, applied on Aug 1 and verified, waiting to be measured."""
    repository = TransactionalFakeRepository("unused")
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(site or make_site())
    factory = FakeAdapterFactory(FakePageAdapter())
    execute = build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    rollback = build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    actions.create(approve(make_pending(), actor=TENANT))
    execute({"action_id": ACTION_ID})
    managed = actions.get(ACTION_ID)
    actions.update(
        managed.model_copy(update={"applied": managed.applied.model_copy(update={"applied_at": APPLIED_AT})})
    )
    feedback = FeedbackStore(repository)
    clock = {"now": now}
    runner = MeasurementRunner(
        repository=repository,
        action_store=actions,
        feedback_store=feedback,
        site_store=sites,
        warehouse=warehouse,
        transaction=repository.transaction,
        clock=lambda: clock["now"],
    )
    return SimpleNamespace(
        repository=repository, actions=actions, feedback=feedback, runner=runner,
        rollback=rollback, clock=clock, warehouse=warehouse,
    )


def outcome_record(w):
    return w.repository.get(aggregate_type=OUTCOME_AGGREGATE_TYPE, aggregate_id=ACTION_ID)


# ---- the windows -----------------------------------------------------------------


def test_the_windows_leave_the_change_day_and_the_settle_days_out():
    windows = measurement_windows(APPLIED_AT, window_days=28, settle_days=7)
    assert windows.change_day == date(2026, 8, 1)
    assert (windows.baseline_start, windows.baseline_end) == BASELINE
    assert (windows.current_start, windows.current_end) == CURRENT
    assert date(2026, 8, 1) not in windows.compared_days()
    assert date(2026, 8, 8) not in windows.compared_days()


def test_the_change_day_is_search_consoles_day_not_utcs():
    # 03:00 UTC on the 2nd is still the 1st in California.
    applied = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
    assert measurement_windows(applied, window_days=28, settle_days=7).change_day == date(2026, 8, 1)


# ---- what each day means -----------------------------------------------------------


def test_a_day_is_observed_zero_or_unknown_by_what_the_warehouse_can_prove():
    d1, d2, d3, d4, d5 = days(date(2026, 7, 1), date(2026, 7, 5))
    observations = build_observations(
        site_id="site-1", normalized_url=URL, normalized_query=QUERY, start=d1, end=d5,
        stored_days={d1, d2, d3, d4},
        capped_days={d4},
        query_rows={d1: (3, 50, 200.0)},
        page_days={d1, d2},
    )
    by_day = {o.date: o for o in observations}
    assert by_day[d1].data_status == DataStatus.OBSERVED
    assert (by_day[d1].clicks, by_day[d1].impressions, by_day[d1].avg_position) == (3, 50, 4.0)
    # The page was seen while the query was not: Search Console hid it. Not zero.
    assert by_day[d2].data_status == DataStatus.MISSING_UNKNOWN
    # The page had no impressions at all, so no query of it had any.
    assert by_day[d3].data_status == DataStatus.MISSING_EXPECTED_ZERO
    # The page rows hit the daily cap: an absent page proves nothing.
    assert by_day[d4].data_status == DataStatus.MISSING_UNKNOWN
    # Never stored.
    assert by_day[d5].data_status == DataStatus.MISSING_UNKNOWN


# ---- settling --------------------------------------------------------------------


def test_a_better_ctr_settles_the_action_as_succeeded_and_teaches_its_tenant():
    w = world(FakeWarehouse(daily(before=5, after=7)))

    report = w.runner.run_round()

    assert report.settled == 1
    stored = w.actions.get(ACTION_ID)
    assert stored.status == ActionStatus.SUCCEEDED
    assert [event.reason for event in stored.history][-2:] == [
        "measurement_ready_for_evaluation",
        "outcome_success",
    ]
    measured = stored.history[-2].detail
    assert measured["baseline"]["start"] == "2026-07-04" and measured["current"]["end"] == "2026-09-05"
    assert measured["baseline"]["ctr"] == 0.05 and measured["current"]["ctr"] == 0.07
    assert measured["data_complete"] is True
    assert stored.history[-1].detail["relative_change"] == 0.4
    assert outcome_record(w).tenant_id == TENANT

    learned = w.feedback.list_for_tenant(TENANT)
    assert [item.feedback_id for item in learned] == [feedback_id_for(ACTION_ID)]
    assert learned[0].relative_change == 0.4


def test_a_worse_ctr_fails_and_the_change_can_still_be_undone():
    w = world(FakeWarehouse(daily(before=5, after=3)))

    w.runner.run_round()

    stored = w.actions.get(ACTION_ID)
    assert stored.status == ActionStatus.FAILED
    check_rollback(stored)
    w.rollback({"action_id": ACTION_ID})
    assert w.actions.get(ACTION_ID).status == ActionStatus.ROLLED_BACK
    assert len(w.feedback.list_for_tenant(TENANT)) == 1, "what was measured stays learned"


@pytest.mark.parametrize(
    "after,status",
    [
        (55, ActionStatus.SUCCEEDED),     # +10%: the threshold itself is met
        (54, ActionStatus.INCONCLUSIVE),  # +8%: noise, not a failure
        (45, ActionStatus.INCONCLUSIVE),  # -10%: not beyond the regression threshold
        (44, ActionStatus.FAILED),        # -12%
    ],
)
def test_a_change_inside_the_noise_band_is_inconclusive_not_failed(after, status):
    w = world(FakeWarehouse(daily(before=50, after=after, impressions=1000)))

    w.runner.run_round()

    settled = w.actions.get(ACTION_ID)
    assert settled.status == status
    if status == ActionStatus.INCONCLUSIVE:
        assert settled.history[-1].detail["reasons"][0] == "primary_metric_change_within_noise_band"


def connected_site(property_url):
    return make_site().model_copy(
        update={
            "gsc": GSCConnectionConfig(
                property_url=property_url,
                credential_ref=SecretRef(provider=SecretProvider.ENVIRONMENT, key="GSC_TOKEN"),
                auth_mode="access_token",
            )
        }
    )


def test_days_still_held_from_a_property_the_site_left_do_not_count():
    # The site moved to the domain property; three days of the second window
    # are still the old URL-prefix copy, not yet fetched again.
    old = {day: "https://example.com/" for day in days(date(2026, 8, 20), date(2026, 8, 22))}
    w = world(FakeWarehouse(daily(5, 7), properties=old), site=connected_site(PROPERTY))

    assert w.runner.run_round().waiting == 1

    w.clock["now"] = AFTER_DEADLINE
    w.runner.run_round()
    settled = w.actions.get(ACTION_ID)
    assert settled.history[-2].detail["data_complete"] is False
    assert settled.history[-2].detail["current"]["completeness"] == round(25 / 28, 6)


def test_a_disconnected_site_is_measured_from_the_property_its_newest_days_came_from():
    # make_site() has no Search Console connection; every stored day is PROPERTY.
    w = world(FakeWarehouse(daily(5, 7)))
    assert w.runner.run_round().settled == 1
    assert w.actions.get(ACTION_ID).history[-2].detail["data_complete"] is True


def test_it_waits_until_the_second_window_is_final():
    w = world(FakeWarehouse(daily(5, 7)), now=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc))

    report = w.runner.run_round()

    assert (report.settled, report.waiting) == (0, 1)
    assert w.actions.get(ACTION_ID).status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert outcome_record(w) is None


def test_a_missing_day_holds_the_verdict_until_the_deadline():
    stored = [day for day in EVERY_DAY if day != date(2026, 8, 20)]
    w = world(FakeWarehouse(daily(5, 7), stored=stored))

    assert w.runner.run_round().waiting == 1
    assert w.actions.get(ACTION_ID).status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE

    w.clock["now"] = AFTER_DEADLINE
    assert w.runner.run_round().settled == 1
    settled = w.actions.get(ACTION_ID)
    assert settled.status == ActionStatus.SUCCEEDED  # 27 of 28 days is enough to judge
    assert settled.history[-2].detail["data_complete"] is False


def test_a_site_whose_sync_stopped_is_settled_as_inconclusive_after_the_deadline():
    # The baseline is stored; nothing after the change ever arrived.
    w = world(FakeWarehouse(daily(5, 7), stored=days(*BASELINE)), now=AFTER_DEADLINE)

    w.runner.run_round()

    stored = w.actions.get(ACTION_ID)
    assert stored.status == ActionStatus.INCONCLUSIVE
    assert stored.history[-1].detail["reasons"][0] == "measurement_not_ready"


def test_a_second_round_neither_settles_nor_counts_it_again():
    w = world(FakeWarehouse(daily(5, 7)))
    w.runner.run_round()
    history = w.actions.get(ACTION_ID).history

    report = w.runner.run_round()

    assert report.settled == 0
    assert w.actions.get(ACTION_ID).history == history
    assert len(w.feedback.list_for_tenant(TENANT)) == 1


def test_a_rolled_back_change_is_not_measured():
    w = world(FakeWarehouse(daily(5, 7)))
    w.rollback({"action_id": ACTION_ID})

    report = w.runner.run_round()

    assert (report.settled, report.waiting) == (0, 0)
    assert outcome_record(w) is None
    assert w.feedback.list_for_tenant(TENANT) == []


def test_a_rollback_landing_during_the_measurement_wins_and_leaves_nothing_behind():
    w = world(FakeWarehouse(daily(5, 7)))
    w.warehouse.on_read = lambda: w.rollback({"action_id": ACTION_ID})

    report = w.runner.run_round()

    assert report.skipped == 1
    assert w.actions.get(ACTION_ID).status == ActionStatus.ROLLED_BACK
    assert outcome_record(w) is None, "outcome, status and feedback are written together or not at all"
    assert w.feedback.list_for_tenant(TENANT) == []


def request_rollback(w):
    """What POST /v1/actions/{id}/rollback writes: a history entry, the status unchanged."""
    managed = w.actions.get(ACTION_ID)
    w.actions.update(managed.record(action=managed.action, actor=TENANT, reason="rollback_requested"))


def test_a_requested_rollback_is_not_settled_away_before_its_job_runs():
    w = world(FakeWarehouse(daily(5, 7)))
    request_rollback(w)

    report = w.runner.run_round()

    assert (report.settled, report.waiting) == (0, 1)
    assert w.actions.get(ACTION_ID).status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    w.rollback({"action_id": ACTION_ID})
    assert w.actions.get(ACTION_ID).status == ActionStatus.ROLLED_BACK
    assert w.feedback.list_for_tenant(TENANT) == []


def test_a_request_landing_during_the_round_is_not_overtaken():
    w = world(FakeWarehouse(daily(5, 7)))
    w.warehouse.on_read = lambda: request_rollback(w)

    assert w.runner.run_round().skipped == 1
    w.warehouse.on_read = None
    assert w.runner.run_round().waiting == 1, "the next round sees the request"
    w.rollback({"action_id": ACTION_ID})
    assert w.actions.get(ACTION_ID).status == ActionStatus.ROLLED_BACK


def test_a_rollback_the_site_refused_no_longer_holds_the_measurement():
    w = world(FakeWarehouse(daily(5, 7)))
    request_rollback(w)
    managed = w.actions.get(ACTION_ID)
    w.actions.update(managed.record(action=managed.action, actor="system", reason="rollback_refused"))

    assert w.runner.run_round().settled == 1
    assert w.actions.get(ACTION_ID).status == ActionStatus.SUCCEEDED


def test_runner_settings_are_checked():
    with pytest.raises(ValueError):
        MeasurementRunner(
            repository=None, action_store=None, feedback_store=None, site_store=None, warehouse=None,
            window_days=0,
        )


# ---- learning, per tenant ----------------------------------------------------------------


def test_each_tenant_learns_only_from_its_own_actions():
    store = FeedbackStore(TransactionalFakeRepository("unused"))
    store.add(event("f1"), tenant_id="t1")
    store.add(event("f2"), tenant_id="t2")

    assert store.learning_context("t1").evidence["feedback_count"] == 1
    assert store.learning_context("t1").context_id.startswith("learning:t1:")
    assert store.learning_context("nobody") is None


def test_feedback_is_stored_once_and_a_different_one_under_the_same_id_is_refused():
    store = FeedbackStore(TransactionalFakeRepository("unused"))
    store.add(event("f1"), tenant_id="t1")
    store.add(event("f1"), tenant_id="t1")
    assert len(store.list_for_tenant("t1")) == 1
    with pytest.raises(PersistenceConflictError):
        store.add(event("f1", change=0.9), tenant_id="t1")
    with pytest.raises(PersistenceConflictError):
        store.add(event("f1"), tenant_id="t2")


def test_a_run_decides_with_its_own_tenants_learning(monkeypatch):
    client, runtime, site_id, _ = setup_api(monkeypatch)
    tenant = runtime.site_store.get(site_id).principal_id
    feedback = FeedbackStore(runtime.repository)
    feedback.add(event("mine"), tenant_id=tenant)
    feedback.add(event("theirs"), tenant_id="someone-else")

    import app.runtime.service as service_module

    original = service_module.run_decision_pipeline
    seen = {}

    def spy(**kwargs):
        seen["context"] = kwargs.get("learning_context")
        return original(**kwargs)

    monkeypatch.setattr(service_module, "run_decision_pipeline", spy)
    create_run(client, site_id)
    runtime.scheduler.run_once()

    assert seen["context"] is not None
    assert seen["context"].evidence["feedback_count"] == 1
    assert seen["context"].context_id.startswith(f"learning:{tenant}:")


# ---- wiring --------------------------------------------------------------------


def build(monkeypatch, **settings):
    from app.runtime.container import build_runtime_container

    monkeypatch.setattr("app.runtime.container.PostgresRepository", TransactionalFakeRepository)
    config = RuntimeConfig(api_key="k", database_dsn="unused", gsc_mode="live", worker_enabled=False, **settings)
    return build_runtime_container(config)


def test_measurement_runs_beside_the_warehouse_and_not_without_it(monkeypatch):
    assert build(monkeypatch).measurement_loop is None
    assert build(monkeypatch, warehouse_sync_enabled=True).measurement_loop is not None


def test_the_measurement_loop_starts_after_the_worker_and_stops_before_it():
    from app.runtime.container import RuntimeContainer

    log = []

    class Recorder:
        def __init__(self, name):
            self.name = name

        def start(self):
            log.append(f"{self.name}.start")

        def stop(self, *args, **kwargs):
            log.append(f"{self.name}.stop")

    built = RuntimeContainer.__new__(RuntimeContainer)
    built.config = SimpleNamespace(worker_enabled=True)
    built.worker = Recorder("worker")
    built.measurement_loop = Recorder("measurement")
    built.start()
    built.stop()
    assert log == ["worker.start", "measurement.start", "measurement.stop", "worker.stop"]


def test_the_measurement_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_API_KEY", "k")
    monkeypatch.setenv("SEO_AGENT_DATABASE_DSN", "unused")
    monkeypatch.setenv("SEO_AGENT_MEASUREMENT_WINDOW_DAYS", "14")
    monkeypatch.setenv("SEO_AGENT_MEASUREMENT_SETTLE_DAYS", "3")
    monkeypatch.setenv("SEO_AGENT_MEASUREMENT_DEADLINE_DAYS", "0")
    config = RuntimeConfig.from_environment()
    assert (config.measurement_window_days, config.measurement_settle_days, config.measurement_deadline_days) == (14, 3, 0)

    with pytest.raises(ValidationError):
        RuntimeConfig(api_key="k", database_dsn="unused", measurement_window_days=3)
