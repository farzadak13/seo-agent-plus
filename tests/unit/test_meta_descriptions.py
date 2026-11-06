"""Meta descriptions: written for a scanned page, approved, put in place, checked and undone."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.action.executor import (
    EXECUTE_ACTION_JOB_TYPE,
    ROLLBACK_ACTION_JOB_TYPE,
    build_execute_action_handler,
    build_rollback_action_handler,
)
from app.action.store import ManagedActionStore
from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.descriptions.handler import DESCRIPTION_JOB_TYPE, build_description_handler
from app.descriptions.writer import MAX_LENGTH, DescriptionError, DescriptionWriter, usable
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.actions import ActionStatus
from app.models.execution import ExecutionCapability
from app.models.site_adapter import SitePage
from app.models.site_scan import ScannedPage, ScanStatus, SiteScan
from app.models.sites import Site, SiteAdapterConnection
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.scan.store import SiteScanStore

API_KEY = "description-key"
URL = "https://shop.example/shoes"
OLD = ""
GOOD = "کفش ورزشی مردانه و زنانه با ارسال سریع به سراسر ایران؛ مدل‌های تازه را ببینید و سایز مناسب خودتان را انتخاب کنید."
OTHER = "خرید کفش ورزشی برای دویدن و پیاده‌روی، با راهنمای انتخاب سایز و مدل‌های متنوع برای هر سلیقه و هر سن."


class Model:
    provider_id = "fake-llm"

    def __init__(self, *answers):
        self.answers = list(answers)

    def complete(self, *, messages):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def answer(*texts):
    return json.dumps({"descriptions": list(texts)})


class Page:
    """A manual site's public page: only the customer changes its description."""

    adapter_id = "manual:site-1"
    manual_apply = True
    exact_rollback = False

    def __init__(self):
        self.description = OLD

    @property
    def capabilities(self):
        return {ExecutionCapability.READ_PAGE}

    def read_page(self, *, site_id, normalized_url):
        return SitePage(site_id=site_id, normalized_url=normalized_url, title="کفش", meta_description=self.description)


class Factory:
    def __init__(self, adapter):
        self.adapter = adapter

    def build(self, site):
        return self.adapter


class World:
    def __init__(self, *, model=None, adapter=None, connection="manual"):
        repository = InMemoryRepository()
        self.actions = ManagedActionStore(repository)
        self.sites = SiteStore(repository)
        self.scans = SiteScanStore(repository)
        self.page = adapter or Page()
        self.sites.create(Site(
            site_id="site-1", principal_id="principal-1", name="n", base_url="https://shop.example/",
            site_adapter=SiteAdapterConnection(adapter_type=connection) if connection else None,
        ))
        self.scans.save(SiteScan(
            site_id="site-1", tenant_id="principal-1", job_id="scan-1", status=ScanStatus.COMPLETED,
            pages=[ScannedPage(url=URL, status_code=200, title="کفش ورزشی", h1="کفش ورزشی", meta_description=None)],
        ))
        factory = Factory(self.page)
        handlers = JobHandlerRegistry()
        handlers.register(DESCRIPTION_JOB_TYPE, build_description_handler(
            scan_store=self.scans, action_store=self.actions, writer=DescriptionWriter(transport=model or Model(answer(GOOD, OTHER))),
            clock=lambda: datetime(2026, 11, 6, tzinfo=timezone.utc),
        ))
        handlers.register(EXECUTE_ACTION_JOB_TYPE, build_execute_action_handler(action_store=self.actions, site_store=self.sites, adapter_factory=factory))
        handlers.register(ROLLBACK_ACTION_JOB_TYPE, build_rollback_action_handler(action_store=self.actions, site_store=self.sites, adapter_factory=factory))
        app = create_app(APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=handlers),
            authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
            site_store=self.sites, action_store=self.actions, scan_store=self.scans, adapter_factory=factory,
            descriptions_enabled=True,
        ))
        self.client = TestClient(app)
        self.client.headers["Authorization"] = f"Bearer {API_KEY}"
        self.scheduler = app.state.dependencies.scheduler

    def run(self):
        while self.scheduler.run_once() is not None:
            pass

    def post(self, path, body=None, expect=202):
        response = self.client.post(path, json=body or {})
        assert response.status_code == expect, response.text
        self.run()
        return response.json()

    def ask(self, expect=202, url=URL):
        return self.post("/v1/sites/site-1/meta-descriptions", {"url": url}, expect)


def test_only_descriptions_of_the_right_length_are_offered():
    kept = usable(answer("«" + GOOD + "»", "کوتاه", "ب" * (MAX_LENGTH + 1), GOOD, OTHER))
    assert kept == [GOOD, OTHER]
    with pytest.raises(DescriptionError):
        usable("not json")


def test_a_model_that_writes_nothing_usable_is_asked_once_more():
    model = Model(answer("کوتاه"), answer(GOOD))
    assert DescriptionWriter(transport=model).write(
        __import__("app.descriptions.writer", fromlist=["PageForDescription"]).PageForDescription(url=URL, title=None, h1=None, current=None)
    ) == [GOOD]


def test_a_description_goes_from_the_request_to_the_page_and_back():
    world = World()
    asked = world.ask()

    proposal = world.client.get(f"/v1/actions/{asked['action_id']}").json()
    assert proposal["field"] == "description" and proposal["status"] == "awaiting_approval"
    assert proposal["proposed_title"] == GOOD and proposal["alternative_titles"] == [OTHER]
    assert proposal["current_title"] == ""

    world.post(f"/v1/actions/{asked['action_id']}/approve", {"title": OTHER})
    waiting = world.actions.get(asked["action_id"])
    assert waiting.status == ActionStatus.APPROVED
    assert waiting.history[-1].detail == {"apply_description": OTHER, "found": OLD}

    world.page.description = OTHER  # the customer puts it in place
    world.post(f"/v1/actions/{asked['action_id']}/applied")
    applied = world.actions.get(asked["action_id"])
    assert applied.applied.new_value == OTHER and applied.status != ActionStatus.FAILED

    world.post(f"/v1/actions/{asked['action_id']}/rollback")
    restore = world.actions.get(asked["action_id"])
    assert restore.history[-1].reason == "waiting_for_customer_to_restore"
    assert restore.history[-1].detail == {"restore_description": "", "found": OTHER}


def test_one_open_description_per_page_and_only_for_pages_the_scan_read():
    world = World()
    world.ask()
    world.ask(expect=409)
    world.ask(expect=422, url="https://shop.example/elsewhere")


def test_a_connected_site_whose_plugin_cannot_write_descriptions_is_told_to_update_it():
    class OldPlugin(Page):
        manual_apply = False

        @property
        def capabilities(self):
            return {ExecutionCapability.READ_PAGE, ExecutionCapability.UPDATE_TITLE}

    world = World(adapter=OldPlugin(), connection="hoshyarseo")
    assert "Update the HoshyarSEO plugin" in world.ask(expect=409)["detail"]


def test_a_model_that_fails_leaves_no_proposal():
    world = World(model=Model("not json"))
    asked = world.ask()
    assert world.actions.find(asked["action_id"]) is None


def test_a_title_change_still_works_as_before():
    from tests.unit.test_manual_apply import world as title_world

    actions, page, execute, _ = title_world()
    execute({"action_id": "action:run-1"})
    assert actions.get("action:run-1").history[-1].detail["apply_title"]


class Connected(Page):
    """A connected site whose connector writes descriptions itself."""

    manual_apply = False
    exact_rollback = False
    adapter_id = "wordpress:site-1"

    def __init__(self):
        super().__init__()
        self.writes = []

    @property
    def capabilities(self):
        return {ExecutionCapability.READ_PAGE, ExecutionCapability.UPDATE_TITLE, ExecutionCapability.UPDATE_META_DESCRIPTION}

    def update_meta_description(self, *, site_id, normalized_url, new_meta_description, idempotency_key):
        from app.models.site_adapter import AdapterOperation, AdapterOperationResult

        self.writes.append(new_meta_description)
        self.description = new_meta_description
        return AdapterOperationResult(
            success=True, operation=AdapterOperation.UPDATE_META_DESCRIPTION, site_id=site_id,
            normalized_url=normalized_url, message="ok", change_id=None,
        )


def test_a_connected_site_writes_the_approved_description_and_undoes_it():
    world = World(adapter=Connected(), connection="wordpress")
    asked = world.ask()
    world.post(f"/v1/actions/{asked['action_id']}/approve")
    done = world.actions.get(asked["action_id"])
    assert world.page.writes == [GOOD] and done.applied.new_value == GOOD
    assert done.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE

    world.post(f"/v1/actions/{asked['action_id']}/rollback")
    assert world.page.writes == [GOOD, ""] and world.actions.get(asked["action_id"]).status == ActionStatus.ROLLED_BACK


def test_a_page_that_had_no_description_is_restored_by_clearing_it():
    world = World()
    asked = world.ask()
    world.post(f"/v1/actions/{asked['action_id']}/approve")
    world.page.description = GOOD
    world.post(f"/v1/actions/{asked['action_id']}/applied")
    world.post(f"/v1/actions/{asked['action_id']}/rollback")
    assert world.actions.get(asked["action_id"]).history[-1].reason == "waiting_for_customer_to_restore"
    world.page.description = ""
    world.post(f"/v1/actions/{asked['action_id']}/rollback")
    assert world.actions.get(asked["action_id"]).status == ActionStatus.ROLLED_BACK


def test_a_second_click_before_the_first_proposal_is_back_is_refused():
    world = World()
    world.client.post("/v1/sites/site-1/meta-descriptions", json={"url": URL})  # job not run yet
    response = world.client.post("/v1/sites/site-1/meta-descriptions", json={"url": URL})
    assert response.status_code == 409


def test_another_customers_site_is_not_found():
    world = World()
    world.sites.create(Site(site_id="site-2", principal_id="principal-2", name="o", base_url="https://other.example/"))
    world.post("/v1/sites/site-2/meta-descriptions", {"url": "https://other.example/"}, expect=404)
