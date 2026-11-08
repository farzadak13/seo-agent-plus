from datetime import datetime, timezone
from types import SimpleNamespace

from app.billing.plans import PlanId, catalog
from app.billing.service import Billing
from app.billing.store import PaymentStore, SubscriptionStore
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.jobs import Job
from app.persistence.memory import InMemoryRepository
from app.reasoning.tiers import TieredTransport, acting_for
from app.runtime.reasoning import build_llm_transport


class Model:
    def __init__(self, name):
        self.model = name
        self.provider_id = "arvan_aiaas"
        self.max_retries = 2
        self.calls = 0
        self.last_usage = {"model": name}
        self.last_response_metadata = {}

    def complete(self, *, messages):
        self.calls += 1
        return self.model


def tiered(basic_tenants=("t-basic",), broken=False):
    best, cheap = Model("Claude-Opus-4.6"), Model("Gemini-3-Flash-Preview")

    def is_basic(tenant):
        if broken:
            raise RuntimeError("billing is down")
        return tenant in basic_tenants

    return TieredTransport(default=best, basic=cheap, is_basic=is_basic), best, cheap


def test_the_basic_plan_is_written_by_the_cheaper_model_and_everyone_else_by_the_best():
    transport, best, cheap = tiered()
    with acting_for("t-basic"):
        assert transport.complete(messages=[]) == "Gemini-3-Flash-Preview"
        assert transport.model == "Gemini-3-Flash-Preview"
        assert transport.last_usage == {"model": "Gemini-3-Flash-Preview"}
    with acting_for("t-gold"):
        assert transport.complete(messages=[]) == "Claude-Opus-4.6"
    assert transport.complete(messages=[]) == "Claude-Opus-4.6"  # no job around the call
    assert (best.calls, cheap.calls) == (2, 1)


def test_when_the_plan_cannot_be_read_the_best_model_writes():
    transport, best, _ = tiered(broken=True)
    with acting_for("t-basic"):
        assert transport.complete(messages=[]) == "Claude-Opus-4.6"


def test_a_job_runs_as_its_tenant():
    repository = InMemoryRepository()
    transport, _, cheap = tiered()
    handlers = JobHandlerRegistry()
    handlers.register("write", lambda payload: {"text": transport.complete(messages=[])})
    scheduler = JobScheduler(store=JobStore(repository), handlers=handlers)
    scheduler.store.create(Job(job_id="j1", job_type="write", principal_id="t-basic", payload={}))
    done = scheduler.run_once()
    assert done.result == {"text": "Gemini-3-Flash-Preview"} and cheap.calls == 1


def billing():
    repository = InMemoryRepository()
    return Billing(
        subscriptions=SubscriptionStore(repository), payments=PaymentStore(repository), plans=catalog(),
        gateway=None, callback_url=None, clock=lambda: datetime.now(timezone.utc),
    )


def test_trial_and_basic_get_the_cheaper_model_gold_and_operator_tenants_the_best():
    plans = catalog()
    assert plans[PlanId.TRIAL].basic_writer and plans[PlanId.BASIC].basic_writer and not plans[PlanId.GOLD].basic_writer
    assert (plans[PlanId.BASIC].price, plans[PlanId.GOLD].price) == (890_000, 3_500_000)
    service = billing()
    assert service.basic_writer("t-operator") is False  # no subscription: set up by hand, as before
    service.start_trial("t-new")
    assert service.basic_writer("t-new") is True


def config(basic_model):
    return SimpleNamespace(
        llm_mode="arvan", arvan_api_key_ref="KEY", arvan_endpoint="https://ai.example/v1",
        arvan_model="Claude-Opus-4.6", arvan_basic_model=basic_model, llm_timeout_seconds=30,
    )


class Secrets:
    def resolve(self, ref):
        return "secret"


def test_one_model_for_all_unless_a_cheaper_one_is_set():
    assert build_llm_transport(config(None), secret_resolver=Secrets(), billing=billing()).model == "Claude-Opus-4.6"
    assert build_llm_transport(config("Gemini-3-Flash-Preview"), secret_resolver=Secrets(), billing=None).model == "Claude-Opus-4.6"
    tiers = build_llm_transport(config("Gemini-3-Flash-Preview"), secret_resolver=Secrets(), billing=billing())
    assert isinstance(tiers, TieredTransport)
