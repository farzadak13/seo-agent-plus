from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.actions import Action


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    INCONCLUSIVE = "inconclusive"


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_id: str
    action_id: str
    site_id: str
    normalized_url: str
    status: VerificationStatus
    expected_state: dict
    observed_state: dict
    matched_keys: list[str]
    mismatched_keys: list[str]
    reasons: list[str]
    evidence: dict
    checked_at: datetime


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_id: str
    action_id: str
    site_id: str
    normalized_url: str
    expected_state: dict


def build_verification_request(
    *,
    action: Action,
    verification_id: str,
) -> VerificationRequest:
    if not action.parameters:
        raise ValueError("Action has no expected execution state.")

    expected_state = {
        key: value
        for key, value in action.parameters.items()
        if key.startswith("expected_")
    }

    if not expected_state:
        raise ValueError("Action has no expected_* parameters for verification.")

    normalized_state = {
        key.removeprefix("expected_"): value
        for key, value in expected_state.items()
    }

    return VerificationRequest(
        verification_id=verification_id,
        action_id=action.action_id,
        site_id=action.site_id,
        normalized_url=action.normalized_url,
        expected_state=normalized_state,
    )
