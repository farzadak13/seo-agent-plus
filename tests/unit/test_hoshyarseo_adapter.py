"""The connector adapter, against a fake WordPress that behaves like the plugin."""
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.action.approval import approve
from app.action.executor import build_execute_action_handler, build_rollback_action_handler
from app.action.store import ManagedActionStore
from app.models.actions import ActionStatus
from app.models.site_adapter import AdapterRollbackRequest
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.site_adapters.hoshyarseo import (
    HoshyarConnectorAdapter,
    HoshyarConnectorConfig,
    HoshyarConnectorError,
    HTTPResponse,
)
from tests.unit.test_stage37_38_actions import FakeAdapterFactory, make_pending, make_site

URL = "https://example.com/men-shoes"
TEMPLATE_TITLE = "کفش مردانه"  # what the SEO plugin renders with no override
NEW_TITLE = "خرید کفش مردانه اصل | ارسال رایگان"


class FakeWordPress:
    """One page, the connector's endpoints, and the public HTML they affect."""

    def __init__(self, *, plugin_installed=True, password="pw"):
        self.override = None
        self.history = []
        self.plugin_installed = plugin_installed
        self.password = password
        self.requests = []

    def rendered_title(self):
        return self.override if self.override is not None else TEMPLATE_TITLE

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, headers, body))
        parsed = urlparse(url)
        if not parsed.path.startswith("/wp-json/"):
            assert "hoshyarseo_check" in parse_qs(parsed.query)
            # & and | survive as entities in real HTML; the parser must undo them.
            title = self.rendered_title().replace("|", "&#124;")
            html = (
                "<html><head><title>" + title + "</title>"
                '<meta name="description" content="توضیح"></head>'
                "<body><svg><title>icon</title></svg></body></html>"
            )
            return HTTPResponse(200, html.encode("utf-8"))
        if not self.plugin_installed:
            return self._json(404, {"code": "rest_no_route", "message": "No route"})
        import base64

        expected = "Basic " + base64.b64encode(f"editor:{self.password}".encode()).decode()
        if headers.get("Authorization") != expected:
            return self._json(401, {"code": "invalid_username"})
        route = parsed.path.removeprefix("/wp-json/hoshyarseo/v1")
        payload = json.loads(body) if body else {}
        if route == "/status":
            return self._json(200, {"plugin_version": "0.1.0", "seo_plugins": ["yoast"]})
        if route == "/page":
            return self._json(
                200,
                {"post_id": 7, "status": "publish", "post_title": "H1", "override_title": self.override},
            )
        if route == "/title":
            for entry in self.history:
                if entry["idempotency_key"] == payload["idempotency_key"]:
                    return self._json(200, entry)
            entry = {
                "change_id": f"7:{len(self.history) + 1}",
                "idempotency_key": payload["idempotency_key"],
                "previous": self.override,
                "new": payload["title"],
                "rolled_back": False,
                "post_id": 7,
            }
            self.override = payload["title"]
            self.history.append(entry)
            return self._json(200, entry)
        if route == "/rollback":
            entry = next(e for e in self.history if e["change_id"] == payload["change_id"])
            if entry["rolled_back"]:
                return self._json(200, entry)
            if self.override != entry["new"]:
                return self._json(409, {"code": "hoshyarseo_changed_since", "message": "changed since"})
            self.override = entry["previous"]
            entry["rolled_back"] = True
            return self._json(200, entry)
        return self._json(404, {"code": "rest_no_route"})

    @staticmethod
    def _json(status, data):
        return HTTPResponse(status, json.dumps(data).encode("utf-8"))


def make_adapter(wordpress, password="pw"):
    return HoshyarConnectorAdapter(
        config=HoshyarConnectorConfig(
            base_url="https://example.com/", username="editor", application_password=password
        ),
        transport=wordpress,
        clock=lambda: 1_700_000_000,
    )


def test_the_title_is_read_from_the_public_page_not_the_post():
    wordpress = FakeWordPress()
    page = make_adapter(wordpress).read_page(site_id="s1", normalized_url=URL)
    assert page.title == TEMPLATE_TITLE
    assert page.meta_description == "توضیح"
    assert page.metadata["post_title"] == "H1"
    assert page.metadata["override_title"] is None


def test_entities_in_the_rendered_title_are_decoded():
    wordpress = FakeWordPress()
    wordpress.override = NEW_TITLE
    assert make_adapter(wordpress).read_page(site_id="s1", normalized_url=URL).title == NEW_TITLE


def test_update_and_exact_rollback_return_to_the_template():
    wordpress = FakeWordPress()
    adapter = make_adapter(wordpress)

    result = adapter.update_title(
        site_id="s1", normalized_url=URL, new_title=NEW_TITLE, idempotency_key="k1"
    )
    assert result.change_id == "hoshyarseo:7:1"
    assert wordpress.rendered_title() == NEW_TITLE

    undone = adapter.rollback_change(
        request=AdapterRollbackRequest(
            site_id="s1", normalized_url=URL, change_id=result.change_id, idempotency_key="r1"
        )
    )
    assert undone.success
    # Not the old text pinned as a new override: no override at all.
    assert wordpress.override is None


def test_a_missing_plugin_is_named_as_such():
    with pytest.raises(HoshyarConnectorError, match="not installed"):
        make_adapter(FakeWordPress(plugin_installed=False)).status()


def test_a_wrong_password_is_named_as_such():
    with pytest.raises(HoshyarConnectorError, match="application password"):
        make_adapter(FakeWordPress(), password="wrong").status()


def test_the_whole_path_approve_apply_verify_roll_back():
    wordpress = FakeWordPress()
    adapter = make_adapter(wordpress)
    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(make_site())
    factory = FakeAdapterFactory(adapter)
    execute = build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    rollback = build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)

    pending = make_pending(current=TEMPLATE_TITLE)
    actions.create(approve(pending, actor="principal-1"))
    execute({"action_id": pending.action_id})

    applied = actions.get(pending.action_id)
    assert applied.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE, applied.error
    assert applied.applied.change_id == "hoshyarseo:7:1"

    rollback({"action_id": pending.action_id})

    assert actions.get(pending.action_id).status == ActionStatus.ROLLED_BACK
    assert wordpress.override is None
    titles_written = [json.loads(body)["title"] for m, u, h, body in wordpress.requests if u.endswith("/title")]
    assert titles_written == [NEW_TITLE]  # rollback did not write the old text back


def test_a_rollback_refused_by_the_site_is_recorded():
    wordpress = FakeWordPress()
    adapter = make_adapter(wordpress)
    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(make_site())
    factory = FakeAdapterFactory(adapter)
    pending = make_pending(current=TEMPLATE_TITLE)
    actions.create(approve(pending, actor="principal-1"))
    build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)(
        {"action_id": pending.action_id}
    )
    # The page still shows our title, but the plugin's history says otherwise.
    wordpress.history[0]["new"] = "something else"

    build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)(
        {"action_id": pending.action_id}
    )

    stored = actions.get(pending.action_id)
    assert stored.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert stored.history[-1].reason == "rollback_refused"
    assert wordpress.override == NEW_TITLE


def _check_client(wordpress):
    from fastapi.testclient import TestClient

    from app.api.app import APIDependencies, create_app
    from app.api.auth import APIKeyAuthenticator
    from app.jobs import JobHandlerRegistry, JobScheduler, JobStore

    repository = InMemoryRepository()
    sites = SiteStore(repository)
    sites.create(make_site())
    app = create_app(
        APIDependencies(
            scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("k", principal_id="principal-1"),
            site_store=sites,
            adapter_factory=FakeAdapterFactory(make_adapter(wordpress, password="pw")),
        )
    )
    return TestClient(app)


def test_the_connection_check_reports_the_plugin():
    response = _check_client(FakeWordPress()).post(
        "/v1/sites/site-1/connections/site-adapter/check", headers={"Authorization": "Bearer k"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["detail"]["seo_plugins"] == ["yoast"]


def test_the_connection_check_names_a_wrong_password_without_echoing_it():
    response = _check_client(FakeWordPress(password="other")).post(
        "/v1/sites/site-1/connections/site-adapter/check", headers={"Authorization": "Bearer k"}
    )
    body = response.json()
    assert body["ok"] is False
    assert "application password" in body["detail"]
    assert "pw" not in body["detail"].split()


def test_exact_rollback_trusts_the_site_not_a_stale_cache():
    class CachedWordPress(FakeWordPress):
        """Serves the HTML from before every change, as a page cache would."""

        def rendered_title(self):
            return TEMPLATE_TITLE

    wordpress = CachedWordPress()
    adapter = make_adapter(wordpress)
    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(make_site())
    factory = FakeAdapterFactory(adapter)
    pending = make_pending(current=TEMPLATE_TITLE)
    actions.create(approve(pending, actor="principal-1"))

    build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)(
        {"action_id": pending.action_id}
    )
    applied = actions.get(pending.action_id)
    # Stored on the site, cached on the page: verified, with a note.
    assert applied.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert "cached" in applied.history[-1].detail["note"]

    build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)(
        {"action_id": pending.action_id}
    )
    # The cache already shows the old title; the override must still be removed.
    assert wordpress.override is None
    assert actions.get(pending.action_id).status == ActionStatus.ROLLED_BACK
