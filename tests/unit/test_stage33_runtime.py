from __future__ import annotations

import os
from datetime import date

import pytest

from app.api.auth import APIKeyAuthenticator
from app.models.runs import SEORun
from app.persistence.postgres import PostgresRepository
from app.runtime.config import RuntimeConfig
from app.runtime.container import build_runtime_container
from app.runtime.worker import WorkerHandle
from app.runs.handler import SEO_RUN_JOB_TYPE


def config(**overrides):
    data = {
        "environment": "test",
        "api_key": "super-secret",
        "database_dsn": "postgresql://user:pass@localhost/db",
        "worker_enabled": False,
        "worker_poll_interval_seconds": 0.01,
        "gsc_mode": "stub",
        "observability_enabled": True,
    }
    data.update(overrides)
    return RuntimeConfig(**data)


def test_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("SEO_AGENT_API_KEY", raising=False)
    monkeypatch.setenv("SEO_AGENT_DATABASE_DSN", "postgresql://x")
    with pytest.raises(RuntimeError, match="SEO_AGENT_API_KEY is required"):
        RuntimeConfig.from_environment()


def test_config_rejects_change_me(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_API_KEY", "change-me")
    monkeypatch.setenv("SEO_AGENT_DATABASE_DSN", "postgresql://x")
    with pytest.raises(RuntimeError, match="change-me"):
        RuntimeConfig.from_environment()


def test_config_requires_database_dsn(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_API_KEY", "real-key")
    monkeypatch.delenv("SEO_AGENT_DATABASE_DSN", raising=False)
    with pytest.raises(RuntimeError, match="SEO_AGENT_DATABASE_DSN is required"):
        RuntimeConfig.from_environment()


def test_runtime_container_uses_postgres_repository_and_registers_seo_handler(monkeypatch):
    class FakePostgresRepository(PostgresRepository):
        def __init__(self, dsn):
            self.dsn = dsn
    monkeypatch.setattr("app.runtime.container.PostgresRepository", FakePostgresRepository)
    container = build_runtime_container(config())
    assert isinstance(container.repository, FakePostgresRepository)
    assert container.repository.dsn == config().database_dsn
    assert container.handlers.contains(SEO_RUN_JOB_TYPE)
    assert container.job_store._repository is container.repository
    assert container.site_store._repository is container.repository
    assert container.run_store._repository is container.repository


def test_runtime_container_builds_real_seo_dependency_graph(monkeypatch):
    class FakePostgresRepository(PostgresRepository):
        def __init__(self, dsn):
            self.dsn = dsn
    monkeypatch.setattr("app.runtime.container.PostgresRepository", FakePostgresRepository)
    container = build_runtime_container(config())
    assert container.scheduler.store is container.job_store
    assert container.worker.scheduler is container.scheduler


def test_live_gsc_mode_builds_live_service(monkeypatch):
    class FakePostgresRepository(PostgresRepository):
        def __init__(self, dsn):
            self.dsn = dsn
    monkeypatch.setattr("app.runtime.container.PostgresRepository", FakePostgresRepository)
    seen = []
    class LiveService:
        def __init__(self, **kwargs): seen.append(kwargs)
    monkeypatch.setattr("app.runtime.container.PersistentSEORunService", LiveService)
    container = build_runtime_container(config(gsc_mode="live"))
    assert len(seen) == 1
    assert seen[0]["repository"] is container.repository
    assert container.handlers.contains(SEO_RUN_JOB_TYPE)


def test_worker_start_and_stop(monkeypatch):
    calls = []
    class FakeScheduler:
        def run_forever(self, *, poll_interval_seconds, stop_event):
            calls.append(poll_interval_seconds)
            stop_event.wait(0.2)
    worker = WorkerHandle(scheduler=FakeScheduler(), poll_interval_seconds=0.01)
    worker.start()
    worker.stop(timeout_seconds=1)
    assert calls == [0.01]
    assert not worker.running


def test_worker_start_is_idempotent(monkeypatch):
    class FakeScheduler:
        def run_forever(self, *, poll_interval_seconds, stop_event):
            stop_event.wait(0.2)
    worker = WorkerHandle(scheduler=FakeScheduler(), poll_interval_seconds=0.01)
    worker.start()
    worker.start()
    worker.stop(timeout_seconds=1)
    assert not worker.running


def test_authenticator_accepts_runtime_key():
    auth = APIKeyAuthenticator(config().api_key)
    from fastapi.security import HTTPAuthorizationCredentials
    assert auth.authenticate(HTTPAuthorizationCredentials(scheme="Bearer", credentials="super-secret")) == "api-key"


def test_config_parses_worker_and_gsc_mode(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_API_KEY", "real-key")
    monkeypatch.setenv("SEO_AGENT_DATABASE_DSN", "postgresql://x")
    monkeypatch.setenv("SEO_AGENT_WORKER_ENABLED", "false")
    monkeypatch.setenv("SEO_AGENT_WORKER_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("SEO_AGENT_GSC_MODE", "stub")
    result = RuntimeConfig.from_environment()
    assert result.worker_enabled is False
    assert result.worker_poll_interval_seconds == 2.5
    assert result.gsc_mode == "stub"
