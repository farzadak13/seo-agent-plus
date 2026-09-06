from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.actions import Action


class ExecutionCapability(StrEnum):
    READ_PAGE = "read_page"
    UPDATE_TITLE = "update_title"
    UPDATE_META_DESCRIPTION = "update_meta_description"
    UPDATE_CONTENT = "update_content"
    UPDATE_INTERNAL_LINKS = "update_internal_links"
    PUBLISH_CONTENT = "publish_content"
    ROLLBACK_CHANGE = "rollback_change"


class ExecutionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str
    normalized_url: str
    normalized_query: str


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    site_id: str

    target: ExecutionTarget

    required_capabilities: list[ExecutionCapability]

    parameters: dict

    idempotency_key: str


class ExecutionResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str

    supported_capabilities: list[ExecutionCapability]

    request: ExecutionRequest