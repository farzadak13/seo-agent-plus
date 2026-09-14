from app.models.providers import ProviderRegistration
from app.reasoning.contracts import StrategicReasoner


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[
            str,
            tuple[
                ProviderRegistration,
                StrategicReasoner,
            ],
        ] = {}

    def register(
        self,
        *,
        registration: ProviderRegistration,
        reasoner: StrategicReasoner,
    ) -> None:
        if (
            registration.provider_id
            != reasoner.provider_id
        ):
            raise ValueError(
                "Provider registration ID must match "
                "the reasoner provider ID."
            )

        if registration.provider_id in self._providers:
            raise ValueError(
                "Provider is already registered: "
                f"{registration.provider_id}"
            )

        self._providers[
            registration.provider_id
        ] = (
            registration,
            reasoner,
        )

    def get(
        self,
        provider_id: str,
    ) -> StrategicReasoner:
        try:
            registration, reasoner = (
                self._providers[provider_id]
            )
        except KeyError as exc:
            raise ValueError(
                f"Provider is not registered: "
                f"{provider_id}"
            ) from exc

        if not registration.enabled:
            raise ValueError(
                f"Provider is disabled: "
                f"{provider_id}"
            )

        return reasoner

    def select_default(
        self,
    ) -> StrategicReasoner:
        enabled = [
            item
            for item in self._providers.values()
            if item[0].enabled
        ]

        if not enabled:
            raise ValueError(
                "No enabled reasoning provider is registered."
            )

        enabled.sort(
            key=lambda item: (
                item[0].priority,
                item[0].provider_id,
            )
        )

        return enabled[0][1]

    def registrations(
        self,
    ) -> list[ProviderRegistration]:
        return [
            registration
            for registration, _ in self._providers.values()
        ]