from __future__ import annotations

from dataclasses import dataclass

from app.action.executor import (
    EXECUTE_ACTION_JOB_TYPE,
    ROLLBACK_ACTION_JOB_TYPE,
    build_execute_action_handler,
    build_rollback_action_handler,
)
from app.action.store import ManagedActionStore
from app.api.app import APIDependencies, create_app
from app.api.auth import TenantAPIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.observability import InMemoryEventSink, InMemoryMetricsSink, ObservabilityContext
from app.onboarding.secrets import EnvironmentSecretResolver
from app.onboarding.google_oauth import GoogleOAuthConfig, GoogleOAuthService
from app.runtime.keywords import KeywordIntel, build_keyword_intel
from app.runtime.warehouse import WarehouseLoop, build_warehouse
from app.warehouse.store import PostgresWarehouse
from app.onboarding.ownership import OwnershipVerifier
from app.onboarding.vault import CompositeSecretResolver, Keyring, SecretVault
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
from app.runtime.gsc import build_google_transport
from app.runtime.properties import build_property_lister
from app.runtime.service import PersistentSEORunService
from app.runtime.adapters import SiteAdapterFactory
from app.runtime.title import build_title_workflow
from app.tenancy.store import APIKeyStore, TenantStore


@dataclass
class RuntimeContainer:
    config: RuntimeConfig
    repository: PostgresRepository
    job_store: JobStore
    site_store: SiteStore
    tenant_store: TenantStore
    api_key_store: APIKeyStore
    run_store: RunStore
    handlers: JobHandlerRegistry
    scheduler: JobScheduler
    observability: ObservabilityContext
    worker: WorkerHandle
    adapters: SiteAdapterFactory
    action_store: ManagedActionStore
    vault: SecretVault | None = None
    google_oauth: GoogleOAuthService | None = None
    keywords: KeywordIntel | None = None
    warehouse: PostgresWarehouse | None = None
    warehouse_loop: WarehouseLoop | None = None
    title_workflow: object | None = None
    gsc_property_lister: object | None = None

    def start(self) -> None:
        if self.config.worker_enabled:
            self.worker.start()
            # Only beside the worker: the worker's lease is what makes this
            # the single process, so two servers never sync the same day.
            if self.warehouse_loop is not None:
                self.warehouse_loop.start()

    def stop(self) -> None:
        if self.warehouse_loop is not None:
            self.warehouse_loop.stop()
        self.worker.stop()


def build_runtime_container(config: RuntimeConfig) -> RuntimeContainer:
    repository = PostgresRepository(config.database_dsn)
    job_store = JobStore(repository)
    site_store = SiteStore(repository)
    run_store = RunStore(repository)
    tenant_store = TenantStore(repository)
    api_key_store = APIKeyStore(repository)
    action_store = ManagedActionStore(repository)

    keyring = Keyring.from_environment()
    vault = SecretVault(repository, keyring) if keyring is not None else None
    secret_resolver = CompositeSecretResolver(EnvironmentSecretResolver(), vault)

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

    gsc_property_lister = None
    transport = None
    if config.gsc_mode == "live":
        # Built here rather than per run: one session, one connection pool, and
        # one place where the egress proxy is configured. Token minting shares
        # it, so a token is never issued to a different address than the one
        # the data is fetched from.
        transport = build_google_transport(config)
        run_service = PersistentSEORunService(
            repository=repository,
            secret_resolver=secret_resolver,
            settings=config,
            transport=transport,
        )
        # Onboarding asks Google which properties a credential can see, so the
        # customer picks one instead of typing a string that has to match byte
        # for byte. In stub mode there is nothing to ask, and the property is
        # taken on trust as before.
        gsc_property_lister = build_property_lister(
            config, secret_resolver=secret_resolver, transport=transport
        )
    adapters = SiteAdapterFactory(secret_resolver)
    keywords = build_keyword_intel(config, repository=repository, secret_resolver=secret_resolver)
    warehouse, warehouse_runner = build_warehouse(
        config, site_store=site_store, secret_resolver=secret_resolver,
        transport=transport,
    )
    # "worker" is bound further down; the lambda reads it only once running.
    warehouse_loop = (
        WarehouseLoop(warehouse_runner, should_run=lambda: worker.running)
        if warehouse_runner is not None
        else None
    )

    google_oauth = None
    if config.google_sign_in_enabled:
        if vault is None:
            # The refresh tokens it receives must be stored encrypted.
            raise RuntimeError(
                "Google sign-in needs SEO_AGENT_SECRET_KEYS to store customers' tokens."
            )
        google_oauth = GoogleOAuthService(
            config=GoogleOAuthConfig(
                client_id=config.google_oauth_client_id,
                client_secret=config.google_oauth_client_secret,
                public_base_url=config.public_base_url,
            ),
            repository=repository,
            vault=vault,
        )

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
            action_store=action_store,
        ),
    )
    # Stage 38: approved changes are carried out by the same worker as runs.
    handlers.register(
        EXECUTE_ACTION_JOB_TYPE,
        build_execute_action_handler(
            action_store=action_store, site_store=site_store, adapter_factory=adapters
        ),
    )
    handlers.register(
        ROLLBACK_ACTION_JOB_TYPE,
        build_rollback_action_handler(
            action_store=action_store, site_store=site_store, adapter_factory=adapters
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
        tenant_store=tenant_store,
        api_key_store=api_key_store,
        run_store=run_store,
        handlers=handlers,
        scheduler=scheduler,
        observability=observability,
        worker=worker,
        adapters=adapters,
        action_store=action_store,
        vault=vault,
        google_oauth=google_oauth,
        keywords=keywords,
        warehouse=warehouse,
        warehouse_loop=warehouse_loop,
        title_workflow=title_workflow,
        gsc_property_lister=gsc_property_lister,
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
            # The configured key authenticates as the tenant "admin" and is
            # confined to that tenant's own rows like any other. Customer keys
            # come from the database, one tenant each; no key sees them all.
            authenticator=TenantAPIKeyAuthenticator(
                key_store=container.api_key_store,
                tenant_store=container.tenant_store,
                admin_key=resolved_config.api_key,
            ),
            site_store=container.site_store,
            run_store=container.run_store,
            transaction_factory=container.repository.transaction,
            adapter_factory=container.adapters,
            gsc_property_lister=container.gsc_property_lister,
            action_store=container.action_store,
            vault=container.vault,
            ownership_verifier=OwnershipVerifier(adapter_factory=container.adapters),
            google_oauth=container.google_oauth,
            keywords=container.keywords,
            warehouse=container.warehouse,
            tenant_name=lambda tenant_id: getattr(
                container.tenant_store.find(tenant_id), "name", tenant_id
            ),
        )
    )
    app.router.lifespan_context = lifespan
    app.state.runtime = container

    @app.get("/readyz")
    def readiness():
        from fastapi import HTTPException
        try:
            # A liveness probe must not scan the record table; ping is O(1).
            container.repository.ping()
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
