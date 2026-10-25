"""A SERP provider behind a persistent cache and a daily request budget.

Results for a query move over days, not minutes, and every request is paid
for, so a query is asked at most once per ``ttl`` (a week by default) and
the answer kept in the database, where every worker and restart sees it.

When no fresh answer can be had (today's budget is spent, the provider is
out of credits or rate-limiting), an older answer is better than none, up to
``max_stale`` (a month): it is returned with its own ``fetched_at``. Beyond
that, or with no answer at all, a SERPProviderError is raised for the title
path to record. Only "out of credits" spends the rest of the day's budget; a
rate limit passes in a minute and must not switch the title path off until
midnight.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.keyword_intel.cache import DocumentStore, RequestBudget
from app.keyword_intel.contracts import BudgetExhaustedError
from app.models.serp import SERPQuerySnapshot
from app.normalization.query import normalize_query
from app.serp.live import SERPQuotaError, SERPRateLimitedError
from app.serp.provider import SERPBudgetSpentError


SNAPSHOT_AGGREGATE = "serp_snapshot"
SNAPSHOT_TTL = timedelta(days=7)
MAX_STALE = timedelta(days=30)


class CachedSERPProvider:
    def __init__(
        self,
        provider,
        repository,
        budget: RequestBudget,
        *,
        scope: str,
        ttl: timedelta = SNAPSHOT_TTL,
        max_stale: timedelta = MAX_STALE,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._provider = provider
        self._store = DocumentStore(repository, SNAPSHOT_AGGREGATE)
        self._budget = budget
        # Country and language are part of the answer: a cached Iranian page
        # one must not be served for another market.
        self._scope = scope
        self._ttl = ttl
        self._max_stale = max_stale
        self._clock = clock

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    def search(self, *, query: str) -> SERPQuerySnapshot:
        key = f"{self._provider.provider_id}:{self._scope}:{normalize_query(query) or query}"
        cached = self._store.read(key)
        older = SERPQuerySnapshot.model_validate(cached[0]) if cached is not None else None
        age = self._clock() - older.fetched_at if older is not None else None
        if older is not None and age <= self._ttl:
            return older.model_copy(update={"query": query})

        def fallback(exc: Exception) -> SERPQuerySnapshot:
            if older is not None and age <= self._max_stale:
                return older.model_copy(update={"query": query})
            raise exc

        try:
            self._budget.consume()
        except BudgetExhaustedError as exc:
            return fallback(SERPBudgetSpentError(str(exc)))
        try:
            snapshot = self._provider.search(query=query)
        except SERPQuotaError as exc:
            self._budget.exhaust()
            return fallback(exc)
        except SERPRateLimitedError as exc:
            return fallback(exc)
        self._store.upsert(key, snapshot.model_dump(mode="json"))
        return snapshot
