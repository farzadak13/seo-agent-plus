from datetime import datetime, timezone

from app.action.lifecycle import (
    mark_execution_failed,
    mark_measurement_window_active,
)
from app.models.actions import Action, ActionStatus
from app.models.verification import (
    VerificationResult,
    VerificationStatus,
    build_verification_request,
)


def verify_action_state(
    *,
    action: Action,
    verification_id: str,
    observed_state: dict,
    checked_at: datetime | None = None,
) -> VerificationResult:
    if action.status != ActionStatus.WAITING_FOR_RECRAWL:
        raise ValueError(
            "Verification requires action state WAITING_FOR_RECRAWL."
        )

    request = build_verification_request(
        action=action,
        verification_id=verification_id,
    )

    expected_keys = set(request.expected_state)
    observed_keys = set(observed_state)

    matched = sorted(
        key
        for key in expected_keys & observed_keys
        if observed_state[key] == request.expected_state[key]
    )
    mismatched = sorted(
        key
        for key in expected_keys
        if key not in observed_state
        or observed_state[key] != request.expected_state[key]
    )

    timestamp = checked_at or datetime.now(timezone.utc)

    missing = sorted(expected_keys - observed_keys)

    if not expected_keys:
        status = VerificationStatus.INCONCLUSIVE
        reasons = ["no_expected_state"]
    elif missing:
        status = VerificationStatus.INCONCLUSIVE
        reasons = ["observed_state_incomplete"]
    elif mismatched:
        status = VerificationStatus.NOT_VERIFIED
        reasons = ["expected_state_mismatch"]
    else:
        status = VerificationStatus.VERIFIED
        reasons = ["expected_state_verified"]

    return VerificationResult(
        verification_id=verification_id,
        action_id=action.action_id,
        site_id=action.site_id,
        normalized_url=action.normalized_url,
        status=status,
        expected_state=request.expected_state,
        observed_state=dict(observed_state),
        matched_keys=matched,
        mismatched_keys=mismatched,
        reasons=reasons,
        evidence={
            "expected_key_count": len(expected_keys),
            "observed_key_count": len(observed_keys),
        },
        checked_at=timestamp,
    )


def apply_verification(
    *,
    action: Action,
    verification: VerificationResult,
) -> Action:
    if verification.action_id != action.action_id:
        raise ValueError("Verification action_id does not match action.")

    if verification.status == VerificationStatus.VERIFIED:
        return mark_measurement_window_active(
            action=action,
            verification_id=verification.verification_id,
        ).action

    if verification.status == VerificationStatus.NOT_VERIFIED:
        return mark_execution_failed(
            action=action,
            execution_id=verification.verification_id,
            reason="post_execution_verification_failed",
        ).action

    return action.model_copy(
        update={
            "reasons": [*action.reasons, "verification_inconclusive"],
            "evidence": {
                **action.evidence,
                "verification_id": verification.verification_id,
                "verification_status": verification.status.value,
            },
        }
    )
