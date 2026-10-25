"""A site's activity log, from action histories and the Search Console sync ledger."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.action.approval import approve
from app.action.store import ManagedActionStore
from app.activity import build_activity
from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.warehouse.planner import FAILURE_MESSAGE
from tests.unit.test_stage37_38_actions import API_KEY, NEW_TITLE, make_pending, make_site

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def entry(fetched_at, status="synced", error=None):
    return SimpleNamespace(day=fetched_at.date(), fetched_at=fetched_at,
                           status=SimpleNamespace(value=status), error=error)


def test_actions_and_syncs_are_one_timeline_newest_first():
    managed = approve(make_pending(), actor="principal-1", note="ok")
    entries = [
        entry(NOW - timedelta(hours=1)), entry(NOW - timedelta(hours=2)),
        entry(NOW - timedelta(days=1), status="failed", error="CredentialError: invalid_grant"),
    ]

    items = build_activity(actions=[managed], sync_entries=entries)

    assert [item.at for item in items] == sorted((item.at for item in items), reverse=True)
    syncs = [item for item in items if item.kind == "sync"]
    assert [(s.event, s.detail["days_synced"], s.detail["days_failed"]) for s in syncs] == [
        ("search_console_sync", 2, 0), ("search_console_sync_failed", 0, 1),
    ]
    # The operator's diagnosis stays in the ledger; the customer reads a generic line.
    assert syncs[1].detail["message"] == FAILURE_MESSAGE
    assert "CredentialError" not in str([item.model_dump() for item in items])
    actions = [item for item in items if item.kind == "action"]
    assert {a.event for a in actions} == {"title_proposal_generated", "approved"}
    assert all(a.title == NEW_TITLE and a.action_id == "action:run-1" for a in actions)
    assert next(a for a in actions if a.event == "approved").detail == {"note": "ok"}


def test_the_log_is_capped():
    entries = [entry(NOW - timedelta(days=n)) for n in range(10)]
    assert len(build_activity(actions=[], sync_entries=entries, limit=3)) == 3


class FakeWarehouse:
    def __init__(self, entries):
        self.entries = entries

    def ledger(self, *, site_id, start, end):
        return self.entries


def client_for(principal="principal-1", entries=()):
    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(make_site())
    actions.create(make_pending())
    app = create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator(API_KEY, principal_id=principal),
        site_store=sites, action_store=actions, warehouse=FakeWarehouse(list(entries)),
        clock=lambda: NOW,
    ))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {API_KEY}"
    return client


def test_the_owner_reads_the_log_and_old_events_are_left_out():
    client = client_for(entries=[entry(NOW - timedelta(days=2)), entry(NOW - timedelta(days=60))])

    response = client.get("/v1/sites/site-1/activity?days=30")

    assert response.status_code == 200
    kinds = [(item["kind"], item["event"]) for item in response.json()]
    assert ("sync", "search_console_sync") in kinds
    assert len([k for k in kinds if k[0] == "sync"]) == 1, "the 60-day-old fetch is outside the window"


def test_another_tenant_cannot_read_it():
    assert client_for(principal="principal-2").get("/v1/sites/site-1/activity").status_code == 404
