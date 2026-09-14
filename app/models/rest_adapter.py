from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class RESTEndpointConfig(BaseModel):
    """
    Describes one REST endpoint template.

    `{url}` is replaced with the percent-encoded normalized URL.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    method: str = Field(min_length=1)


class RESTAdapterConfig(BaseModel):
    """
    Runtime configuration for a generic REST site adapter.

    The configuration intentionally describes the remote API rather than
    the domain model of the SEO engine.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: HttpUrl

    read_page: RESTEndpointConfig = RESTEndpointConfig(
        path="/pages?url={url}",
        method="GET",
    )

    update_title: RESTEndpointConfig = RESTEndpointConfig(
        path="/pages/title",
        method="PATCH",
    )

    update_meta_description: RESTEndpointConfig = RESTEndpointConfig(
        path="/pages/meta-description",
        method="PATCH",
    )

    update_content: RESTEndpointConfig = RESTEndpointConfig(
        path="/pages/content",
        method="PATCH",
    )

    update_internal_links: RESTEndpointConfig = RESTEndpointConfig(
        path="/pages/internal-links",
        method="PATCH",
    )

    publish_content: RESTEndpointConfig = RESTEndpointConfig(
        path="/pages/publish",
        method="POST",
    )

    rollback_change: RESTEndpointConfig = RESTEndpointConfig(
        path="/changes/{change_id}/rollback",
        method="POST",
    )

    title_field: str = "title"
    meta_description_field: str = "meta_description"
    content_field: str = "content"

    page_status_field: str = "status"

    authorization_token: str | None = None
    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
    )

    extra_headers: dict[str, str] = Field(
        default_factory=dict,
    )

    request_body_extra: dict[str, Any] = Field(
        default_factory=dict,
    )