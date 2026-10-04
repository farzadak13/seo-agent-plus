"""The port the rest of the system depends on. Adapters implement it."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.models.keyword_intel import KeywordVolume


class KeywordProviderError(RuntimeError):
    """A provider call failed. The message is safe to show an operator."""

    retryable = False


class ProviderAuthError(KeywordProviderError):
    """The key was refused, or the account is disabled."""


class ProviderPlanError(KeywordProviderError):
    """The account's plan does not include this feature."""


class ProviderQuotaError(KeywordProviderError):
    """The provider says today's allowance is spent."""


class ProviderUnavailableError(KeywordProviderError):
    retryable = True


class BudgetExhaustedError(KeywordProviderError):
    """Our own daily budget for the provider is spent; nothing was sent."""


class SearchVolumeProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def max_keywords_per_request(self) -> int: ...

    def search_volumes(self, keywords: Sequence[str]) -> list[KeywordVolume]:
        """One upstream request for at most ``max_keywords_per_request`` keywords.

        Returns one entry per keyword asked about, in the same order; a keyword
        the provider has no data for comes back with ``search_volume=None``.
        """
        ...
