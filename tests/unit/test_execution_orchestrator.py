from datetime import datetime, timezone

import pytest

from app.execution.orchestrator import (
    ExecutionOrchestratorError,
    approve_action,
    execute_approved_action,
)
from app.models.actions import (
    Action,
    ActionRiskLevel,
    ActionStatus,
    ActionType,
)
from app.models.execution import ExecutionCapability
from app.models.execution_run import (
    ExecutionRunResult,
    ExecutionRunStatus,
)
from app.models.site_adapter import (
    AdapterOperation,
    AdapterOperationResult,
    SitePage,
)
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-stage29",
        data_snapshot_id="data-stage29",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            9,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )


def make_action(
    *,
    action_type: ActionType = ActionType.OPTIMIZE_TITLE,
    status: ActionStatus = ActionStatus.AWAITING_APPROVAL,
    parameters: dict | None = None,
) -> Action:
    if parameters is None:
        parameters = {
            "current_title": "Old title",
            "recommended_title": "New title",
        }

    return Action(
        action_id="action-stage29-001",
        strategy_id="strategy-stage29-001",
        opportunity_id="opportunity-stage29-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="seo",
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        action_type=action_type,
        status=status,
        risk_level=ActionRiskLevel.LOW,
        confidence_score=0.90,
        expected_impact_score=0.80,
        priority_score=0.85,
        requires_approval=True,
        reasons=["stage29-test"],
        parameters=parameters,
        evidence={},
        snapshot=make_snapshot(),
    )


class FakeSiteAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def adapter_id(self) -> str:
        return "fake-site"

    @property
    def capabilities(self) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
            ExecutionCapability.UPDATE_TITLE,
            ExecutionCapability.UPDATE_CONTENT,
            ExecutionCapability.UPDATE_INTERNAL_LINKS,
        }

    def read_page(
        self,
        *,
        site_id: str,
        normalized_url: str,
    ) -> SitePage:
        self.calls.append(
            {
                "operation": "read_page",
                "site_id": site_id,
                "normalized_url": normalized_url,
            }
        )

        return SitePage(
            site_id=site_id,
            normalized_url=normalized_url,
            title="Current title",
            meta_description="Current description",
            content="<p>Current content</p>",
            status="publish",
        )

    def update_title(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_title: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        self.calls.append(
            {
                "operation": "update_title",
                "site_id": site_id,
                "normalized_url": normalized_url,
                "new_title": new_title,
                "idempotency_key": idempotency_key,
            }
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_TITLE,
            site_id=site_id,
            normalized_url=normalized_url,
            message="title updated",
            change_id="change-stage29-001",
            previous_value="Old title",
            new_value=new_title,
        )

    def update_meta_description(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_meta_description: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        self.calls.append(
            {
                "operation": "update_meta_description",
            }
        )

        return AdapterOperationResult(
            success=True,
            operation=(
                AdapterOperation.UPDATE_META_DESCRIPTION
            ),
            site_id=site_id,
            normalized_url=normalized_url,
            message="meta updated",
            change_id="change-meta-001",
            new_value=new_meta_description,
        )

    def update_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        self.calls.append(
            {
                "operation": "update_content",
                "new_content": new_content,
            }
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_CONTENT,
            site_id=site_id,
            normalized_url=normalized_url,
            message="content updated",
            change_id="change-content-001",
            new_value=new_content,
        )

    def update_internal_links(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        self.calls.append(
            {
                "operation": "update_internal_links",
                "new_content": new_content,
            }
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_INTERNAL_LINKS,
            site_id=site_id,
            normalized_url=normalized_url,
            message="links updated",
            change_id="change-links-001",
            new_value=new_content,
        )

    def publish_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        self.calls.append(
            {
                "operation": "publish_content",
            }
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.PUBLISH_CONTENT,
            site_id=site_id,
            normalized_url=normalized_url,
            message="published",
            change_id="change-publish-001",
        )

    def rollback_change(
        self,
        *,
        request,
    ) -> AdapterOperationResult:
        self.calls.append(
            {
                "operation": "rollback_change",
            }
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.ROLLBACK_CHANGE,
            site_id=request.site_id,
            normalized_url=request.normalized_url,
            message="rolled back",
            change_id=request.change_id,
        )


class FakeReadOnlyAdapter:
    @property
    def adapter_id(self) -> str:
        return "readonly"

    @property
    def capabilities(self) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
        }


class FailingAdapter(FakeSiteAdapter):
    def update_title(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_title: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        self.calls.append(
            {
                "operation": "update_title",
            }
        )

        raise RuntimeError(
            "external site unavailable"
        )


def test_approval_moves_action_to_approved():
    action = make_action()

    approved = approve_action(action)

    assert approved.status == ActionStatus.APPROVED
    assert approved.action_id == action.action_id


def test_planned_action_can_be_approved():
    action = make_action(
        status=ActionStatus.PLANNED,
    )

    approved = approve_action(action)

    assert approved.status == ActionStatus.APPROVED


def test_non_approvable_action_is_rejected():
    action = make_action(
        status=ActionStatus.EXECUTED,
    )

    with pytest.raises(
        ExecutionOrchestratorError,
        match="cannot be approved",
    ):
        approve_action(action)


def test_execution_requires_approval():
    action = make_action(
        status=ActionStatus.AWAITING_APPROVAL,
    )

    with pytest.raises(
        ExecutionOrchestratorError,
        match="Only approved actions",
    ):
        execute_approved_action(
            action=action,
            adapters=[FakeSiteAdapter()],
        )


def test_title_action_executes_end_to_end():
    adapter = FakeSiteAdapter()

    action = make_action(
        status=ActionStatus.APPROVED,
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert isinstance(
        result,
        ExecutionRunResult,
    )

    assert result.status == (
        ExecutionRunStatus.EXECUTED
    )

    assert result.adapter_id == "fake-site"

    assert result.action.status == (
        ActionStatus.EXECUTED
    )

    assert result.operation_result is not None

    assert result.operation_result.operation == (
        AdapterOperation.UPDATE_TITLE
    )

    assert result.operation_result.new_value == (
        "New title"
    )


def test_title_action_passes_idempotency_key():
    adapter = FakeSiteAdapter()

    action = make_action(
        status=ActionStatus.APPROVED,
    )

    execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert adapter.calls[0][
        "operation"
    ] == "update_title"

    assert adapter.calls[0][
        "idempotency_key"
    ] == "action:action-stage29-001"


def test_monitor_action_reads_page():
    adapter = FakeSiteAdapter()

    action = make_action(
        action_type=ActionType.MONITOR,
        status=ActionStatus.APPROVED,
        parameters={},
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert result.status == (
        ExecutionRunStatus.EXECUTED
    )

    assert result.page is not None
    assert result.page.title == (
        "Current title"
    )

    assert result.operation_result is None


def test_cannibalization_investigation_reads_page():
    adapter = FakeSiteAdapter()

    action = make_action(
        action_type=(
            ActionType.INVESTIGATE_CANNIBALIZATION
        ),
        status=ActionStatus.APPROVED,
        parameters={},
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert result.status == (
        ExecutionRunStatus.EXECUTED
    )

    assert result.page is not None


def test_content_action_executes():
    adapter = FakeSiteAdapter()

    action = make_action(
        action_type=ActionType.IMPROVE_CONTENT_DEPTH,
        status=ActionStatus.APPROVED,
        parameters={
            "new_content": "<p>New content</p>",
        },
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert result.status == (
        ExecutionRunStatus.EXECUTED
    )

    assert result.operation_result is not None
    assert (
        result.operation_result.operation
        == AdapterOperation.UPDATE_CONTENT
    )


def test_internal_linking_action_executes():
    adapter = FakeSiteAdapter()

    action = make_action(
        action_type=ActionType.IMPROVE_INTERNAL_LINKING,
        status=ActionStatus.APPROVED,
        parameters={
            "new_content": (
                "<p>Content with "
                "<a href='/other'>link</a></p>"
            ),
        },
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert result.status == (
        ExecutionRunStatus.EXECUTED
    )

    assert result.operation_result is not None

    assert (
        result.operation_result.operation
        == AdapterOperation.UPDATE_INTERNAL_LINKS
    )


def test_missing_required_title_parameter_fails_without_adapter_call():
    adapter = FakeSiteAdapter()

    action = make_action(
        status=ActionStatus.APPROVED,
        parameters={},
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert result.status == (
        ExecutionRunStatus.FAILED
    )

    assert result.action.status == (
        ActionStatus.FAILED
    )

    assert "recommended_title" in (
        result.error or ""
    )

    assert adapter.calls == []


def test_incapable_adapter_causes_failure():
    action = make_action(
        status=ActionStatus.APPROVED,
    )

    result = execute_approved_action(
        action=action,
        adapters=[FakeReadOnlyAdapter()],
    )

    assert result.status == (
        ExecutionRunStatus.FAILED
    )

    assert result.action.status == (
        ActionStatus.FAILED
    )

    assert "No execution adapter supports" in (
        result.error or ""
    )


def test_external_adapter_failure_becomes_failed_action():
    action = make_action(
        status=ActionStatus.APPROVED,
    )

    result = execute_approved_action(
        action=action,
        adapters=[FailingAdapter()],
    )

    assert result.status == (
        ExecutionRunStatus.FAILED
    )

    assert result.action.status == (
        ActionStatus.FAILED
    )

    assert "external site unavailable" in (
        result.error or ""
    )


def test_adapter_selection_is_capability_based():
    read_only = FakeReadOnlyAdapter()
    capable = FakeSiteAdapter()

    action = make_action(
        status=ActionStatus.APPROVED,
    )

    result = execute_approved_action(
        action=action,
        adapters=[
            read_only,
            capable,
        ],
    )

    assert result.adapter_id == "fake-site"


def test_execution_result_preserves_original_request():
    adapter = FakeSiteAdapter()

    action = make_action(
        status=ActionStatus.APPROVED,
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert result.request.action_id == (
        action.action_id
    )

    assert result.request.site_id == (
        action.site_id
    )

    assert result.request.target.normalized_url == (
        action.normalized_url
    )


def test_execution_does_not_auto_advance_to_recrawl():
    adapter = FakeSiteAdapter()

    action = make_action(
        status=ActionStatus.APPROVED,
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    assert result.action.status == (
        ActionStatus.EXECUTED
    )

    assert result.action.status != (
        ActionStatus.WAITING_FOR_RECRAWL
    )


def test_execution_result_is_platform_agnostic():
    adapter = FakeSiteAdapter()

    action = make_action(
        status=ActionStatus.APPROVED,
    )

    result = execute_approved_action(
        action=action,
        adapters=[adapter],
    )

    dumped = result.model_dump_json().lower()

    assert "wordpress" not in dumped
    assert "wp_post_id" not in dumped