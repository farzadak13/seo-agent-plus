"""The part of a page a change is about: its title, or its meta description.

Everything else about a change is the same whatever it changes: approval,
applying, checking the page shows it, measuring, undoing. So that path reads
and writes through here, and a new field is a new entry, not a new path.

An action names its field in ``parameters["field"]``; actions made before
there was more than one field have none and are titles.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.actions import Action, ActionType


@dataclass(frozen=True)
class Field:
    name: str
    action_type: ActionType
    # Longest value accepted from a proposal or a reviewer's edit.
    max_length: int
    # What the connector reports as the value it stores for this field.
    stored_key: str
    label: str

    def recommended(self, action: Action) -> str:
        return action.parameters[f"recommended_{self.name}"]

    def expected(self, action: Action) -> str:
        return action.parameters[f"expected_{self.name}"]

    def on_page(self, page) -> str:
        return page.title if self.name == "title" else page.meta_description

    def write(self, adapter, *, site_id: str, normalized_url: str, value: str, idempotency_key: str):
        if self.name == "title":
            return adapter.update_title(
                site_id=site_id, normalized_url=normalized_url, new_title=value, idempotency_key=idempotency_key
            )
        return adapter.update_meta_description(
            site_id=site_id, normalized_url=normalized_url, new_meta_description=value, idempotency_key=idempotency_key
        )


TITLE = Field(name="title", action_type=ActionType.OPTIMIZE_TITLE, max_length=300, stored_key="override_title", label="title")
# Past 170 characters the scan calls a description too long; an edit may not make one.
DESCRIPTION = Field(
    name="description", action_type=ActionType.OPTIMIZE_META_DESCRIPTION, max_length=170,
    stored_key="override_description", label="meta description",
)
FIELDS = {TITLE.name: TITLE, DESCRIPTION.name: DESCRIPTION}
BY_ACTION_TYPE = {field.action_type: field for field in FIELDS.values()}


def field_of(action: Action) -> Field:
    """By the action's type; an action of a type with no field is about its title, as all were."""
    return BY_ACTION_TYPE.get(action.action_type) or FIELDS[action.parameters.get("field", TITLE.name)]
