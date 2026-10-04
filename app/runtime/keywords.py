"""Building the keyword-data service from configuration.

The only place that knows which provider is in use. Adding DataForSEO means
an adapter module beside app/keyword_intel/seosignal.py and one more branch
here; the API and everything after it are unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.keyword_intel.cache import CachedSearchVolume, CombinedBudget, RequestBudget
from app.keyword_intel.contracts import SearchVolumeProvider
from app.models.sites import SecretProvider, SecretRef


class KeywordIntel:
    def __init__(
        self,
        *,
        volumes: SearchVolumeProvider,
        repository,
        daily_budget: int,
        tenant_daily_budget: int,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._volumes = volumes
        self._repository = repository
        self._daily_budget = daily_budget
        self._tenant_daily_budget = tenant_daily_budget
        self._clock = clock

    @property
    def provider_id(self) -> str:
        return self._volumes.provider_id

    def _budget(self, scope: str, limit: int) -> RequestBudget:
        return RequestBudget(self._repository, scope=scope, daily_limit=limit, clock=self._clock)

    def volumes_for(self, tenant_id: str) -> CachedSearchVolume:
        """Volume lookups on behalf of one customer, within their share and everyone's."""
        provider = self._volumes.provider_id
        budget = CombinedBudget(
            self._budget(f"{provider}:tenant:{tenant_id}", self._tenant_daily_budget),
            self._budget(provider, self._daily_budget),
        )
        return CachedSearchVolume(self._volumes, self._repository, budget, clock=self._clock)

    def volumes_for_system(self) -> CachedSearchVolume:
        """Lookups the service makes itself (enriching a run), on the shared budget."""
        provider = self._volumes.provider_id
        return CachedSearchVolume(
            self._volumes, self._repository, self._budget(provider, self._daily_budget),
            clock=self._clock,
        )


def build_keyword_intel(config, *, repository, secret_resolver) -> KeywordIntel | None:
    if config.keyword_provider == "none":
        return None
    if config.keyword_provider == "seosignal":
        from app.keyword_intel.seosignal import SeoSignalClient, SeoSignalSearchVolume

        api_key = secret_resolver.resolve(
            SecretRef(provider=SecretProvider.ENVIRONMENT, key=config.seosignal_api_key_ref)
        )
        return KeywordIntel(
            volumes=SeoSignalSearchVolume(SeoSignalClient(api_key=api_key)),
            repository=repository,
            daily_budget=config.keyword_daily_budget,
            tenant_daily_budget=config.keyword_tenant_daily_budget,
        )
    raise ValueError(f"Unsupported keyword provider: {config.keyword_provider}")
