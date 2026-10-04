import base64
import json

import pytest

from app.models.execution import ExecutionCapability
from app.models.site_adapter import (
    AdapterOperation,
    AdapterRollbackRequest,
    SitePage,
)
from app.execution.site_adapter import SiteAdapter
from app.models.wordpress_adapter import (
    WordPressAdapterConfig,
    WordPressContentType,
)
from app.site_adapters.wordpress import (
    WordPressAdapterError,
    WordPressHTTPResponse,
    WordPressSiteAdapter,
)


def make_config() -> WordPressAdapterConfig:
    return WordPressAdapterConfig(
        base_url="https://example.com/",
        username="seo-agent",
        application_password="application-password",
        content_type=WordPressContentType.POSTS,
        timeout_seconds=10,
    )


class FakeWordPressTransport:
    def __init__(self) -> None:
        self.calls = []

        self.responses = []

    def add_response(
        self,
        payload,
        status_code: int = 200,
    ) -> None:
        body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(
                payload,
                ensure_ascii=False,
            ).encode("utf-8")
        )

        self.responses.append(
            WordPressHTTPResponse(
                status_code=status_code,
                body=body,
                headers={
                    "Content-Type": (
                        "application/json"
                    )
                },
            )
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

        if not self.responses:
            raise AssertionError(
                "No fake WordPress response configured."
            )

        return self.responses.pop(0)


def make_post(
    *,
    post_id: int = 42,
    title: str = "Old title",
    content: str = "<p>Old content</p>",
    status: str = "publish",
):
    return {
        "id": post_id,
        "slug": "test-page",
        "link": "https://example.com/test-page/",
        "status": status,
        "title": {
            "raw": title,
            "rendered": title,
        },
        "content": {
            "raw": content,
            "rendered": content,
        },
        "meta": {
            "_seo_agent_meta_description": (
                "Old description"
            ),
        },
        "modified_gmt": (
            "2026-09-09T08:00:00"
        ),
    }


def make_revision(
    revision_id: int = 900,
):
    return {
        "id": revision_id,
        "title": {
            "raw": "Revision title",
        },
        "content": {
            "raw": "<p>Revision content</p>",
        },
        "excerpt": {
            "raw": "Revision excerpt",
        },
    }


def test_adapter_id_is_wordpress():
    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=FakeWordPressTransport(),
    )

    assert adapter.adapter_id == "wordpress"


def test_adapter_exposes_expected_capabilities():
    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=FakeWordPressTransport(),
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


def test_read_page_resolves_slug_and_returns_canonical_model():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    page = adapter.read_page(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
    )

    assert isinstance(page, SitePage)
    assert page.site_id == "site-1"
    assert page.title == "Old title"
    assert page.content == "<p>Old content</p>"
    assert page.meta_description == "Old description"
    assert page.status == "publish"


def test_read_page_uses_wordpress_posts_endpoint():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    adapter.read_page(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
    )

    call = transport.calls[0]

    assert call["method"] == "GET"
    assert (
        "/wp-json/wp/v2/posts"
        in call["url"]
    )
    assert "slug=test-page" in call["url"]
    # The raw title, not the HTML-escaped rendering, or nothing we write
    # ever reads back equal to itself.
    assert "context=edit" in call["url"]


def test_application_password_basic_auth_is_correct():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    adapter.read_page(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
    )

    header = transport.calls[0][
        "headers"
    ]["Authorization"]

    expected = base64.b64encode(
        (
            "seo-agent:"
            "application-password"
        ).encode("utf-8")
    ).decode("ascii")

    assert header == (
        f"Basic {expected}"
    )


def test_update_title_sends_wordpress_post_request():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    transport.add_response(
        make_post(
            title="New title"
        )
    )

    transport.add_response(
        [
            make_revision(
                revision_id=901
            )
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    result = adapter.update_title(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
        new_title="New title",
        idempotency_key="action-title-001",
    )

    assert result.success is True
    assert (
        result.operation
        == AdapterOperation.UPDATE_TITLE
    )
    assert (
        result.previous_value
        == "Old title"
    )
    assert (
        result.new_value
        == "New title"
    )

    update_call = transport.calls[1]

    assert update_call["method"] == "POST"
    assert (
        "/wp-json/wp/v2/posts/42"
        in update_call["url"]
    )

    assert (
        update_call["headers"][
            "Idempotency-Key"
        ]
        == "action-title-001"
    )

    payload = json.loads(
        update_call["body"].decode(
            "utf-8"
        )
    )

    assert payload["title"] == "New title"


def test_update_content_uses_content_field():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    transport.add_response(
        make_post(
            content="<p>New</p>"
        )
    )

    transport.add_response(
        [
            make_revision(
                revision_id=902
            )
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    result = adapter.update_content(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
        new_content="<p>New</p>",
        idempotency_key="action-content-001",
    )

    payload = json.loads(
        transport.calls[1]["body"].decode(
            "utf-8"
        )
    )

    assert payload["content"] == (
        "<p>New</p>"
    )

    assert (
        result.operation
        == AdapterOperation.UPDATE_CONTENT
    )


def test_meta_description_uses_registered_meta_field():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    transport.add_response(
        {
            **make_post(),
            "meta": {
                "_seo_agent_meta_description": (
                    "New description"
                )
            },
        }
    )

    transport.add_response(
        [
            make_revision(
                revision_id=901
            )
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    result = adapter.update_meta_description(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
        new_meta_description="New description",
        idempotency_key="action-meta-001",
    )

    payload = json.loads(
        transport.calls[1]["body"].decode(
            "utf-8"
        )
    )

    assert payload["meta"][
        "_seo_agent_meta_description"
    ] == "New description"

    assert result.new_value == (
        "New description"
    )

def test_publish_content_changes_status_to_publish():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    transport.add_response(
        make_post(
            status="publish"
        )
    )

    transport.add_response(
        [
            make_revision(
                revision_id=903
            )
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    result = adapter.publish_content(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
        idempotency_key="publish-001",
    )

    payload = json.loads(
        transport.calls[1]["body"].decode(
            "utf-8"
        )
    )

    assert payload["status"] == "publish"

    assert (
        result.operation
        == AdapterOperation.PUBLISH_CONTENT
    )


def test_rollback_reads_revision_and_updates_post():
    transport = FakeWordPressTransport()

    transport.add_response(
        make_revision(
            revision_id=900
        )
    )

    transport.add_response(
        make_post(
            title="Revision title",
            content=(
                "<p>Revision content</p>"
            )
        )
    )

    transport.add_response(
        [
            make_revision(
                revision_id=904
            )
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    result = adapter.rollback_change(
        request=AdapterRollbackRequest(
            site_id="site-1",
            normalized_url=(
                "https://example.com/test-page/"
            ),
            change_id=(
                "wordpress:42:900"
            ),
            idempotency_key="rollback-001",
        )
    )

    assert result.success is True
    assert (
        result.operation
        == AdapterOperation.ROLLBACK_CHANGE
    )

    update_call = transport.calls[1]

    payload = json.loads(
        update_call["body"].decode(
            "utf-8"
        )
    )

    assert payload["title"] == (
        "Revision title"
    )

    assert payload["content"] == (
        "<p>Revision content</p>"
    )


def test_rollback_rejects_invalid_change_id():
    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=FakeWordPressTransport(),
    )

    with pytest.raises(
        WordPressAdapterError,
        match="Invalid WordPress rollback",
    ):
        adapter.rollback_change(
            request=AdapterRollbackRequest(
                site_id="site-1",
                normalized_url=(
                    "https://example.com/page"
                ),
                change_id="invalid",
                idempotency_key="rollback-001",
            )
        )


def test_missing_slug_result_is_rejected():
    transport = FakeWordPressTransport()

    transport.add_response([])

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    with pytest.raises(
        WordPressAdapterError,
        match="not found",
    ):
        adapter.read_page(
            site_id="site-1",
            normalized_url=(
                "https://example.com/missing/"
            ),
        )


def test_ambiguous_slug_result_is_rejected():
    transport = FakeWordPressTransport()

    first = make_post(
        post_id=1
    )

    second = make_post(
        post_id=2
    )

    second["link"] = (
        "https://another.example.com/test-page/"
    )

    transport.add_response(
        [
            first,
            second,
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    with pytest.raises(
        WordPressAdapterError,
        match="ambiguous",
    ):
        adapter.read_page(
            site_id="site-1",
            normalized_url=(
                "https://third.example.com/test-page/"
            ),
        )


def test_http_401_is_not_retryable():
    transport = FakeWordPressTransport()

    transport.add_response(
        {},
        status_code=401,
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    with pytest.raises(
        WordPressAdapterError
    ) as exc_info:
        adapter.read_page(
            site_id="site-1",
            normalized_url=(
                "https://example.com/page/"
            ),
        )

    assert exc_info.value.status_code == 401
    assert (
        exc_info.value.retryable
        is False
    )


def test_http_500_is_retryable():
    transport = FakeWordPressTransport()

    transport.add_response(
        {},
        status_code=500,
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    with pytest.raises(
        WordPressAdapterError
    ) as exc_info:
        adapter.read_page(
            site_id="site-1",
            normalized_url=(
                "https://example.com/page/"
            ),
        )

    assert exc_info.value.status_code == 500
    assert (
        exc_info.value.retryable
        is True
    )


def test_page_content_type_is_supported():
    config = make_config().model_copy(
        update={
            "content_type": (
                WordPressContentType.PAGES
            )
        }
    )

    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    adapter = WordPressSiteAdapter(
        config=config,
        transport=transport,
    )

    adapter.read_page(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
    )

    assert "/wp-json/wp/v2/pages" in (
        transport.calls[0]["url"]
    )


def test_empty_idempotency_key_is_rejected():
    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=FakeWordPressTransport(),
    )

    with pytest.raises(
        ValueError,
        match="idempotency_key",
    ):
        adapter.update_title(
            site_id="site-1",
            normalized_url=(
                "https://example.com/page/"
            ),
            new_title="New",
            idempotency_key=" ",
        )


def test_adapter_is_a_site_adapter():
    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=FakeWordPressTransport(),
    )

    assert isinstance(
        adapter,
        SiteAdapter,
    )


def test_wordpress_credentials_do_not_enter_canonical_page():
    transport = FakeWordPressTransport()

    transport.add_response(
        [
            make_post()
        ]
    )

    adapter = WordPressSiteAdapter(
        config=make_config(),
        transport=transport,
    )

    page = adapter.read_page(
        site_id="site-1",
        normalized_url=(
            "https://example.com/test-page/"
        ),
    )

    dumped = page.model_dump_json().lower()

    assert "application-password" not in dumped
    assert "seo-agent" not in dumped