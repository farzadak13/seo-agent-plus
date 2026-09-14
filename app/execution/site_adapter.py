from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.execution import ExecutionCapability
from app.models.site_adapter import (
    AdapterOperationResult,
    AdapterRollbackRequest,
    SitePage,
)


@runtime_checkable
class SiteAdapter(Protocol):
    """
    Platform-agnostic contract for interacting with a site.
    """

    @property
    def adapter_id(self) -> str:
        ...

    @property
    def capabilities(self) -> set[ExecutionCapability]:
        ...

    def read_page(
        self,
        *,
        site_id: str,
        normalized_url: str,
    ) -> SitePage:
        ...

    def update_title(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_title: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        ...

    def update_meta_description(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_meta_description: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        ...

    def update_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        ...

    def update_internal_links(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        ...

    def publish_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        ...

    def rollback_change(
        self,
        *,
        request: AdapterRollbackRequest,
    ) -> AdapterOperationResult:
        ...