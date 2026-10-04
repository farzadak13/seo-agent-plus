"""Stage 37 and 38: a proposal waits for a person, then reaches the site and can be undone."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.action.approval import ApprovalError, approve, check_rollback, propose_title_change, reject
from app.action.executor import (
    EXECUTE_ACTION_JOB_TYPE,
    ROLLBACK_ACTION_JOB_TYPE,
    build_execute_action_handler,
    build_rollback_action_handler,
)
from app.action.planner import build_action
from app.action.store import ManagedActionStore
from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.actions import ActionStatus
from app.models.execution import ExecutionCapability
from app.models.site_adapter import AdapterOperation, AdapterOperationResult, SitePage
from app.models.sites import SecretProvider, SecretRef, Site, SiteAdapterConnection
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import Strategy, StrategyStatus, StrategyType
from app.models.opportunities import OpportunityType
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository


API_KEY = "stage37-secret"
URL = "https://example.com/men-shoes"
OLD_TITLE = "کفش مردانه"
NEW_TITLE = "خرید کفش مردانه اصل | ارسال رایگان"


def make_strategy():
    return Strategy(
        strategy_id="strategy-1",
        opportunity_id="opportunity-1",
        site_id="site-1",
        normalized_url=URL,
        normalized_query="کفش مردانه",
        opportunity_type=OpportunityType.VISIBILITY_GROWTH,
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=1.0,
        expected_impact_score=0.8,
        effort_score=0.2,
        risk_score=0.1,
        priority_score=0.9,
        reasons=["test"],
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-1",
            data_snapshot_id="data-1",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime.now(timezone.utc),
        ),
    )


def make_pending(run_id="run-1", tenant="principal-1", current=OLD_TITLE):
    action = build_action(strategy=make_strategy(), action_id="action-candidate-1")
    return propose_title_change(
        action=action,
        run_id=run_id,
        tenant_id=tenant,
        proposal_id=f"proposal:{run_id}",
        proposed_title=NEW_TITLE,
        current_title=current,
        provider_id="fake-llm",
    )


class FakePageAdapter:
    """A site with one page whose title can be read and written."""

    adapter_id = "fake:site-1"

    def __init__(self, title=OLD_TITLE, *, ignore_writes=False, fail_writes=False):
        self.title = title
        self.writes = []
        self.ignore_writes = ignore_writes
        self.fail_writes = fail_writes

    @property
    def capabilities(self):
        return {ExecutionCapability.READ_PAGE, ExecutionCapability.UPDATE_TITLE}

    def read_page(self, *, site_id, normalized_url):
        return SitePage(site_id=site_id, normalized_url=normalized_url, title=self.title)

    def update_title(self, *, site_id, normalized_url, new_title, idempotency_key):
        if self.fail_writes:
            raise RuntimeError("site is down")
        self.writes.append((new_title, idempotency_key))
        previous = self.title
        if not self.ignore_writes:
            self.title = new_title
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_TITLE,
            site_id=site_id,
            normalized_url=normalized_url,
            change_id=f"fake:{len(self.writes)}",
            previous_value=previous,
            new_value=new_title,
        )


class FakeAdapterFactory:
    def __init__(self, adapter):
        self.adapter = adapter

    def build(self, site):
        return self.adapter


def make_site(adapter_configured=True):
    return Site(
        site_id="site-1",
        principal_id="principal-1",
        name="Example",
        base_url="https://example.com/",
        site_adapter=(
            SiteAdapterConnection(
                adapter_type="wordpress",
                config={},
                secret_refs={
                    "username": SecretRef(provider=SecretProvider.ENVIRONMENT, key="WP_USER")
                },
            )
            if adapter_configured
            else None
        ),
    )


def make_world(adapter=None, *, site=None):
    repository = InMemoryRepository()
    action_store = ManagedActionStore(repository)
    site_store = SiteStore(repository)
    site_store.create(site or make_site())
    adapter = adapter or FakePageAdapter()
    factory = FakeAdapterFactory(adapter)
    execute = build_execute_action_handler(
        action_store=action_store, site_store=site_store, adapter_factory=factory
    )
    rollback = build_rollback_action_handler(
        action_store=action_store, site_store=site_store, adapter_factory=factory
    )
    return action_store, site_store, adapter, execute, rollback


# ---- Stage 37: the decision ------------------------------------------------


def test_a_proposal_waits_for_approval_and_carries_the_title():
    pending = make_pending()
    assert pending.status == ActionStatus.AWAITING_APPROVAL
    assert pending.action_id == "action:run-1"
    assert pending.action.parameters["recommended_title"] == NEW_TITLE
    assert pending.action.parameters["expected_title"] == NEW_TITLE
    assert pending.observed_value == OLD_TITLE
    assert [event.reason for event in pending.history] == ["title_proposal_generated"]


def test_approval_records_who_approved():
    approved = approve(make_pending(), actor="principal-1", note="ok")
    assert approved.status == ActionStatus.APPROVED
    assert approved.history[-1].actor == "principal-1"
    assert approved.history[-1].detail == {"note": "ok"}


def test_a_reviewer_can_approve_an_edited_title():
    approved = approve(make_pending(), actor="principal-1", title="  عنوان   ویرایش‌شده ")
    assert approved.action.parameters["recommended_title"] == "عنوان ویرایش‌شده"
    assert approved.action.parameters["expected_title"] == "عنوان ویرایش‌شده"
    assert approved.action.parameters["edited_by_reviewer"] is True
    assert approved.history[-1].detail["proposed_title"] == NEW_TITLE


def test_an_action_cannot_be_approved_twice():
    approved = approve(make_pending(), actor="principal-1")
    with pytest.raises(ApprovalError):
        approve(approved, actor="principal-1")


def test_a_rejected_action_cannot_be_approved():
    rejected = reject(make_pending(), actor="principal-1", reason="off brand")
    assert rejected.status == ActionStatus.REJECTED
    with pytest.raises(ApprovalError):
        approve(rejected, actor="principal-1")


def test_an_empty_edited_title_is_refused():
    with pytest.raises(ApprovalError):
        approve(make_pending(), actor="principal-1", title="   ")


def test_nothing_applied_means_nothing_to_roll_back():
    with pytest.raises(ApprovalError):
        check_rollback(make_pending())


# ---- Stage 38: carrying it out ---------------------------------------------


def test_an_approved_title_reaches_the_site_and_is_verified():
    action_store, _, adapter, execute, _ = make_world()
    action_store.create(approve(make_pending(), actor="principal-1"))

    result = execute({"action_id": "action:run-1"})

    assert adapter.title == NEW_TITLE
    assert adapter.writes == [(NEW_TITLE, "action:action:run-1")]
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert stored.applied.previous_value == OLD_TITLE
    assert stored.applied.new_value == NEW_TITLE
    assert stored.applied.change_id == "fake:1"
    assert result["status"] == "measurement_window_active"
    assert [event.reason for event in stored.history][-4:] == [
        "execution_started",
        "execution_succeeded",
        "waiting_for_recrawl",
        "verification_verified",
    ]


def test_an_unapproved_action_is_never_executed():
    action_store, _, adapter, execute, _ = make_world()
    action_store.create(make_pending())

    result = execute({"action_id": "action:run-1"})

    assert result["skipped"] is True
    assert adapter.writes == []
    assert action_store.get("action:run-1").status == ActionStatus.AWAITING_APPROVAL


def test_a_page_edited_since_the_proposal_is_not_overwritten():
    adapter = FakePageAdapter(title="someone else's title")
    action_store, _, _, execute, _ = make_world(adapter)
    action_store.create(approve(make_pending(), actor="principal-1"))

    execute({"action_id": "action:run-1"})

    assert adapter.writes == []
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.FAILED
    assert stored.history[-1].reason == "page_changed_since_proposal"


def test_a_second_delivery_after_a_crash_repeats_the_write_under_the_same_key():
    # The first delivery wrote the title and died before recording it. The
    # repeat uses the same idempotency key, so the site returns the original
    # change (and its id) instead of making another one.
    adapter = FakePageAdapter(title=NEW_TITLE)
    action_store, _, _, execute, _ = make_world(adapter)
    action_store.create(approve(make_pending(), actor="principal-1"))

    execute({"action_id": "action:run-1"})

    assert adapter.writes == [(NEW_TITLE, "action:action:run-1")]
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert stored.applied.previous_value == OLD_TITLE
    assert "already_applied" in [event.reason for event in stored.history]


def test_a_write_the_page_does_not_show_is_a_failure():
    adapter = FakePageAdapter(ignore_writes=True)
    action_store, _, _, execute, _ = make_world(adapter)
    action_store.create(approve(make_pending(), actor="principal-1"))

    execute({"action_id": "action:run-1"})

    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.FAILED
    assert stored.error


def test_an_unreachable_site_is_recorded_and_retried():
    adapter = FakePageAdapter(fail_writes=True)
    action_store, _, _, execute, _ = make_world(adapter)
    action_store.create(approve(make_pending(), actor="principal-1"))

    # The job fails so the scheduler retries it; the customer sees why.
    with pytest.raises(RuntimeError):
        execute({"action_id": "action:run-1"})
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.EXECUTING
    assert "site is down" in stored.error

    adapter.fail_writes = False
    execute({"action_id": "action:run-1"})
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert stored.error is None


def test_a_timeout_after_the_site_accepted_the_write_is_not_lost():
    class AcceptsThenTimesOut(FakePageAdapter):
        def update_title(self, **kwargs):
            result = super().update_title(**kwargs)
            if len(self.writes) == 1:
                raise TimeoutError("no answer")
            return result

    adapter = AcceptsThenTimesOut()
    action_store, _, _, execute, rollback = make_world(adapter)
    action_store.create(approve(make_pending(), actor="principal-1"))

    with pytest.raises(TimeoutError):
        execute({"action_id": "action:run-1"})
    execute({"action_id": "action:run-1"})

    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert stored.applied.previous_value == OLD_TITLE
    rollback({"action_id": "action:run-1"})
    assert adapter.title == OLD_TITLE


def test_a_rejection_cannot_land_once_execution_has_claimed_the_action():
    action_store, *_ = make_world()
    approved = action_store.create(approve(make_pending(), actor="principal-1"))
    from app.action.executor import _move

    action_store.update(_move(approved, ActionStatus.EXECUTING, "execution_started"))
    with pytest.raises(ApprovalError):
        reject(action_store.get("action:run-1"), actor="principal-1")


def test_a_write_built_from_a_stale_copy_is_refused():
    from app.action.store import ActionChangedError

    action_store, *_ = make_world()
    pending = action_store.create(make_pending())
    action_store.update(reject(pending, actor="principal-1"))  # someone else's decision
    with pytest.raises(ActionChangedError):
        action_store.update(approve(pending, actor="principal-1"))  # built from before it
    assert action_store.get("action:run-1").status == ActionStatus.REJECTED


def test_rollback_restores_the_previous_title():
    action_store, _, adapter, execute, rollback = make_world()
    action_store.create(approve(make_pending(), actor="principal-1"))
    execute({"action_id": "action:run-1"})

    rollback({"action_id": "action:run-1", "actor": "principal-1"})

    assert adapter.title == OLD_TITLE
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.ROLLED_BACK
    assert stored.rolled_back.new_value == OLD_TITLE
    assert stored.history[-1].actor == "principal-1"


def test_rollback_does_not_overwrite_a_later_manual_edit():
    action_store, _, adapter, execute, rollback = make_world()
    action_store.create(approve(make_pending(), actor="principal-1"))
    execute({"action_id": "action:run-1"})
    adapter.title = "edited by hand afterwards"

    rollback({"action_id": "action:run-1"})

    assert adapter.title == "edited by hand afterwards"
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE
    assert stored.history[-1].reason == "rollback_refused_page_changed"


def test_rollback_delivered_twice_writes_once():
    action_store, _, adapter, execute, rollback = make_world()
    action_store.create(approve(make_pending(), actor="principal-1"))
    execute({"action_id": "action:run-1"})

    rollback({"action_id": "action:run-1"})
    second = rollback({"action_id": "action:run-1"})

    assert second["skipped"] is True
    assert len(adapter.writes) == 2  # the change, and one rollback


# ---- The API ---------------------------------------------------------------


def make_client(*, adapter_configured=True, principal="principal-1"):
    repository = InMemoryRepository()
    action_store = ManagedActionStore(repository)
    site_store = SiteStore(repository)
    site_store.create(make_site(adapter_configured))
    action_store.create(make_pending())
    job_store = JobStore(repository)
    scheduler = JobScheduler(store=job_store, handlers=JobHandlerRegistry())
    jobs = iter(f"job-{n}" for n in range(100))
    app = create_app(
        APIDependencies(
            scheduler=scheduler,
            authenticator=APIKeyAuthenticator(API_KEY, principal_id=principal),
            site_store=site_store,
            action_store=action_store,
            job_id_factory=lambda: next(jobs),
        )
    )
    return TestClient(app), action_store, job_store


def headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def test_the_owner_sees_pending_actions_for_a_site():
    client, *_ = make_client()
    response = client.get("/v1/sites/site-1/actions", headers=headers())
    assert response.status_code == 200
    [item] = response.json()["actions"]
    assert item["status"] == "awaiting_approval"
    assert item["current_title"] == OLD_TITLE
    assert item["proposed_title"] == NEW_TITLE


def test_another_tenant_cannot_see_or_approve_an_action():
    client, *_ = make_client(principal="principal-2")
    assert client.get("/v1/actions/action:run-1", headers=headers()).status_code == 404
    response = client.post("/v1/actions/action:run-1/approve", headers=headers(), json={})
    assert response.status_code == 404


def test_approving_queues_exactly_one_execution_job():
    client, action_store, job_store = make_client()
    response = client.post(
        "/v1/actions/action:run-1/approve", headers=headers(), json={"title": "عنوان من"}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "approved"
    assert body["proposed_title"] == "عنوان من"
    assert body["edited_by_reviewer"] is True
    job = job_store.get(body["job_id"])
    assert job.job_type == EXECUTE_ACTION_JOB_TYPE
    assert job.payload["action_id"] == "action:run-1"

    again = client.post("/v1/actions/action:run-1/approve", headers=headers(), json={})
    assert again.status_code == 409


def test_approval_needs_a_connected_site():
    client, *_ = make_client(adapter_configured=False)
    response = client.post("/v1/actions/action:run-1/approve", headers=headers(), json={})
    assert response.status_code == 409


def test_reject_through_the_api():
    client, action_store, _ = make_client()
    response = client.post(
        "/v1/actions/action:run-1/reject", headers=headers(), json={"reason": "no"}
    )
    assert response.status_code == 200
    assert action_store.get("action:run-1").status == ActionStatus.REJECTED


def test_rollback_of_an_unapplied_action_is_refused():
    client, *_ = make_client()
    response = client.post("/v1/actions/action:run-1/rollback", headers=headers(), json={})
    assert response.status_code == 409


def test_rollback_through_the_api_queues_a_job():
    client, action_store, job_store = make_client()
    action_store.update(
        approve(action_store.get("action:run-1"), actor="principal-1")
    )
    adapter = FakePageAdapter()
    site_store = SiteStore(InMemoryRepository())
    site_store.create(make_site())
    build_execute_action_handler(
        action_store=action_store,
        site_store=site_store,
        adapter_factory=FakeAdapterFactory(adapter),
    )({"action_id": "action:run-1"})

    response = client.post("/v1/actions/action:run-1/rollback", headers=headers(), json={})

    assert response.status_code == 202
    assert job_store.get(response.json()["job_id"]).job_type == ROLLBACK_ACTION_JOB_TYPE


@pytest.mark.parametrize("job_type", ["seo_run", EXECUTE_ACTION_JOB_TYPE, ROLLBACK_ACTION_JOB_TYPE])
def test_internal_job_types_cannot_be_created_directly(job_type):
    client, *_ = make_client()
    response = client.post(
        "/v1/jobs",
        headers=headers(),
        json={"job_type": job_type, "payload": {"action_id": "action:run-1"}},
    )
    assert response.status_code == 422


# ---- The run hands a completed proposal to a person ------------------------


def test_a_completed_run_queues_its_title_for_approval_once():
    from datetime import date
    from types import SimpleNamespace

    from app.models.runs import SEORun
    from app.models.title_proposal import TitleProposalStatus
    from app.runs.handler import build_seo_run_handler

    strategy = make_strategy()
    action = build_action(strategy=strategy, action_id="action-candidate-1")

    class RunService:
        def run(self, **kwargs):
            return SimpleNamespace(
                status=SimpleNamespace(value="completed"),
                strategy=strategy,
                action=action,
                model_dump=lambda mode="json": {"status": "completed"},
            )

    class TitleWorkflow:
        def run(self, **kwargs):
            return SimpleNamespace(
                proposal_id="proposal:run-1",
                status=TitleProposalStatus.COMPLETED,
                selected_title=NEW_TITLE,
                provider_id="fake-llm",
                investigation=SimpleNamespace(target_title=OLD_TITLE),
            )

    class Runs:
        def __init__(self, run):
            self.run = run

        def get(self, run_id):
            return self.run

        def update(self, run):
            self.run = run
            return run

    runs = Runs(
        SEORun(
            run_id="run-1",
            site_id="site-1",
            principal_id="principal-1",
            job_id="job-1",
            start_date=date(2026, 9, 1).isoformat(),
            end_date=date(2026, 9, 2).isoformat(),
            normalized_url=URL,
            normalized_query="کفش مردانه",
            candidate_id="candidate-1",
        )
    )
    action_store = ManagedActionStore(InMemoryRepository())
    site_store = SiteStore(InMemoryRepository())
    site_store.create(make_site())
    handler = build_seo_run_handler(
        site_store=site_store,
        run_store=runs,
        run_service=RunService(),
        title_workflow=TitleWorkflow(),
        action_store=action_store,
    )

    result = handler({"run_id": "run-1"})
    assert result["action_id"] == "action:run-1"
    stored = action_store.get("action:run-1")
    assert stored.status == ActionStatus.AWAITING_APPROVAL
    assert stored.tenant_id == "principal-1"
    assert stored.observed_value == OLD_TITLE

    # Delivered again after a crash: still one action, untouched.
    runs.run = runs.run.model_copy(update={"status": "failed"})
    handler({"run_id": "run-1"})
    assert action_store.get("action:run-1").history == stored.history
