from app.models.fallback import (
    ProviderFallbackPolicy,
)
from app.models.llm import (
    LLMFailureType,
)


def should_fallback(
    *,
    failure_type: LLMFailureType,
    policy: ProviderFallbackPolicy,
) -> bool:
    if (
        policy.mode.value
        == "disabled"
    ):
        return False

    return (
        failure_type
        in policy.fallback_failure_types
    )


def can_switch_provider(
    *,
    switch_count: int,
    policy: ProviderFallbackPolicy,
) -> bool:
    return (
        switch_count
        < policy.max_provider_switches
    )