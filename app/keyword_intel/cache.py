"""Persistent cache and daily request budget, wrapping any provider.

Search volume is monthly data and the provider allows a few dozen requests a
day, so a keyword is asked about at most once a month and only in bulk:

* every answer is stored, including "no data", for ``VOLUME_TTL`` — or for
  ``UNCONFIRMED_TTL`` when the provider found nothing for a whole request,
  which is too unsure an answer to keep for a month;
* only keywords without a fresh answer are sent, as few requests as the
  provider's bulk size allows;
* each request is counted against a daily budget kept in the database, so
  restarts and several workers share one count, and the budget refuses before
  the provider has to;
* when the budget is spent, keywords with an older answer get that answer
  (its ``fetched_at`` says how old) and the rest are reported as pending.

Days are counted in Tehran time because that is when the provider's counter
resets. Iran has not observed daylight saving since 2022, so a fixed offset is
exact and needs no time-zone database on the server.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

from app.keyword_intel.contracts import (
    BudgetExhaustedError,
    ProviderQuotaError,
    SearchVolumeProvider,
)
from app.models.keyword_intel import KeywordVolume, VolumeLookup
from app.models.persistence import PersistenceRecord
from app.normalization.query import normalize_query
from app.persistence.contracts import PersistenceConflictError, Repository


TEHRAN = timezone(timedelta(hours=3, minutes=30))
VOLUME_TTL = timedelta(days=30)
UNCONFIRMED_TTL = timedelta(days=1)

VOLUME_AGGREGATE = "keyword_volume"
BUDGET_AGGREGATE = "provider_budget"

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Store:
    """Upsert and read of small cached documents, versioned like every record."""

    def __init__(self, repository: Repository, aggregate_type: str) -> None:
        self._repository = repository
        self._aggregate_type = aggregate_type

    def read(self, key: str) -> tuple[dict, int] | None:
        record = self._repository.get(aggregate_type=self._aggregate_type, aggregate_id=key)
        return None if record is None else (record.payload, record.version)

    def write(self, key: str, payload: dict, *, expected_version: int | None) -> None:
        version = 1 if expected_version is None else expected_version + 1
        record = PersistenceRecord(
            record_id=f"{self._aggregate_type}:{key}:v{version}",
            aggregate_type=self._aggregate_type,
            aggregate_id=key,
            version=version,
            payload=payload,
        )
        if expected_version is None:
            self._repository.create(record)
        else:
            self._repository.replace(record, expected_version=expected_version)

    def upsert(self, key: str, payload: dict) -> None:
        for _ in range(3):
            current = self.read(key)
            try:
                self.write(key, payload, expected_version=None if current is None else current[1])
                return
            except PersistenceConflictError:
                continue  # someone else wrote it meanwhile; theirs is as good


class RequestBudget:
    """A daily allowance of provider requests, shared by every process."""

    def __init__(
        self, repository: Repository, *, scope: str, daily_limit: int, clock: Clock = _utcnow
    ) -> None:
        if daily_limit < 0:
            raise ValueError("daily_limit must not be negative.")
        self._store = _Store(repository, BUDGET_AGGREGATE)
        self._scope = scope
        self._limit = daily_limit
        self._clock = clock

    def _key(self) -> str:
        return f"{self._scope}:{self._clock().astimezone(TEHRAN).date().isoformat()}"

    def used(self) -> int:
        current = self._store.read(self._key())
        return 0 if current is None else int(current[0].get("used", 0))

    def remaining(self) -> int:
        return max(0, self._limit - self.used())

    def consume(self) -> None:
        """Take one request from today's allowance, or refuse without sending it."""
        key = self._key()
        for _ in range(5):
            current = self._store.read(key)
            used = 0 if current is None else int(current[0].get("used", 0))
            if used >= self._limit:
                raise BudgetExhaustedError(
                    f"Today's budget of {self._limit} requests to {self._scope} is spent."
                )
            try:
                self._store.write(
                    key, {"used": used + 1, "limit": self._limit},
                    expected_version=None if current is None else current[1],
                )
                return
            except PersistenceConflictError:
                continue  # another worker took one at the same moment; count again
        raise BudgetExhaustedError("The request budget is too contended right now; try again.")

    def exhaust(self) -> None:
        """The provider says today is spent, whatever our count said."""
        self._store.upsert(self._key(), {"used": self._limit, "limit": self._limit, "provider_said": True})


class CombinedBudget:
    """Several budgets that must all have room: one customer's, and everyone's."""

    def __init__(self, *budgets: RequestBudget) -> None:
        self._budgets = budgets

    def remaining(self) -> int:
        return min(budget.remaining() for budget in self._budgets)

    def consume(self) -> None:
        # Checked before any is taken, so a refusal costs nobody anything.
        for budget in self._budgets:
            if budget.remaining() <= 0:
                raise BudgetExhaustedError("Today's request budget is spent.")
        for budget in self._budgets:
            budget.consume()

    def exhaust(self) -> None:
        # The provider's cap is the service-wide one: the last budget.
        self._budgets[-1].exhaust()


class CachedSearchVolume:
    def __init__(
        self,
        provider: SearchVolumeProvider,
        repository: Repository,
        budget: RequestBudget | CombinedBudget,
        *,
        ttl: timedelta = VOLUME_TTL,
        unconfirmed_ttl: timedelta = UNCONFIRMED_TTL,
        clock: Clock = _utcnow,
    ) -> None:
        self._provider = provider
        self._store = _Store(repository, VOLUME_AGGREGATE)
        self._budget = budget
        self._ttl = ttl
        self._unconfirmed_ttl = unconfirmed_ttl
        self._clock = clock

    def _key(self, keyword: str) -> str:
        return f"{self._provider.provider_id}:{keyword}"

    def peek(self, keywords: Sequence[str]) -> dict[str, KeywordVolume]:
        """Cached answers only, fresh or old; never a request, never the budget.

        For pages that list many keywords at once (the dashboard's queries
        table): a keyword not yet asked about is simply absent.
        """
        found: dict[str, KeywordVolume] = {}
        for keyword in keywords:
            normalized = normalize_query(keyword)
            if not normalized or normalized in found:
                continue
            cached = self._store.read(self._key(normalized))
            if cached is not None:
                found[normalized] = KeywordVolume.model_validate(cached[0])
        return found

    def lookup(self, keywords: Sequence[str]) -> VolumeLookup:
        wanted: list[str] = []
        for keyword in keywords:
            normalized = normalize_query(keyword)
            if normalized and normalized not in wanted:
                wanted.append(normalized)

        now = self._clock()
        answers: dict[str, KeywordVolume] = {}
        stale: dict[str, KeywordVolume] = {}
        for keyword in wanted:
            cached = self._store.read(self._key(keyword))
            if cached is None:
                continue
            volume = KeywordVolume.model_validate(cached[0])
            ttl = self._ttl if volume.confirmed else self._unconfirmed_ttl
            if now - volume.fetched_at <= ttl:
                answers[keyword] = volume
            else:
                stale[keyword] = volume

        missing = [keyword for keyword in wanted if keyword not in answers]
        size = max(1, self._provider.max_keywords_per_request)
        pending: list[str] = []
        requests = 0
        for offset in range(0, len(missing), size):
            chunk = missing[offset:offset + size]
            try:
                self._budget.consume()
                fetched = self._provider.search_volumes(chunk)
            except BudgetExhaustedError:
                pending = missing[offset:]
                break
            except ProviderQuotaError:
                self._budget.exhaust()
                pending = missing[offset:]
                break
            requests += 1
            for volume in fetched:
                key = normalize_query(volume.keyword)
                volume = volume.model_copy(update={"keyword": key})
                answers[key] = volume
                self._store.upsert(self._key(key), volume.model_dump(mode="json"))

        # Out of budget: an older answer, with its age, beats none.
        still_pending = []
        for keyword in pending:
            if keyword in stale:
                answers[keyword] = stale[keyword]
            else:
                still_pending.append(keyword)

        return VolumeLookup(
            volumes=[answers[k] for k in wanted if k in answers],
            pending=still_pending,
            requests_made=requests,
        )
