from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.serp import SERPQuerySnapshot


class SERPProviderError(RuntimeError):
    """A SERP source could not answer: refused, out of credits, rate-limited,
    out of today's budget, or unreachable. The title path records it and
    blocks the proposal; it does not fail the run."""


class SERPBudgetSpentError(SERPProviderError):
    """Today's request budget is spent and there is no recent enough answer."""


@runtime_checkable
class SERPProvider(Protocol):
    """Provider-neutral contract for one SERP query snapshot."""

    @property
    def provider_id(self) -> str:
        ...

    def search(self, *, query: str) -> SERPQuerySnapshot:
        ...


class StaticSERPProvider:
    """Deterministic SERP provider for local development and tests."""

    def __init__(self, snapshots: dict[str, SERPQuerySnapshot], provider_id: str = "static") -> None:
        self._snapshots = dict(snapshots)
        self._provider_id = provider_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def search(self, *, query: str) -> SERPQuerySnapshot:
        try:
            return self._snapshots[query]
        except KeyError as exc:
            raise LookupError(f"No SERP snapshot configured for query: {query}") from exc
