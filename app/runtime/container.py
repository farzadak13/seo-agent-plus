from __future__ import annotations

from dataclasses import dataclass

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.observability import InMemoryEventSink, InMemoryMetricsSink, ObservabilityContext
from app.onboarding.secrets import EnvironmentSecretResolver
from app.onboarding.site_store import SiteStore
from app.persistence.postgres import PostgresRepository
from app.runs.development import EmptyBaselineProvider, StubGSCGateway
from app.runs.handler import SEO_RUN_JOB_TYPE, build_seo_run_handler
from app.runs.runner import DeterministicPipelineRunner
from app.runs.service import SEORunService
from app.runs.store import RunStore
from app.runtime.config import RuntimeConfig
from app.runtime.worker import WorkerHandle
from app.runtime.lease import postgres_worker_lease
from app.runtime.service import PersistentSEORunService
from app.runtime.adapters import SiteAdapterFactory
from app.runtime.title import build_title_workflow


@dataclass
class RuntimeContainer:
    config: RuntimeConfig
    repository: PostgresRepository
    job_store: JobStore
    site_store: SiteStore
    run_store: RunStore
    handlers: JobHandlerRegistry
    scheduler: JobScheduler
    observability: ObservabilityContext
    worker: WorkerHandle
    adapters: SiteAdapterFactory
    title_workflow: object | None = None

    def start(self) -> None:
        if self.config.worker_enabled:
            self.worker.start()

    def stop(self) -> None:
        self.worker.stop()


def build_runtime_container(config: RuntimeConfig) -> RuntimeContainer:
    repository = PostgresRepository(config.database_dsn)
    job_store = JobStore(repository)
    site_store = SiteStore(repository)
    run_store = RunStore(repository)

    secret_resolver = EnvironmentSecretResolver()

    observability = ObservabilityContext(
        event_sink=InMemoryEventSink(),
        metrics_sink=InMemoryMetricsSink(),
        correlation_id="application-worker",
    )

    run_service = SEORunService(
        secret_resolver=secret_resolver,
        gsc_gateway=StubGSCGateway(),
        baseline_provider=EmptyBaselineProvider(),
        pipeline_runner=DeterministicPipelineRunner(),
    )

    if config.gsc_mode == "live":
        run_service = PersistentSEORunService(
            repository=repository, secret_resolver=secret_resolver, settings=config,
        )
    adapters = SiteAdapterFactory(secret_resolver)

    # Stage 36: the title path is wired here or not at all. An enabled workflow
    # that cannot be built must fail startup rather than let runs report success
    # while silently producing no proposal.
    title_workflow = build_title_workflow(
        config,
        repository=repository,
        adapter_factory=adapters,
        secret_resolver=secret_resolver,
    )

    handlers = JobHandlerRegistry()
    handlers.register(
        SEO_RUN_JOB_TYPE,
        build_seo_run_handler(
            site_store=site_store,
            run_store=run_store,
            run_service=run_service,
            title_workflow=title_workflow,
        ),
    )

    scheduler = JobScheduler(
        store=job_store,
        handlers=handlers,
        observability=observability if config.observability_enabled else None,
    )
    worker = WorkerHandle(
        scheduler=scheduler,
        poll_interval_seconds=config.worker_poll_interval_seconds,
        lease_factory=lambda: postgres_worker_lease(repository),
    )
    return RuntimeContainer(
        config=config,
        repository=repository,
        job_store=job_store,
        site_store=site_store,
        run_store=run_store,
        handlers=handlers,
        scheduler=scheduler,
        observability=observability,
        worker=worker,
        adapters=adapters,
        title_workflow=title_workflow,
    )


def create_runtime_app(config: RuntimeConfig | None = None):
    """Build the FastAPI app and wire startup/shutdown to the real worker."""
    from contextlib import asynccontextmanager

    resolved_config = config or RuntimeConfig.from_environment()
    container = build_runtime_container(resolved_config)

    @asynccontextmanager
    async def lifespan(app):
        container.start()
        try:
            yield
        finally:
            container.stop()

    app = create_app(
        APIDependencies(
            scheduler=container.scheduler,
            authenticator=APIKeyAuthenticator(resolved_config.api_key),
            site_store=container.site_store,
            run_store=container.run_store,
            transaction_factory=container.repository.transaction,
            adapter_factory=container.adapters,
        )
    )
    app.router.lifespan_context = lifespan
    app.state.runtime = container

    @app.get("/readyz")
    def readiness():
        from fastapi import HTTPException
        try:
            container.repository.list(limit=1)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Persistence unavailable.") from exc
        if resolved_config.worker_enabled and not container.worker.running:
            raise HTTPException(status_code=503, detail="Worker is not running.")
        return {
            "status": "ready",
            "gsc_mode": resolved_config.gsc_mode,
            "title_workflow": "enabled" if container.title_workflow is not None else "disabled",
            "serp_mode": resolved_config.serp_mode,
            "llm_mode": resolved_config.llm_mode,
        }

    return app
