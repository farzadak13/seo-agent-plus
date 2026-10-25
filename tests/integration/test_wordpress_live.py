"""Stage 37/38 against a real WordPress: approve, write, verify, undo.

Runs against the throwaway WordPress from `python scripts/local_wordpress.py up`
(docker/wordpress), with the HoshyarSEO Connector from this checkout. It is
the first time the whole path meets a WordPress rather than a fake: the
plugin's REST routes, its title filter on the rendered page, its change
history, and the executor's checks on what came back.

Settings come from the environment, or from the .env.wordpress the script
writes. Without them, or without a WordPress answering, the module is skipped.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.integration

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _settings() -> dict[str, str]:
    values = {}
    path = os.path.join(ROOT, ".env.wordpress")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                name, sep, value = line.strip().partition("=")
                if sep and not name.startswith("#"):
                    values[name] = value
    for name in ("SEO_AGENT_TEST_WP_URL", "SEO_AGENT_TEST_WP_USER", "SEO_AGENT_TEST_WP_PASSWORD",
                 "SEO_AGENT_TEST_WP_PAGE_URL"):
        if os.getenv(name):
            values[name] = os.environ[name]
    return values


SETTINGS = _settings()
if not SETTINGS.get("SEO_AGENT_TEST_WP_URL"):
    pytest.skip("No local WordPress configured (python scripts/local_wordpress.py up).", allow_module_level=True)

# This test writes a title to a real page, through a transport that skips the
# production guard against private addresses. A leftover setting pointing at
# staging or a customer's site must not be enough to make it do that there.
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
for _name in ("SEO_AGENT_TEST_WP_URL", "SEO_AGENT_TEST_WP_PAGE_URL"):
    _host = urllib.parse.urlsplit(SETTINGS.get(_name, "")).hostname
    if _host not in LOOPBACK_HOSTS:
        pytest.skip(f"{_name} is not on this machine ({_host!r}); refusing to write titles there.",
                    allow_module_level=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Production follows redirects only on the same host; the test follows none."""

    def redirect_request(self, *args, **kwargs):
        return None


# No proxy either: the application password must not leave this machine.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

try:
    _OPENER.open(SETTINGS["SEO_AGENT_TEST_WP_URL"] + "/wp-login.php", timeout=5).close()
except urllib.error.HTTPError:
    pass  # it answered
except OSError as _exc:
    pytest.skip(f"The local WordPress does not answer: {_exc}", allow_module_level=True)

from app.action.approval import approve, propose_title_change  # noqa: E402
from app.action.executor import build_execute_action_handler, build_rollback_action_handler  # noqa: E402
from app.action.planner import build_action  # noqa: E402
from app.action.store import ManagedActionStore  # noqa: E402
from app.models.actions import ActionStatus  # noqa: E402
from app.models.site_adapter import AdapterRollbackRequest  # noqa: E402
from app.onboarding.site_store import SiteStore  # noqa: E402
from app.persistence.memory import InMemoryRepository  # noqa: E402
from app.site_adapters.hoshyarseo import (  # noqa: E402
    HoshyarConnectorAdapter,
    HoshyarConnectorConfig,
    HTTPResponse,
)
from tests.unit.test_stage37_38_actions import FakeAdapterFactory, make_site, make_strategy  # noqa: E402

PAGE = SETTINGS["SEO_AGENT_TEST_WP_PAGE_URL"]
NEW_TITLE = "خرید کفش مردانه اصل | ارسال رایگان"


def loopback_transport(method, url, headers, body, timeout) -> HTTPResponse:
    """The production transport refuses private addresses (app.net.guard), so
    it can never be pointed at this server's own network. This WordPress is on
    127.0.0.1 by design; the test talks to it directly."""
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return HTTPResponse(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return HTTPResponse(exc.code, exc.read())


def connector() -> HoshyarConnectorAdapter:
    return HoshyarConnectorAdapter(
        config=HoshyarConnectorConfig(
            base_url=SETTINGS["SEO_AGENT_TEST_WP_URL"],
            username=SETTINGS["SEO_AGENT_TEST_WP_USER"],
            application_password=SETTINGS["SEO_AGENT_TEST_WP_PASSWORD"],
        ),
        adapter_id="hoshyarseo:site-1",
        transport=loopback_transport,
    )


def test_the_connector_answers_and_the_editor_may_verify_ownership():
    status = connector().status()
    assert status["plugin_version"] == "0.1.0"
    assert status["can_verify_ownership"] is True


def test_a_title_is_approved_written_verified_and_undone_on_a_real_wordpress():
    adapter = connector()
    before = adapter.read_page(site_id="site-1", normalized_url=PAGE)
    assert before.metadata["override_title"] is None, "the page starts without an override"

    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(make_site())
    factory = FakeAdapterFactory(adapter)
    execute = build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)
    rollback = build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=factory)

    # A fresh run id each time: the plugin remembers idempotency keys, and an
    # old key would hand back an earlier, already-undone change.
    run_id = f"wp-live-{uuid.uuid4().hex[:12]}"
    action = build_action(
        strategy=make_strategy().model_copy(update={"normalized_url": PAGE}),
        action_id="action-candidate-1",
    )
    pending = propose_title_change(
        action=action, run_id=run_id, tenant_id="principal-1", proposal_id=f"proposal:{run_id}",
        proposed_title=NEW_TITLE, current_title=before.title, provider_id="live-test",
    )
    actions.create(approve(pending, actor="principal-1"))
    action_id = pending.action_id

    try:
        execute({"action_id": action_id})

        applied = actions.get(action_id)
        assert applied.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE, applied.error
        assert applied.applied.change_id.startswith("hoshyarseo:")
        page = adapter.read_page(site_id="site-1", normalized_url=PAGE)
        assert page.title == NEW_TITLE, "the rendered <title> is the approved one, Persian intact"
        assert page.metadata["override_title"] == NEW_TITLE

        rollback({"action_id": action_id})

        undone = actions.get(action_id)
        assert undone.status == ActionStatus.ROLLED_BACK, undone.error
        after = adapter.read_page(site_id="site-1", normalized_url=PAGE)
        assert after.metadata["override_title"] is None, "exactly as before: no override at all"
        assert after.title == before.title
    finally:
        _undo_if_left_applied(adapter, actions.get(action_id))


def _undo_if_left_applied(adapter, managed) -> None:
    """A failure between writing and undoing must not leave the page retitled
    for every later run; undo through the plugin's own history."""
    if managed.applied is None or managed.rolled_back is not None or not managed.applied.change_id:
        return
    adapter.rollback_change(
        request=AdapterRollbackRequest(
            site_id=managed.site_id,
            normalized_url=managed.action.normalized_url,
            change_id=managed.applied.change_id,
            idempotency_key=f"cleanup:{managed.action_id}",
        )
    )


def test_a_proposal_for_a_title_the_page_no_longer_has_is_not_written():
    """The executor refuses to replace a title other than the one the customer approved replacing."""
    adapter = connector()
    before = adapter.read_page(site_id="site-1", normalized_url=PAGE)

    repository = InMemoryRepository()
    actions = ManagedActionStore(repository)
    sites = SiteStore(repository)
    sites.create(make_site())
    execute = build_execute_action_handler(
        action_store=actions, site_store=sites, adapter_factory=FakeAdapterFactory(adapter)
    )
    run_id = f"wp-live-{uuid.uuid4().hex[:12]}"
    action = build_action(
        strategy=make_strategy().model_copy(update={"normalized_url": PAGE}),
        action_id="action-candidate-1",
    )
    pending = propose_title_change(
        action=action, run_id=run_id, tenant_id="principal-1", proposal_id=f"proposal:{run_id}",
        proposed_title=NEW_TITLE, current_title="A title the page no longer has", provider_id="live-test",
    )
    actions.create(approve(pending, actor="principal-1"))

    execute({"action_id": pending.action_id})

    refused = actions.get(pending.action_id)
    assert refused.status == ActionStatus.FAILED
    assert refused.history[-1].reason == "page_changed_since_proposal"
    assert adapter.read_page(site_id="site-1", normalized_url=PAGE).title == before.title
