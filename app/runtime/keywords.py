"""Building the keyword-data services from configuration.

The only place that knows which provider is in use. Adding DataForSEO means
an adapter module beside app/keyword_intel/seosignal.py and one more branch
here; the API and everything after it are unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.keyword_intel.cache import (
    TEHRAN,
    CachedRankTracker,
    CachedSearchVolume,
    CombinedBudget,
    RequestBudget,
)
from app.keyword_intel.contracts import RankTrackerProvider, SearchVolumeProvider
from app.models.keyword_intel import Device, RankHistory
from app.models.sites import SecretProvider, SecretRef
from app.net.guard import same_site


class KeywordIntel:
    def __init__(
        self,
        *,
        volumes: SearchVolumeProvider,
        ranks: RankTrackerProvider | None,
        repository,
        daily_budget: int,
        tenant_daily_budget: int,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._volumes = volumes
        self._ranks = CachedRankTracker(ranks, repository, clock=clock) if ranks else None
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

    def rankings_for_site(self, site_url: str, *, device: Device, days: int) -> RankHistory | None:
        """The tracked rankings of the project on this site's domain, if there is one."""
        if self._ranks is None:
            return None
        project = next(
            (
                item for item in self._ranks.projects()
                if item.active and same_site(f"https://{item.domain}/", site_url)
            ),
            None,
        )
        if project is None:
            return None
        end = self._clock().astimezone(TEHRAN).date()
        return self._ranks.rank_history(
            project_id=project.project_id,
            device=device,
            start=end - timedelta(days=days - 1),
            end=end,
            domain=project.domain,
        )


def build_keyword_intel(config, *, repository, secret_resolver) -> KeywordIntel | None:
    if config.keyword_provider == "none":
        return None
    if config.keyword_provider == "seosignal":
        from app.keyword_intel.seosignal import (
            SeoSignalClient,
            SeoSignalRankTracker,
            SeoSignalSearchVolume,
        )

        api_key = secret_resolver.resolve(
            SecretRef(provider=SecretProvider.ENVIRONMENT, key=config.seosignal_api_key_ref)
        )
        client = SeoSignalClient(api_key=api_key)
        return KeywordIntel(
            volumes=SeoSignalSearchVolume(client),
            ranks=SeoSignalRankTracker(client),
            repository=repository,
            daily_budget=config.keyword_daily_budget,
            tenant_daily_budget=config.keyword_tenant_daily_budget,
        )
    raise ValueError(f"Unsupported keyword provider: {config.keyword_provider}")
