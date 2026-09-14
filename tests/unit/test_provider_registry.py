import pytest

from app.models.providers import ProviderRegistration
from app.models.reasoning import (
    TitleReasoningCandidate,
)
from app.reasoning.registry import ProviderRegistry


class FakeReasoner:
    def __init__(
        self,
        provider_id: str,
    ):
        self._provider_id = provider_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def generate_title_candidates(
        self,
        reasoning_input,
    ):
        return []


def make_registration(
    provider_id: str,
    priority: int,
    enabled: bool = True,
) -> ProviderRegistration:
    return ProviderRegistration(
        provider_id=provider_id,
        display_name=provider_id,
        enabled=enabled,
        priority=priority,
        model="test-model",
        config_version="config-v1",
    )


def test_register_and_get_provider():
    registry = ProviderRegistry()

    reasoner = FakeReasoner("arvan_aiaas")

    registry.register(
        registration=make_registration(
            "arvan_aiaas",
            priority=1,
        ),
        reasoner=reasoner,
    )

    assert registry.get(
        "arvan_aiaas"
    ) is reasoner


def test_provider_id_must_match():
    registry = ProviderRegistry()

    reasoner = FakeReasoner("different")

    with pytest.raises(
        ValueError,
        match="must match",
    ):
        registry.register(
            registration=make_registration(
                "arvan_aiaas",
                priority=1,
            ),
            reasoner=reasoner,
        )


def test_duplicate_provider_is_rejected():
    registry = ProviderRegistry()

    reasoner = FakeReasoner("arvan_aiaas")

    registration = make_registration(
        "arvan_aiaas",
        priority=1,
    )

    registry.register(
        registration=registration,
        reasoner=reasoner,
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        registry.register(
            registration=registration,
            reasoner=reasoner,
        )


def test_disabled_provider_cannot_be_selected_by_id():
    registry = ProviderRegistry()

    reasoner = FakeReasoner("arvan_aiaas")

    registry.register(
        registration=make_registration(
            "arvan_aiaas",
            priority=1,
            enabled=False,
        ),
        reasoner=reasoner,
    )

    with pytest.raises(
        ValueError,
        match="disabled",
    ):
        registry.get(
            "arvan_aiaas"
        )


def test_default_provider_uses_priority():
    registry = ProviderRegistry()

    first = FakeReasoner("first")
    second = FakeReasoner("second")

    registry.register(
        registration=make_registration(
            "first",
            priority=10,
        ),
        reasoner=first,
    )

    registry.register(
        registration=make_registration(
            "second",
            priority=1,
        ),
        reasoner=second,
    )

    assert (
        registry.select_default()
        is second
    )


def test_default_provider_uses_provider_id_for_tie():
    registry = ProviderRegistry()

    first = FakeReasoner("z-provider")
    second = FakeReasoner("a-provider")

    registry.register(
        registration=make_registration(
            "z-provider",
            priority=1,
        ),
        reasoner=first,
    )

    registry.register(
        registration=make_registration(
            "a-provider",
            priority=1,
        ),
        reasoner=second,
    )

    assert (
        registry.select_default()
        is second
    )


def test_disabled_provider_is_ignored_for_default():
    registry = ProviderRegistry()

    disabled = FakeReasoner("disabled")
    enabled = FakeReasoner("enabled")

    registry.register(
        registration=make_registration(
            "disabled",
            priority=1,
            enabled=False,
        ),
        reasoner=disabled,
    )

    registry.register(
        registration=make_registration(
            "enabled",
            priority=10,
        ),
        reasoner=enabled,
    )

    assert (
        registry.select_default()
        is enabled
    )


def test_no_enabled_provider_fails():
    registry = ProviderRegistry()

    registry.register(
        registration=make_registration(
            "disabled",
            priority=1,
            enabled=False,
        ),
        reasoner=FakeReasoner("disabled"),
    )

    with pytest.raises(
        ValueError,
        match="No enabled",
    ):
        registry.select_default()


def test_unknown_provider_fails():
    registry = ProviderRegistry()

    with pytest.raises(
        ValueError,
        match="not registered",
    ):
        registry.get(
            "missing"
        )


def test_registrations_are_exposed():
    registry = ProviderRegistry()

    registry.register(
        registration=make_registration(
            "arvan_aiaas",
            priority=1,
        ),
        reasoner=FakeReasoner(
            "arvan_aiaas"
        ),
    )

    registrations = (
        registry.registrations()
    )

    assert len(registrations) == 1
    assert registrations[0].provider_id == (
        "arvan_aiaas"
    )