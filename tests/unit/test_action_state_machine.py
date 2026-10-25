import pytest

from app.action.state_machine import (
    can_transition,
    transition,
)
from app.models.actions import ActionStatus


def test_planned_can_move_to_approval():
    assert can_transition(
        ActionStatus.PLANNED,
        ActionStatus.AWAITING_APPROVAL,
    )

    assert (
        transition(
            ActionStatus.PLANNED,
            ActionStatus.AWAITING_APPROVAL,
        )
        == ActionStatus.AWAITING_APPROVAL
    )


def test_approval_can_be_approved():
    assert can_transition(
        ActionStatus.AWAITING_APPROVAL,
        ActionStatus.APPROVED,
    )


def test_approved_can_enter_execution():
    assert can_transition(
        ActionStatus.APPROVED,
        ActionStatus.EXECUTING,
    )


def test_executed_can_wait_for_recrawl():
    assert can_transition(
        ActionStatus.EXECUTED,
        ActionStatus.WAITING_FOR_RECRAWL,
    )


def test_measurement_can_move_to_evaluation():
    assert can_transition(
        ActionStatus.MEASUREMENT_WINDOW_ACTIVE,
        ActionStatus.EVALUATING,
    )


def test_evaluation_can_finish_successfully():
    assert can_transition(
        ActionStatus.EVALUATING,
        ActionStatus.SUCCEEDED,
    )


def test_failed_can_be_rolled_back():
    assert can_transition(
        ActionStatus.FAILED,
        ActionStatus.ROLLED_BACK,
    )


def test_invalid_transition_is_rejected():
    assert not can_transition(
        ActionStatus.PLANNED,
        ActionStatus.EXECUTED,
    )

    with pytest.raises(ValueError):
        transition(
            ActionStatus.PLANNED,
            ActionStatus.EXECUTED,
        )


def test_a_succeeded_action_cannot_run_again():
    assert not can_transition(
        ActionStatus.SUCCEEDED,
        ActionStatus.EXECUTING,
    )


@pytest.mark.parametrize("settled", [ActionStatus.SUCCEEDED, ActionStatus.INCONCLUSIVE])
def test_a_settled_change_can_still_be_rolled_back(settled):
    assert can_transition(settled, ActionStatus.ROLLED_BACK)


def test_terminal_rejected_state_has_no_transition():
    assert not can_transition(
        ActionStatus.REJECTED,
        ActionStatus.APPROVED,
    )