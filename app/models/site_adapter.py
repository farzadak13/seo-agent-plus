from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AdapterOperation(StrEnum):
    READ_PAGE = "read_page"
    UPDATE_TITLE = "update_title"
    UPDATE_META_DESCRIPTION = "update_meta_description"
    UPDATE_CONTENT = "update_content"
    UPDATE_INTERNAL_LINKS = "update_internal_links"
    PUBLISH_CONTENT = "publish_content"
    ROLLBACK_CHANGE = "rollback_change"


class SitePage(BaseModel):
    """
    Canonical page representation returned by a site adapter.

    This model intentionally contains only CMS/site-independent data.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(min_length=1)
    normalized_url: str = Field(min_length=1)

    title: str = ""
    meta_description: str = ""
    content: str = ""

    status: str = "unknown"

    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterOperationResult(BaseModel):
    """
    Canonical result of one adapter operation.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    operation: AdapterOperation

    site_id: str = Field(min_length=1)
    normalized_url: str = Field(min_length=1)

    message: str = ""

    change_id: str | None = None

    previous_value: Any = None
    new_value: Any = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterRollbackRequest(BaseModel):
    """
    Generic rollback request.

    The change_id is the canonical identifier returned by
    a previous mutating operation.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(min_length=1)
    normalized_url: str = Field(min_length=1)
    change_id: str = Field(min_length=1)

    idempotency_key: str = Field(min_length=1)