"""Any website: the customer applies the approved title, HoshyarSEO checks the public page."""
import pytest
from fastapi.testclient import TestClient

from app.action.approval import approve
from app.action.executor import (
    EXECUTE_ACTION_JOB_TYPE,
    build_execute_action_handler,
    build_rollback_action_handler,
)
from app.action.store import ManagedActionStore
from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.measurement.runner import rollback_pending
from app.models.actions import ActionStatus
from app.models.execution import ExecutionCapability
from app.models.site_adapter import SitePage
from app.models.sites import SiteAdapterConnection
from app.onboarding.secrets import EnvironmentSecretResolver
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runtime.adapters import SiteAdapterFactory
from app.site_adapters.hoshyarseo import HoshyarConnectorError, HTTPResponse
from app.site_adapters.manual import ManualAdapterConfig, ManualSiteAdapter
from tests.unit.test_stage37_38_actions import (
    API_KEY,
    NEW_TITLE,
    OLD_TITLE,
    FakeAdapterFactory,
    make_pending,
    make_site,
)

ACTION_ID = "action:run-1"


class FakeManualSite:
    """A page whose title only the customer changes."""

    adapter_id = "manual:site-1"
    manual_apply = True
    exact_rollback = False

    def __init__(self, title=OLD_TITLE):
        self.title = title

    @property
    def capabilities(self):
        return {ExecutionCapability.READ_PAGE}

    def read_page(self, *, site_id, normalized_url):
        return SitePage(site_id=site_id, normalized_url=normalized_url, title=self.title)


def manual_site():
    return make_site(adapter_configured=False).model_copy(
        update={"site_adapter": SiteAdapterConnection(adapter_type="manual")}
    )


def world():
    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(manual_site())
    page = FakeManualSite()
    factory = FakeAdapterFactory(page)
    execute = build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    rollback = build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    actions.create(approve(make_pending(), actor="principal-1"))
    return actions, page, execute, rollback


# ---- applying ----------------------------------------------------------------


def test_until_the_customer_applies_it_the_action_waits_with_the_title_to_apply():
    actions, page, execute, _ = world()

    result = execute({"action_id": ACTION_ID})

    stored = actions.get(ACTION_ID)
    assert stored.status == ActionStatus.APPROVED
    assert stored.applied is None
    assert stored.history[-1].reason == "waiting_for_customer_to_apply"
    assert stored.history[-1].detail == {"apply_title": NEW_TITLE, "found": OLD_TITLE}
    assert result["status"] == "approved"


def test_once_the_page_shows_it_the_change_is_recorded_and_verified():
    actions, page, execute, _ = world()
    execute({"action_id": ACTION_ID})

    page.title = NEW_TITLE  # the customer edited their site
    execute({"action_id": ACTION_ID})

    stored = actions.get(ACTION_ID)
    assert stored.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert stored.applied.previous_value == OLD_TITLE
    assert stored.applied.new_value == NEW_TITLE
    assert stored.applied.change_id is None
    assert [e.reason for e in stored.history][-4:] == [
        "execution_started", "applied_by_customer", "waiting_for_recrawl", "verification_verified",
    ]


def test_a_job_for_an_action_already_applied_does_nothing():
    actions, page, execute, _ = world()
    page.title = NEW_TITLE
    execute({"action_id": ACTION_ID})
    history = actions.get(ACTION_ID).history

    assert execute({"action_id": ACTION_ID})["skipped"] is True
    assert actions.get(ACTION_ID).history == history


# ---- undoing -----------------------------------------------------------------


def test_undoing_waits_for_the_customer_to_restore_the_old_title_and_holds_the_measurement():
    actions, page, execute, rollback = world()
    page.title = NEW_TITLE
    execute({"action_id": ACTION_ID})
    managed = actions.get(ACTION_ID)
    actions.update(managed.record(action=managed.action, actor="principal-1", reason="rollback_requested"))

    rollback({"action_id": ACTION_ID, "actor": "principal-1"})

    waiting = actions.get(ACTION_ID)
    assert waiting.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert waiting.history[-1].reason == "waiting_for_customer_to_restore"
    assert waiting.history[-1].detail == {"restore_title": OLD_TITLE, "found": NEW_TITLE}
    assert rollback_pending(waiting), "no verdict on a change the customer is taking back"

    page.title = OLD_TITLE
    rollback({"action_id": ACTION_ID, "actor": "principal-1"})

    undone = actions.get(ACTION_ID)
    assert undone.status == ActionStatus.ROLLED_BACK
    assert undone.history[-1].reason == "restored_by_customer"


def test_without_the_old_title_an_undo_is_refused_plainly_and_does_not_hold_the_measurement():
    actions, page, execute, rollback = world()
    managed = actions.get(ACTION_ID)
    # As if the proposal had been made without reading the page's title.
    actions.update(managed.model_copy(update={"observed_value": None}))
    page.title = NEW_TITLE
    execute({"action_id": ACTION_ID})
    managed = actions.get(ACTION_ID)
    actions.update(managed.record(action=managed.action, actor="principal-1", reason="rollback_requested"))

    rollback({"action_id": ACTION_ID, "actor": "principal-1"})

    refused = actions.get(ACTION_ID)
    assert refused.history[-1].reason == "rollback_refused"
    assert "not known" in refused.error
    assert not rollback_pending(refused)


# ---- the adapter -----------------------------------------------------------------


def test_the_manual_adapter_reads_the_public_page_past_its_cache_and_never_writes():
    seen = []

    def transport(method, url, headers, body, timeout):
        seen.append((method, url, body))
        return HTTPResponse(200, "<html><head><title>  کفش   مردانه </title></head></html>".encode("utf-8"))

    adapter = ManualSiteAdapter(
        config=ManualAdapterConfig(base_url="https://example.com/"), transport=transport, clock=lambda: 1700000000
    )
    page = adapter.read_page(site_id="site-1", normalized_url="https://example.com/men?a=1")

    assert page.title == "کفش مردانه"
    assert seen == [("GET", "https://example.com/men?a=1&hoshyarseo_check=1700000000", None)]
    assert adapter.capabilities == {ExecutionCapability.READ_PAGE}
    assert not hasattr(adapter, "update_title")


def test_a_page_that_does_not_answer_is_an_error_not_a_title():
    adapter = ManualSiteAdapter(
        config=ManualAdapterConfig(base_url="https://example.com/"),
        transport=lambda *args: HTTPResponse(503, b""),
    )
    with pytest.raises(HoshyarConnectorError):
        adapter.read_page(site_id="site-1", normalized_url="https://example.com/men")


def test_a_manual_connection_needs_no_credentials_and_stays_on_the_site():
    adapter = SiteAdapterFactory(EnvironmentSecretResolver()).build(manual_site())
    assert isinstance(adapter, ManualSiteAdapter)
    assert adapter.adapter_id == "manual:site-1"


# ---- the API -----------------------------------------------------------------


def make_client(site):
    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(site)
    actions.create(make_pending())
    jobs = JobStore(repository)
    ids = iter(f"job-{n}" for n in range(100))
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=jobs, handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
            site_store=sites,
            action_store=actions,
            job_id_factory=lambda: next(ids),
        )
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {API_KEY}"
    return client, actions, jobs


def test_saying_it_is_applied_queues_a_check_of_the_page():
    client, actions, jobs = make_client(manual_site())
    assert client.post(f"/v1/actions/{ACTION_ID}/applied").status_code == 409, "not approved yet"
    client.post(f"/v1/actions/{ACTION_ID}/approve", json={})

    response = client.post(f"/v1/actions/{ACTION_ID}/applied")

    assert response.status_code == 202
    assert actions.get(ACTION_ID).history[-1].reason == "customer_reported_applied"
    assert jobs.get(response.json()["job_id"]).job_type == EXECUTE_ACTION_JOB_TYPE


def test_a_site_that_applies_changes_itself_has_nothing_to_report():
    client, *_ = make_client(make_site())  # a WordPress connection
    client.post(f"/v1/actions/{ACTION_ID}/approve", json={})
    assert client.post(f"/v1/actions/{ACTION_ID}/applied").status_code == 409
