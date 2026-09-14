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


class ReasoningNotConfiguredError(RuntimeError):
    """Raised when reasoning is required but no provider is configured."""


def build_reasoning_router(config, *, secret_resolver):
    """Build a ReasoningRouter from runtime configuration.

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
        model=config.arvan_model,
        timeout_seconds=config.llm_timeout_seconds,
    )
    transport = ArvanTransport(config=provider_config)
    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=provider_config.max_retries,
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
