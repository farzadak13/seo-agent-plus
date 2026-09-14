import pytest

from app.models.fallback import (
    FallbackPolicyMode,
    ProviderFallbackPolicy,
)
from app.models.llm import (
    LLMFailureType,
)
from app.reasoning.fallback import (
    can_switch_provider,
    should_fallback,
)


def test_timeout_allows_fallback():
    policy = ProviderFallbackPolicy()

    assert should_fallback(
        failure_type=LLMFailureType.TIMEOUT,
        policy=policy,
    ) is True


def test_rate_limit_allows_fallback():
    policy = ProviderFallbackPolicy()

    assert should_fallback(
        failure_type=LLMFailureType.RATE_LIMIT,
        policy=policy,
    ) is True


def test_transport_allows_fallback():
    policy = ProviderFallbackPolicy()

    assert should_fallback(
        failure_type=LLMFailureType.TRANSPORT,
        policy=policy,
    ) is True


def test_authentication_does_not_fallback():
    policy = ProviderFallbackPolicy()

    assert should_fallback(
        failure_type=LLMFailureType.AUTHENTICATION,
        policy=policy,
    ) is False


def test_invalid_schema_does_not_fallback():
    policy = ProviderFallbackPolicy()

    assert should_fallback(
        failure_type=LLMFailureType.INVALID_SCHEMA,
        policy=policy,
    ) is False


def test_disabled_policy_prevents_fallback():
    policy = ProviderFallbackPolicy(
        mode=FallbackPolicyMode.DISABLED,
    )

    assert should_fallback(
        failure_type=LLMFailureType.TIMEOUT,
        policy=policy,
    ) is False


def test_switch_limit_is_respected():
    policy = ProviderFallbackPolicy(
        max_provider_switches=1,
    )

    assert can_switch_provider(
        switch_count=0,
        policy=policy,
    ) is True

    assert can_switch_provider(
        switch_count=1,
        policy=policy,
    ) is False


def test_zero_switches_disables_provider_switching():
    policy = ProviderFallbackPolicy(
        max_provider_switches=0,
    )

    assert can_switch_provider(
        switch_count=0,
        policy=policy,
    ) is False


def test_negative_switch_limit_is_rejected():
    with pytest.raises(ValueError):
        ProviderFallbackPolicy(
            max_provider_switches=-1,
        )