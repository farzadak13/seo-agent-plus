"""Stage 36 closure: the title workflow must reach the real run handler.

The Stage 36 handler test proved the handler attaches a proposal when a
workflow is passed. Nothing proved the composition root passes one, so the
whole title path could be — and was — absent at runtime while tests stayed
green. These tests assert the wiring itself.
"""
from __future__ import annotations

import json

import pytest

from app.persistence.postgres import PostgresRepository
from app.runtime.config import RuntimeConfig
from app.runtime.reasoning import ReasoningNotConfiguredError, build_reasoning_router
from app.runtime.serp import SERPNotConfiguredError, build_serp_provider
from app.runtime.title import build_title_workflow
from app.runs.handler import SEO_RUN_JOB_TYPE
from app.title.workflow import TitleRecommendationWorkflow


SNAPSHOT_PAYLOAD = {
    "کفش مردانه": {
        "query": "کفش مردانه",
        "provider": "static",
        "response_id": "serp:1",
        "fetched_at": "2026-09-01T00:00:00+00:00",
        "results": [
            {"position": 1, "url": "https://a.example/1", "title": "خرید کفش مردانه"},
            {"position": 2, "url": "https://b.example/2", "title": "قیمت کفش مردانه"},
            {"position": 3, "url": "https://c.example/3", "title": "بهترین کفش مردانه"},
        ],
    }
}


class FakePostgresRepository(PostgresRepository):
    def __init__(self, dsn):
        self.dsn = dsn


class FakeAdapterFactory:
    """Stands in for SiteAdapterFactory; only ``build`` is needed to wire."""

    def build(self, site):
        raise AssertionError("The adapter is resolved per run, not at wiring time.")


def base_config(**overrides):
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


def title_config(snapshot_path, **overrides):
    data = {
        "title_workflow_enabled": True,
        "serp_mode": "static",
        "serp_static_path": str(snapshot_path),
        "llm_mode": "arvan",
        "arvan_endpoint": "https://ai.example/v1",
        "arvan_model": "test-model",
        "arvan_api_key_ref": "TEST_ARVAN_KEY",
    }
    data.update(overrides)
    return base_config(**data)


@pytest.fixture
def snapshot_file(tmp_path):
    path = tmp_path / "serp.json"
    path.write_text(json.dumps(SNAPSHOT_PAYLOAD, ensure_ascii=False), encoding="utf-8")
    return path


# --- configuration: the title path is wholly on or wholly off ---------------


def test_enabled_title_workflow_requires_a_serp_source():
    with pytest.raises(ValueError, match="SEO_AGENT_SERP_MODE"):
        base_config(title_workflow_enabled=True, llm_mode="none")


def test_enabled_title_workflow_requires_a_reasoning_provider(snapshot_file):
    with pytest.raises(ValueError, match="SEO_AGENT_LLM_MODE"):
        base_config(
            title_workflow_enabled=True,
            serp_mode="static",
            serp_static_path=str(snapshot_file),
            llm_mode="none",
        )


def test_static_serp_mode_requires_a_snapshot_path():
    with pytest.raises(ValueError, match="SEO_AGENT_SERP_STATIC_PATH"):
        base_config(serp_mode="static")


def test_arvan_mode_requires_endpoint_model_and_key_reference():
    with pytest.raises(ValueError, match="SEO_AGENT_ARVAN_ENDPOINT"):
        base_config(llm_mode="arvan")


def test_default_config_leaves_the_title_path_off():
    config = base_config()
    assert config.title_workflow_enabled is False
    assert config.serp_mode == "none"
    assert config.llm_mode == "none"


# --- collaborators fail loudly rather than degrading ------------------------


def test_serp_provider_refuses_to_build_when_unconfigured():
    with pytest.raises(SERPNotConfiguredError, match="SEO_AGENT_SERP_MODE"):
        build_serp_provider(base_config())


def test_serp_provider_reports_a_missing_snapshot_file(tmp_path):
    config = base_config(serp_mode="static", serp_static_path=str(tmp_path / "absent.json"))
    with pytest.raises(SERPNotConfiguredError, match="not found"):
        build_serp_provider(config)


def test_static_serp_provider_returns_the_configured_snapshot(snapshot_file):
    provider = build_serp_provider(
        base_config(serp_mode="static", serp_static_path=str(snapshot_file))
    )
    snapshot = provider.search(query="کفش مردانه")
    assert snapshot.provider == "static"
    assert len(snapshot.results) == 3


def test_reasoning_router_refuses_to_build_when_unconfigured():
    with pytest.raises(ReasoningNotConfiguredError, match="SEO_AGENT_LLM_MODE"):
        build_reasoning_router(base_config(), secret_resolver=None)


def test_reasoning_router_resolves_the_key_through_the_secret_resolver(snapshot_file, monkeypatch):
    monkeypatch.setenv("TEST_ARVAN_KEY", "arvan-secret")
    from app.onboarding.secrets import EnvironmentSecretResolver

    router = build_reasoning_router(
        title_config(snapshot_file), secret_resolver=EnvironmentSecretResolver()
    )
    registrations = router._registry.registrations()
    assert [item.provider_id for item in registrations] == ["arvan_aiaas"]
    assert registrations[0].model == "test-model"


def test_missing_reasoning_secret_fails_instead_of_disabling_the_path(snapshot_file, monkeypatch):
    monkeypatch.delenv("TEST_ARVAN_KEY", raising=False)
    from app.onboarding.secrets import SecretResolutionError
    from app.onboarding.secrets import EnvironmentSecretResolver

    with pytest.raises(SecretResolutionError):
        build_title_workflow(
            title_config(snapshot_file),
            repository=FakePostgresRepository("postgresql://x"),
            adapter_factory=FakeAdapterFactory(),
            secret_resolver=EnvironmentSecretResolver(),
        )


def test_disabled_title_workflow_needs_no_collaborators():
    assert (
        build_title_workflow(
            base_config(),
            repository=FakePostgresRepository("postgresql://x"),
            adapter_factory=FakeAdapterFactory(),
            secret_resolver=None,
        )
        is None
    )


# --- the composition root actually passes the workflow ----------------------


def test_container_passes_the_title_workflow_to_the_run_handler(snapshot_file, monkeypatch):
    monkeypatch.setenv("TEST_ARVAN_KEY", "arvan-secret")
    monkeypatch.setattr("app.runtime.container.PostgresRepository", FakePostgresRepository)

    captured = {}
    import app.runtime.container as container_module

    original = container_module.build_seo_run_handler

    def spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(container_module, "build_seo_run_handler", spy)

    container = container_module.build_runtime_container(title_config(snapshot_file))

    assert isinstance(captured["title_workflow"], TitleRecommendationWorkflow)
    assert captured["title_workflow"] is container.title_workflow
    assert container.handlers.contains(SEO_RUN_JOB_TYPE)


def test_container_leaves_the_workflow_absent_when_disabled(monkeypatch):
    monkeypatch.setattr("app.runtime.container.PostgresRepository", FakePostgresRepository)

    captured = {}
    import app.runtime.container as container_module

    original = container_module.build_seo_run_handler

    def spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(container_module, "build_seo_run_handler", spy)

    container = container_module.build_runtime_container(base_config())

    assert "title_workflow" in captured
    assert captured["title_workflow"] is None
    assert container.title_workflow is None


def test_title_workflow_uses_the_site_adapter_factory(snapshot_file, monkeypatch):
    monkeypatch.setenv("TEST_ARVAN_KEY", "arvan-secret")
    monkeypatch.setattr("app.runtime.container.PostgresRepository", FakePostgresRepository)
    import app.runtime.container as container_module

    container = container_module.build_runtime_container(title_config(snapshot_file))

    assert container.title_workflow._adapter_resolver == container.adapters.build
    assert container.title_workflow._serp_provider.provider_id == "static"
