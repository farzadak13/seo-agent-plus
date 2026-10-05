"""The warehouse's connection to Google, its background loop, and its start-up wiring."""
import threading
import time
from datetime import date

import pytest

from app.models.sites import GSCConnectionConfig, SecretProvider, SecretRef, Site
from app.runtime.warehouse import WarehouseLoop, build_query_factory


class Settings:
    gsc_timeout_seconds = 5.0
    google_oauth_client_id = None
    google_oauth_client_secret = None


class Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeGoogle:
    """Answers the Search Analytics endpoint and keeps every request body."""

    def __init__(self, *, first_incomplete_date=None, rows=None):
        self.bodies = []
        self.headers = []
        self.first_incomplete_date = first_incomplete_date
        self.rows = rows or []

    def __call__(self, method, path, *, headers, json, timeout):
        self.bodies.append(json)
        self.headers.append(headers)
        payload = {"rows": self.rows, "responseAggregationType": "byProperty"}
        if json["dataState"] == "all" and self.first_incomplete_date:
            payload["metadata"] = {"first_incomplete_date": self.first_incomplete_date}
        return Response(payload)


class Resolver:
    def __init__(self):
        self.asked = []

    def resolve(self, reference):
        self.asked.append(reference)
        return "a-token"


def site():
    return Site(
        site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/",
        gsc=GSCConnectionConfig(
            property_url="sc-domain:tennisino.com",
            credential_ref=SecretRef(provider=SecretProvider.ENVIRONMENT, key="GSC_TOKEN"),
            auth_mode="access_token",
        ),
    )


DAY = date(2026, 9, 30)


def test_the_requested_data_state_reaches_google_for_each_call():
    google = FakeGoogle()
    query = build_query_factory(Settings(), secret_resolver=Resolver(), transport=google)(site())

    query(("date", "device"), DAY, "all")
    query(("date", "page", "device"), DAY, "final")

    assert [body["dataState"] for body in google.bodies] == ["all", "final"]
    assert google.bodies[0]["dimensions"] == ["date", "device"]
    assert google.bodies[0]["startDate"] == google.bodies[0]["endDate"] == DAY.isoformat()
    assert google.headers[0]["Authorization"] == "Bearer a-token"


def test_googles_incomplete_date_reaches_the_answer():
    # The bridge that keeps a day Google is still collecting from being
    # stored as a day of zeros.
    google = FakeGoogle(first_incomplete_date="2026-10-03")
    query = build_query_factory(Settings(), secret_resolver=Resolver(), transport=google)(site())

    answer = query(("date", "device"), DAY, "all")

    assert answer.first_incomplete_date == date(2026, 10, 3)


def test_no_incomplete_date_means_none():
    query = build_query_factory(Settings(), secret_resolver=Resolver(), transport=FakeGoogle())(site())
    assert query(("date", "device"), DAY, "final").first_incomplete_date is None


def test_rows_are_passed_on_as_they_came():
    rows = [{"keys": [DAY.isoformat(), "MOBILE"], "clicks": 1, "impressions": 2, "position": 3.0}]
    query = build_query_factory(Settings(), secret_resolver=Resolver(), transport=FakeGoogle(rows=rows))(site())
    assert query(("date", "device"), DAY, "final").rows == rows


def test_the_sites_own_credential_is_resolved():
    resolver = Resolver()
    build_query_factory(Settings(), secret_resolver=resolver, transport=FakeGoogle())(site())
    assert resolver.asked == [site().gsc.credential_ref]


# ---- the loop ----------------------------------------------------------------------


class CountingRunner:
    def __init__(self, fail_first=False):
        self.rounds = 0
        self.fail_first = fail_first
        self.ran = threading.Event()

    def run_round(self):
        self.rounds += 1
        if self.fail_first and self.rounds == 1:
            raise RuntimeError("database briefly away")
        self.ran.set()


def wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_round_that_breaks_does_not_end_the_loop():
    runner = CountingRunner(fail_first=True)
    loop = WarehouseLoop(runner, interval_seconds=0.01)
    loop.start()
    try:
        assert runner.ran.wait(2.0), "a later round ran after the first one failed"
    finally:
        loop.stop()


def test_stop_ends_the_thread():
    loop = WarehouseLoop(CountingRunner(), interval_seconds=0.01)
    loop.start()
    loop.stop()
    assert not loop.running


def test_no_round_runs_while_the_worker_is_not_running():
    runner = CountingRunner()
    worker_running = {"value": False}
    loop = WarehouseLoop(runner, interval_seconds=0.01, should_run=lambda: worker_running["value"])
    loop.start()
    try:
        time.sleep(0.1)
        assert runner.rounds == 0
        worker_running["value"] = True
        assert wait_for(lambda: runner.rounds > 0)
    finally:
        loop.stop()


# ---- start-up wiring -----------------------------------------------------------------


class Recorder:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def start(self):
        self.log.append(f"{self.name}.start")

    def stop(self, *args, **kwargs):
        self.log.append(f"{self.name}.stop")


def container(worker_enabled, with_loop=True):
    from app.runtime.container import RuntimeContainer

    class Config:
        pass

    config = Config()
    config.worker_enabled = worker_enabled
    log = []
    built = RuntimeContainer.__new__(RuntimeContainer)
    built.config = config
    built.worker = Recorder(log, "worker")
    built.warehouse_loop = Recorder(log, "loop") if with_loop else None
    return built, log


def test_the_loop_starts_after_the_worker_and_stops_before_it():
    built, log = container(worker_enabled=True)
    built.start()
    built.stop()
    assert log == ["worker.start", "loop.start", "loop.stop", "worker.stop"]


def test_without_the_worker_the_loop_never_starts():
    built, log = container(worker_enabled=False)
    built.start()
    assert "loop.start" not in log


def test_without_the_warehouse_nothing_extra_starts():
    built, log = container(worker_enabled=True, with_loop=False)
    built.start()
    assert log == ["worker.start"]
