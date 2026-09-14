from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class WordPressContentType(StrEnum):
    POSTS = "posts"
    PAGES = "pages"


class WordPressAdapterConfig(BaseModel):
    """
    Configuration for the real WordPress REST API adapter.

    Credentials live only in infrastructure configuration and never
    become part of SEO domain models.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    base_url: HttpUrl

    username: str = Field(min_length=1)
    application_password: str = Field(min_length=1)

    content_type: WordPressContentType = (
        WordPressContentType.POSTS
    )

    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
    )

    api_prefix: str = "/wp-json/wp/v2"

    extra_headers: dict[str, str] = Field(
        default_factory=dict,
    )