"""Which model writes for whom: the basic plan's for basic tenants, the best for the rest.

Every model call runs inside a job, and the worker says whose job it is
(acting_for); the transport asks the tenant's plan at the moment of the call.
Anyone the plan cannot be told for (no job around the call, a tenant set up by
the operator, billing unreachable) gets the default model: a cheaper text is
never given where it was not paid for, but a better one may be.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.models.llm import LLMMessage

log = logging.getLogger(__name__)

_acting_for: ContextVar[str | None] = ContextVar("acting_for", default=None)


@contextmanager
def acting_for(tenant_id: str | None) -> Iterator[None]:
    token = _acting_for.set(tenant_id)
    try:
        yield
    finally:
        _acting_for.reset(token)


class TieredTransport:
    def __init__(self, *, default, basic, is_basic: Callable[[str], bool]) -> None:
        self._default = default
        self._basic = basic
        self._is_basic = is_basic
        self._last = default

    def _pick(self):
        tenant = _acting_for.get()
        if tenant is None:
            return self._default
        try:
            return self._basic if self._is_basic(tenant) else self._default
        except Exception:
            log.warning("model tier: could not read the plan of %s; the default model writes", tenant, exc_info=True)
            return self._default

    @property
    def provider_id(self) -> str:
        return self._default.provider_id

    @property
    def model(self) -> str:
        return self._pick().model

    @property
    def max_retries(self) -> int:
        return self._default.max_retries

    @property
    def last_usage(self) -> dict:
        return self._last.last_usage

    @property
    def last_response_metadata(self) -> dict:
        return self._last.last_response_metadata

    def complete(self, *, messages: list[LLMMessage]) -> str:
        chosen = self._pick()
        self._last = chosen
        return chosen.complete(messages=messages)
