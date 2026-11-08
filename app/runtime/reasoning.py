"""Reasoning provider construction.

Provider choice is configuration, not domain logic: the registry and router
are built here so that the decision engine never learns which vendor answers.
"""
from __future__ import annotations

from app.models.fallback import ProviderFallbackPolicy
from app.models.provider_config import ArvanAIProviderConfig
from app.models.providers import ProviderRegistration
from app.models.sites import SecretProvider, SecretRef
from app.reasoning.arvan_transport import ArvanTransport
from app.reasoning.provider import StructuredTitleReasoner
from app.reasoning.registry import ProviderRegistry
from app.reasoning.router import ReasoningRouter
from app.reasoning.tiers import TieredTransport


class ReasoningNotConfiguredError(RuntimeError):
    """Raised when reasoning is required but no provider is configured."""


def build_arvan_transport(config, *, secret_resolver, model: str | None = None) -> ArvanTransport:
    """The model's connection, for every caller that asks it something.

    The API key is resolved through the SecretResolver so the raw value never
    reaches configuration objects or persistence.
    """
    if config.llm_mode == "none":
        raise ReasoningNotConfiguredError(
            "SEO_AGENT_LLM_MODE is 'none'; the title path requires a reasoning provider."
        )
    if config.llm_mode != "arvan":
        raise ReasoningNotConfiguredError(f"Unsupported LLM mode: {config.llm_mode}")

    api_key = secret_resolver.resolve(
        SecretRef(
            provider=SecretProvider.ENVIRONMENT,
            key=config.arvan_api_key_ref,
        )
    )
    provider_config = ArvanAIProviderConfig(
        endpoint=config.arvan_endpoint,
        api_key=api_key,
        model=model or config.arvan_model,
        timeout_seconds=config.llm_timeout_seconds,
    )
    return ArvanTransport(config=provider_config)


def build_llm_transport(config, *, secret_resolver, billing=None):
    """The model's connection, by plan when a cheaper model is set for the basic one."""
    best = build_arvan_transport(config, secret_resolver=secret_resolver)
    basic_model = (config.arvan_basic_model or "").strip()
    if not basic_model or billing is None or basic_model == config.arvan_model:
        return best
    return TieredTransport(
        default=best,
        basic=build_arvan_transport(config, secret_resolver=secret_resolver, model=basic_model),
        is_basic=billing.basic_writer,
    )


def build_reasoning_router(config, *, secret_resolver, billing=None):
    """Build a ReasoningRouter from runtime configuration."""
    transport = build_llm_transport(config, secret_resolver=secret_resolver, billing=billing)
    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=transport.max_retries,
    )

    registry = ProviderRegistry()
    registry.register(
        registration=ProviderRegistration(
            provider_id=reasoner.provider_id,
            display_name="ArvanCloud AIaaS",
            priority=1,
            model=reasoner.model,
            config_version=config.reasoning_config_version,
            enabled=True,
        ),
        reasoner=reasoner,
    )
    return ReasoningRouter(
        registry=registry,
        fallback_policy=ProviderFallbackPolicy(),
    )
