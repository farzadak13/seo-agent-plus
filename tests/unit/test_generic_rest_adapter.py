import json

import pytest

from app.models.execution import ExecutionCapability
from app.models.rest_adapter import (
    RESTAdapterConfig,
    RESTEndpointConfig,
)
from app.models.site_adapter import (
    AdapterOperation,
    AdapterRollbackRequest,
    SitePage,
)
from app.site_adapters.generic_rest import (
    GenericRESTSiteAdapter,
    HTTPResponse,
    RESTAdapterError,
)


def make_config() -> RESTAdapterConfig:
    return RESTAdapterConfig(
        base_url="https://cms.example.com/api/",
        read_page=RESTEndpointConfig(
            path="/pages?url={url}",
            method="GET",
        ),
        update_title=RESTEndpointConfig(
            path="/pages/title",
            method="PATCH",
        ),
        update_meta_description=RESTEndpointConfig(
            path="/pages/meta",
            method="PATCH",
        ),
        update_content=RESTEndpointConfig(
            path="/pages/content",
            method="PATCH",
        ),
        update_internal_links=RESTEndpointConfig(
            path="/pages/links",
            method="PATCH",
        ),
        publish_content=RESTEndpointConfig(
            path="/pages/publish",
            method="POST",
        ),
        rollback_change=RESTEndpointConfig(
            path="/changes/{change_id}/rollback",
            method="POST",
        ),
        authorization_token="secret-token",
        timeout_seconds=15,
    )


class FakeTransport:
    def __init__(self):
        self.calls = []

        self.response = HTTPResponse(
            status_code=200,
            body=json.dumps(
                {
                    "title": "Current title",
                    "meta_description": "Current description",
                    "content": "<p>Current</p>",
                    "status": "published",
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
            },
        )

    def __call__(
        self,
        method,
        url,
        headers,
        body,
        timeout,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "timeout": timeout,
            }
        )

        return self.response


def test_adapter_has_identifier():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    assert adapter.adapter_id == "generic-rest"


def test_adapter_exposes_all_supported_capabilities():
    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=FakeTransport(),
    )

    assert adapter.capabilities == {
        ExecutionCapability.READ_PAGE,
        ExecutionCapability.UPDATE_TITLE,
        ExecutionCapability.UPDATE_META_DESCRIPTION,
        ExecutionCapability.UPDATE_CONTENT,
        ExecutionCapability.UPDATE_INTERNAL_LINKS,
        ExecutionCapability.PUBLISH_CONTENT,
        ExecutionCapability.ROLLBACK_CHANGE,
    }


def test_read_page_returns_canonical_site_page():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    page = adapter.read_page(
        site_id="site-1",
        normalized_url="https://example.com/page?a=1&b=2",
    )

    assert isinstance(page, SitePage)
    assert page.site_id == "site-1"
    assert page.normalized_url == (
        "https://example.com/page?a=1&b=2"
    )
    assert page.title == "Current title"
    assert page.meta_description == "Current description"
    assert page.content == "<p>Current</p>"
    assert page.status == "published"


def test_read_page_encodes_url_in_endpoint():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    adapter.read_page(
        site_id="site-1",
        normalized_url="https://example.com/a page?q=کفش",
    )

    call = transport.calls[0]

    assert call["method"] == "GET"

    assert (
        "https%3A%2F%2Fexample.com%2Fa%20page"
        "%3Fq%3D%DA%A9%D9%81%D8%B4"
        in call["url"]
    )

def test_authorization_header_is_added():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    adapter.read_page(
        site_id="site-1",
        normalized_url="https://example.com/page",
    )

    assert transport.calls[0]["headers"][
        "Authorization"
    ] == "Bearer secret-token"


def test_update_title_sends_patch_and_idempotency_key():
    transport = FakeTransport()

    transport.response = HTTPResponse(
        status_code=200,
        body=json.dumps(
            {
                "change_id": "change-001",
                "previous_value": "Old title",
                "new_value": "New title",
                "message": "updated",
            }
        ).encode("utf-8"),
        headers={},
    )

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    result = adapter.update_title(
        site_id="site-1",
        normalized_url="https://example.com/page",
        new_title="New title",
        idempotency_key="action-001",
    )

    call = transport.calls[0]

    assert call["method"] == "PATCH"
    assert call["headers"]["Idempotency-Key"] == (
        "action-001"
    )

    payload = json.loads(
        call["body"].decode("utf-8")
    )

    assert payload["site_id"] == "site-1"
    assert payload["url"] == (
        "https://example.com/page"
    )
    assert payload["title"] == "New title"

    assert result.success is True
    assert result.operation == AdapterOperation.UPDATE_TITLE
    assert result.change_id == "change-001"
    assert result.previous_value == "Old title"
    assert result.new_value == "New title"


def test_meta_description_uses_configured_field():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    adapter.update_meta_description(
        site_id="site-1",
        normalized_url="https://example.com/page",
        new_meta_description="New description",
        idempotency_key="action-meta-001",
    )

    payload = json.loads(
        transport.calls[0]["body"].decode("utf-8")
    )

    assert payload["meta_description"] == (
        "New description"
    )


def test_update_content_uses_configured_field():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    adapter.update_content(
        site_id="site-1",
        normalized_url="https://example.com/page",
        new_content="<p>New</p>",
        idempotency_key="action-content-001",
    )

    payload = json.loads(
        transport.calls[0]["body"].decode("utf-8")
    )

    assert payload["content"] == "<p>New</p>"


def test_publish_content_is_supported():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    result = adapter.publish_content(
        site_id="site-1",
        normalized_url="https://example.com/page",
        idempotency_key="publish-001",
    )

    assert transport.calls[0]["method"] == "POST"
    assert result.operation == (
        AdapterOperation.PUBLISH_CONTENT
    )


def test_rollback_uses_change_id_in_endpoint():
    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    result = adapter.rollback_change(
        request=AdapterRollbackRequest(
            site_id="site-1",
            normalized_url="https://example.com/page",
            change_id="change-123",
            idempotency_key="rollback-001",
        )
    )

    assert (
        "/changes/change-123/rollback"
        in transport.calls[0]["url"]
    )

    assert result.operation == (
        AdapterOperation.ROLLBACK_CHANGE
    )


def test_empty_idempotency_key_is_rejected():
    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=FakeTransport(),
    )

    with pytest.raises(ValueError):
        adapter.update_title(
            site_id="site-1",
            normalized_url="https://example.com/page",
            new_title="New title",
            idempotency_key=" ",
        )


def test_non_json_read_response_is_rejected():
    transport = FakeTransport()

    transport.response = HTTPResponse(
        status_code=200,
        body=b"not-json",
        headers={},
    )

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    with pytest.raises(
        RESTAdapterError,
        match="not valid UTF-8 JSON|not valid",
    ):
        adapter.read_page(
            site_id="site-1",
            normalized_url="https://example.com/page",
        )


def test_http_error_status_is_rejected():
    transport = FakeTransport()

    transport.response = HTTPResponse(
        status_code=500,
        body=b"{}",
        headers={},
    )

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    with pytest.raises(RESTAdapterError) as exc_info:
        adapter.read_page(
            site_id="site-1",
            normalized_url="https://example.com/page",
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.retryable is True


def test_unauthorized_response_is_not_retryable():
    transport = FakeTransport()

    transport.response = HTTPResponse(
        status_code=401,
        body=b"{}",
        headers={},
    )

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=transport,
    )

    with pytest.raises(RESTAdapterError) as exc_info:
        adapter.read_page(
            site_id="site-1",
            normalized_url="https://example.com/page",
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.retryable is False


def test_extra_headers_are_forwarded():
    config = make_config().model_copy(
        update={
            "extra_headers": {
                "X-Site-Key": "site-secret",
            }
        }
    )

    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=config,
        transport=transport,
    )

    adapter.read_page(
        site_id="site-1",
        normalized_url="https://example.com/page",
    )

    assert transport.calls[0]["headers"][
        "X-Site-Key"
    ] == "site-secret"


def test_adapter_remains_platform_agnostic():
    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=make_config(),
        transport=FakeTransport(),
    )

    assert adapter.adapter_id == "generic-rest"
    assert "wordpress" not in adapter.adapter_id.lower()

def test_custom_api_field_mapping_works():
    config = make_config().model_copy(
        update={
            "title_field": "page_title",
            "meta_description_field": "seo_description",
            "content_field": "html",
            "page_status_field": "publication_state",
        }
    )

    transport = FakeTransport()

    transport.response = HTTPResponse(
        status_code=200,
        body=json.dumps(
            {
                "page_title": "Custom title",
                "seo_description": "Custom desc",
                "html": "<main>Custom</main>",
                "publication_state": "live",
            }
        ).encode("utf-8"),
        headers={},
    )

    adapter = GenericRESTSiteAdapter(
        adapter_id="custom-rest",
        config=config,
        transport=transport,
    )

    page = adapter.read_page(
        site_id="site-1",
        normalized_url="https://example.com/page",
    )

    assert page.title == "Custom title"
    assert page.meta_description == "Custom desc"
    assert page.content == "<main>Custom</main>"
    assert page.status == "live"


def test_request_body_extra_is_added():
    config = make_config().model_copy(
        update={
            "request_body_extra": {
                "locale": "fa-IR",
                "source": "seo-agent",
            }
        }
    )

    transport = FakeTransport()

    adapter = GenericRESTSiteAdapter(
        adapter_id="generic-rest",
        config=config,
        transport=transport,
    )

    adapter.update_title(
        site_id="site-1",
        normalized_url="https://example.com/page",
        new_title="New",
        idempotency_key="action-001",
    )

    payload = json.loads(
        transport.calls[0]["body"].decode("utf-8")
    )

    assert payload["locale"] == "fa-IR"
    assert payload["source"] == "seo-agent"