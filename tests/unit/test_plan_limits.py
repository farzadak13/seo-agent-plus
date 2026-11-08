from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.action.approval import approve
from app.action.store import ManagedActionStore
from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.onboarding.site_store import SiteStore

from app.billing.plans import QUOTA_WINDOW, UNLIMITED, PlanId, catalog
from app.billing.quota import quota
from app.billing.service import Billing
from app.billing.store import PaymentStore, SubscriptionStore
from app.models.actions import ActionStatus
from app.models.managed_action import ActionEvent
from app.persistence.memory import InMemoryRepository

NOW = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)


class Approved:
    def __init__(self, *moments, status=ActionStatus.APPROVED, applied=None, also=()):
        self.history = [ActionEvent(at=at, status=ActionStatus.APPROVED, actor="t", reason="approved") for at in moments]
        # Other events of an approved action: not approvals.
        self.history += [ActionEvent(at=at, status=ActionStatus.APPROVED, actor="t", reason=reason) for at, reason in also]
        self.status = status
        self.applied = applied
        self.updated_at = max([event.at for event in self.history], default=NOW)


class Actions:
    """As ManagedActionStore.query answers: a page and a cursor."""

    def __init__(self, actions, page_size=2):
        self._actions = actions
        self._size = page_size

    def query(self, *, tenant_id, limit=50, cursor=None):
        start = int(cursor or 0)
        page = self._actions[start:start + self._size]
        nxt = start + self._size
        return page, (str(nxt) if nxt < len(self._actions) else None)


def test_each_plan_allows_what_was_decided():
    plans = catalog()
    basic, gold, trial = plans[PlanId.BASIC], plans[PlanId.GOLD], plans[PlanId.TRIAL]
    assert (basic.changes_a_month, basic.targets, basic.rescan_every) == (20, 10, timedelta(days=30))
    assert (gold.changes_a_month, gold.targets, gold.rescan_every) == (100, 50, timedelta(days=7))
    assert (trial.changes_a_month, trial.targets) == (10, 10)


def test_only_approvals_of_the_last_30_days_count_across_pages():
    old = NOW - QUOTA_WINDOW - timedelta(hours=1)
    actions = Actions([Approved(old), Approved(NOW - timedelta(days=3)), Approved(NOW - timedelta(days=1), NOW - timedelta(days=2))])
    found = quota(actions, "t", 3, NOW)
    assert (found.used, found.left) == (3, 0)
    assert found.frees_at == NOW - timedelta(days=3) + QUOTA_WINDOW  # the oldest that counts
    assert quota(actions, "t", 10, NOW).frees_at is None  # room left: nothing to wait for
    assert quota(actions, "t", None, NOW).left is None  # no limit


def test_a_tenant_without_a_subscription_keeps_working_as_before():
    repository = InMemoryRepository()
    billing = Billing(
        subscriptions=SubscriptionStore(repository), payments=PaymentStore(repository), plans=catalog(),
        gateway=None, callback_url=None, clock=lambda: NOW,
    )
    assert billing.limits("t-operator") == UNLIMITED
    billing.start_trial("t-new")
    assert billing.limits("t-new").changes_a_month == 10



class PlanOf:
    """Billing as the routes ask it, for one tenant on one plan's limits."""

    def __init__(self, limits):
        self._limits = limits

    def limits(self, tenant_id):
        return self._limits

    def require_active(self, tenant_id):
        return SimpleNamespace(active=True)

    def entitlement(self, tenant_id):
        return SimpleNamespace(active=True)


def test_an_approval_past_the_months_changes_is_refused_and_says_when_one_frees_up():
    from tests.unit.test_stage37_38_actions import API_KEY, make_pending, make_site

    repository = InMemoryRepository()
    sites, actions = SiteStore(repository), ManagedActionStore(repository)
    sites.create(make_site())
    for run in ("run-a", "run-b"):
        actions.create(approve(make_pending(run_id=run), actor="principal-1"))
    actions.create(make_pending(run_id="run-c"))
    billing = PlanOf(replace(UNLIMITED, changes_a_month=2))
    app = create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
        site_store=sites, action_store=actions, billing=billing,
    ))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {API_KEY}"
    pending = actions.get("action:run-c")

    refused = client.post(f"/v1/actions/{pending.action_id}/approve", json={})
    assert refused.status_code == 429
    assert "changes for this month are used up" in refused.json()["detail"]

    billing._limits = replace(UNLIMITED, changes_a_month=3)
    assert client.post(f"/v1/actions/{pending.action_id}/approve", json={}).status_code == 202



def test_applied_reports_waits_and_retries_are_not_changes_and_a_failed_one_is_given_back():
    day = NOW - timedelta(days=1)
    actions = Actions([
        Approved(day, also=[(day, "waiting_for_customer_to_apply"), (day, "customer_reported_applied"), (day, "execution_error")]),
        Approved(day, status=ActionStatus.FAILED),  # nothing was written
        Approved(day, status=ActionStatus.ROLLED_BACK, applied=object()),  # undone: the work was done
    ])
    assert quota(actions, "t", 20, NOW).used == 2


def test_over_the_limit_the_change_frees_up_when_enough_have_left():
    days = [NOW - timedelta(days=n) for n in (1, 2, 3, 4, 5)]
    found = quota(Actions([Approved(at) for at in days]), "t", 3, NOW)
    # 5 used of 3: the third oldest must leave before one is free.
    assert found.left == 0 and found.frees_at == NOW - timedelta(days=3) + QUOTA_WINDOW
