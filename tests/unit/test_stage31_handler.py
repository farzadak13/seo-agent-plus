from datetime import date

from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.pipeline import PipelineStatus
from app.models.sites import GSCConnectionConfig, SecretRef, Site
from app.models.runs import SEORun
from app.onboarding.secrets import EnvironmentSecretResolver
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runs.handler import SEO_RUN_JOB_TYPE, build_seo_run_handler
from app.runs.service import SEORunService
from app.runs.store import RunStore


class FakePipelineResult:
    status = PipelineStatus.COMPLETED

    def model_dump(self, mode="json"):
        return {"status": self.status.value}


class FakeGateway:
    def fetch(self, **kwargs):
        from app.models.gsc import RawGSCResponse
        from datetime import datetime, timezone
        return RawGSCResponse(
            response_id="response-1",
            site_id=kwargs["site_id"],
            fetch_date=kwargs["end_date"],
            raw_payload={"rows": []},
            created_at=datetime.now(timezone.utc),
        )


class FakeBaseline:
    def load(self, **kwargs):
        return [], []


class FakeRunner:
    def run(self, **kwargs):
        return FakePipelineResult()


def setup():
    repository = InMemoryRepository()
    site_store = SiteStore(repository)
    run_store = RunStore(repository)
    service = SEORunService(
        secret_resolver=EnvironmentSecretResolver(),
        gsc_gateway=FakeGateway(),
        baseline_provider=FakeBaseline(),
        pipeline_runner=FakeRunner(),
    )
    registry = JobHandlerRegistry()
    registry.register(
        SEO_RUN_JOB_TYPE,
        build_seo_run_handler(
            site_store=site_store,
            run_store=run_store,
            run_service=service,
        ),
    )
    scheduler = JobScheduler(store=JobStore(repository), handlers=registry)
    return site_store, run_store, scheduler


def test_seo_run_handler_updates_run_and_returns_pipeline_status(monkeypatch):
    monkeypatch.setenv("GSC_TOKEN", "secret")
    site_store, run_store, scheduler = setup()
    site = Site(
        site_id="site-001",
        principal_id="principal-1",
        name="Example",
        base_url="https://example.com",
        gsc=GSCConnectionConfig(
            property_url="https://example.com",
            credential_ref=SecretRef(key="GSC_TOKEN"),
        ),
    )
    site_store.create(site)
    run = SEORun(
        run_id="run-001",
        site_id=site.site_id,
        principal_id=site.principal_id,
        job_id="job-001",
        start_date=date(2026, 9, 1).isoformat(),
        end_date=date(2026, 9, 12).isoformat(),
        normalized_url="https://example.com/page",
        normalized_query="کفش",
        candidate_id="candidate-1",
    )
    run_store.create(run)
    scheduler.store.create(
        __import__("app.models.jobs", fromlist=["Job"]).Job(
            job_id="job-001",
            job_type=SEO_RUN_JOB_TYPE,
            principal_id="principal-1",
            payload={"run_id": "run-001"},
            max_attempts=1,
        )
    )

    result = scheduler.run_once()

    assert result is not None
    assert result.status.value == "completed"
    assert run_store.get("run-001").status.value == "completed"
    assert run_store.get("run-001").result == {"status": "completed"}
