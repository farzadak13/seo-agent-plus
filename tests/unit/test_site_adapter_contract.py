from typing import Any

import pytest

from app.execution.site_adapter import SiteAdapter
from app.models.execution import ExecutionCapability
from app.models.site_adapter import (
    AdapterOperation,
    AdapterOperationResult,
    AdapterRollbackRequest,
    SitePage,
)


class FakeGenericAdapter:
    @property
    def adapter_id(self) -> str:
        return "generic-test"

    @property
    def capabilities(
        self,
    ) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
            ExecutionCapability.UPDATE_TITLE,
            ExecutionCapability.UPDATE_META_DESCRIPTION,
            ExecutionCapability.UPDATE_CONTENT,
            ExecutionCapability.UPDATE_INTERNAL_LINKS,
            ExecutionCapability.PUBLISH_CONTENT,
            ExecutionCapability.ROLLBACK_CHANGE,
        }

    def read_page(
        self,
        *,
        site_id: str,
        normalized_url: str,
    ) -> SitePage:
        return SitePage(
            site_id=site_id,
            normalized_url=normalized_url,
            title="Current title",
            meta_description="Current description",
            content="<p>Hello</p>",
            status="published",
        )

    def update_title(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_title: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_TITLE,
            site_id=site_id,
            normalized_url=normalized_url,
            change_id="change-title-001",
            previous_value="Old title",
            new_value=new_title,
        )

    def update_meta_description(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_meta_description: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_META_DESCRIPTION,
            site_id=site_id,
            normalized_url=normalized_url,
            change_id="change-meta-001",
            previous_value="Old description",
            new_value=new_meta_description,
        )

    def update_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_CONTENT,
            site_id=site_id,
            normalized_url=normalized_url,
            change_id="change-content-001",
            previous_value="<p>Old</p>",
            new_value=new_content,
        )

    def update_internal_links(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_INTERNAL_LINKS,
            site_id=site_id,
            normalized_url=normalized_url,
            change_id="change-links-001",
            previous_value="<p>Old</p>",
            new_value=new_content,
        )

    def publish_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.PUBLISH_CONTENT,
            site_id=site_id,
            normalized_url=normalized_url,
            change_id="change-publish-001",
        )

    def rollback_change(
        self,
        *,
        request: AdapterRollbackRequest,
    ) -> AdapterOperationResult:
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.ROLLBACK_CHANGE,
            site_id=request.site_id,
            normalized_url=request.normalized_url,
            change_id=request.change_id,
        )


def test_generic_adapter_satisfies_site_adapter_protocol():
    adapter = FakeGenericAdapter()

    assert isinstance(adapter, SiteAdapter)


def test_site_adapter_is_platform_agnostic():
    adapter = FakeGenericAdapter()

    assert adapter.adapter_id == "generic-test"

    assert "wordpress" not in adapter.adapter_id.lower()


def test_read_page_returns_canonical_model():
    adapter = FakeGenericAdapter()

    page = adapter.read_page(
        site_id="site-1",
        normalized_url="https://example.com/page",
    )

    assert page.site_id == "site-1"
    assert page.normalized_url == "https://example.com/page"
    assert page.title == "Current title"
    assert page.status == "published"


def test_update_title_returns_change_id():
    adapter = FakeGenericAdapter()

    result = adapter.update_title(
        site_id="site-1",
        normalized_url="https://example.com/page",
        new_title="New title",
        idempotency_key="action-001",
    )

    assert result.success is True
    assert result.operation == AdapterOperation.UPDATE_TITLE
    assert result.change_id == "change-title-001"
    assert result.new_value == "New title"


def test_mutating_operations_require_idempotency_key():
    adapter = FakeGenericAdapter()

    with pytest.raises(TypeError):
        adapter.update_title(
            site_id="site-1",
            normalized_url="https://example.com/page",
            new_title="New title",
        )


def test_rollback_contract_is_generic():
    adapter = FakeGenericAdapter()

    request = AdapterRollbackRequest(
        site_id="site-1",
        normalized_url="https://example.com/page",
        change_id="change-title-001",
        idempotency_key="rollback-001",
    )

    result = adapter.rollback_change(
        request=request,
    )

    assert result.success is True
    assert result.operation == AdapterOperation.ROLLBACK_CHANGE
    assert result.change_id == "change-title-001"


def test_capabilities_are_exposed_as_execution_capabilities():
    adapter = FakeGenericAdapter()

    assert (
        ExecutionCapability.UPDATE_TITLE
        in adapter.capabilities
    )

    assert (
        ExecutionCapability.ROLLBACK_CHANGE
        in adapter.capabilities
    )


def test_site_page_rejects_unknown_fields():
    with pytest.raises(Exception):
        SitePage(
            site_id="site-1",
            normalized_url="https://example.com",
            wordpress_post_id=123,
        )


def test_operation_result_rejects_unknown_fields():
    with pytest.raises(Exception):
        AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_TITLE,
            site_id="site-1",
            normalized_url="https://example.com",
            wordpress_post_id=123,
        )


def test_canonical_models_do_not_contain_platform_specific_fields():
    page = SitePage(
        site_id="site-1",
        normalized_url="https://example.com/page",
    )

    result = AdapterOperationResult(
        success=True,
        operation=AdapterOperation.UPDATE_TITLE,
        site_id="site-1",
        normalized_url="https://example.com/page",
    )

    combined = (
        page.model_dump_json().lower()
        + result.model_dump_json().lower()
    )

    assert "wordpress" not in combined
    assert "wp_post_id" not in combined
    assert "wp_" not in combined