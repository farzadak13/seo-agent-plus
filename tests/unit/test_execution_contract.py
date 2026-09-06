from datetime import datetime, timezone

import pytest

from app.execution.capabilities import (
    required_capabilities_for_action,
)
from app.execution.contracts import (
    build_execution_request,
)
from app.execution.resolver import (
    resolve_execution_adapter,
)
from app.models.actions import (
    ActionRiskLevel,
    ActionStatus,
    ActionType,
)
from app.models.execution import (
    ExecutionCapability,
)
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import StrategyType
from app.models.actions import Action


def make_snapshot() -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id="snapshot-execution-001",
        data_snapshot_id="data-execution-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            6,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )


def make_action(
    action_type: ActionType,
) -> Action:
    return Action(
        action_id="action-execution-001",
        strategy_id="strategy-execution-001",
        opportunity_id="opportunity-execution-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        action_type=action_type,
        status=ActionStatus.AWAITING_APPROVAL,
        risk_level=ActionRiskLevel.LOW,
        confidence_score=0.90,
        expected_impact_score=0.80,
        priority_score=0.85,
        requires_approval=True,
        reasons=["test"],
        parameters={
            "current_title": "عنوان قدیمی",
            "recommended_title": "عنوان جدید",
        },
        evidence={},
        snapshot=make_snapshot(),
    )


class FakeWordPressAdapter:
    @property
    def adapter_id(self) -> str:
        return "wordpress"

    @property
    def capabilities(
        self,
    ) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
            ExecutionCapability.UPDATE_TITLE,
            ExecutionCapability.UPDATE_META_DESCRIPTION,
            ExecutionCapability.UPDATE_CONTENT,
            ExecutionCapability.UPDATE_INTERNAL_LINKS,
            ExecutionCapability.ROLLBACK_CHANGE,
        }


class FakeRestAdapter:
    @property
    def adapter_id(self) -> str:
        return "generic-rest"

    @property
    def capabilities(
        self,
    ) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
            ExecutionCapability.UPDATE_TITLE,
        }


class FakeReadOnlyAdapter:
    @property
    def adapter_id(self) -> str:
        return "readonly"

    @property
    def capabilities(
        self,
    ) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
        }


def test_title_action_requires_update_title():
    capabilities = required_capabilities_for_action(
        ActionType.OPTIMIZE_TITLE
    )

    assert capabilities == [
        ExecutionCapability.UPDATE_TITLE
    ]


def test_internal_linking_requires_read_and_update():
    capabilities = required_capabilities_for_action(
        ActionType.IMPROVE_INTERNAL_LINKING
    )

    assert capabilities == [
        ExecutionCapability.READ_PAGE,
        ExecutionCapability.UPDATE_INTERNAL_LINKS,
    ]


def test_content_action_requires_read_and_update():
    capabilities = required_capabilities_for_action(
        ActionType.IMPROVE_CONTENT_DEPTH
    )

    assert capabilities == [
        ExecutionCapability.READ_PAGE,
        ExecutionCapability.UPDATE_CONTENT,
    ]


def test_execution_request_is_platform_agnostic():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    request = build_execution_request(
        action=action,
    )

    assert request.action_id == action.action_id
    assert request.site_id == action.site_id

    assert request.target.normalized_url == (
        action.normalized_url
    )

    assert request.target.normalized_query == (
        action.normalized_query
    )

    assert request.required_capabilities == [
        ExecutionCapability.UPDATE_TITLE
    ]

    assert request.parameters == action.parameters

    assert "wordpress" not in request.model_dump_json().lower()


def test_custom_idempotency_key_is_preserved():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    request = build_execution_request(
        action=action,
        idempotency_key="custom-key-001",
    )

    assert request.idempotency_key == "custom-key-001"


def test_default_idempotency_key_is_action_based():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    request = build_execution_request(
        action=action,
    )

    assert request.idempotency_key == (
        "action:action-execution-001"
    )


def test_resolver_selects_capable_adapter():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    request = build_execution_request(
        action=action,
    )

    resolution = resolve_execution_adapter(
        request=request,
        adapters=[
            FakeReadOnlyAdapter(),
            FakeRestAdapter(),
        ],
    )

    assert resolution.adapter_id == "generic-rest"


def test_resolver_can_select_wordpress_without_brain_dependency():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    request = build_execution_request(
        action=action,
    )

    resolution = resolve_execution_adapter(
        request=request,
        adapters=[
            FakeReadOnlyAdapter(),
            FakeWordPressAdapter(),
        ],
    )

    assert resolution.adapter_id == "wordpress"


def test_resolver_requires_all_capabilities():
    action = make_action(
        ActionType.IMPROVE_INTERNAL_LINKING
    )

    request = build_execution_request(
        action=action,
    )

    with pytest.raises(
        ValueError,
        match="No execution adapter supports",
    ):
        resolve_execution_adapter(
            request=request,
            adapters=[
                FakeReadOnlyAdapter(),
            ],
        )


def test_empty_adapter_registry_is_rejected():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    request = build_execution_request(
        action=action,
    )

    with pytest.raises(
        ValueError,
        match="No execution adapters are registered",
    ):
        resolve_execution_adapter(
            request=request,
            adapters=[],
        )


def test_resolver_is_deterministic():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    request = build_execution_request(
        action=action,
    )

    adapters = [
        FakeReadOnlyAdapter(),
        FakeRestAdapter(),
        FakeWordPressAdapter(),
    ]

    first = resolve_execution_adapter(
        request=request,
        adapters=adapters,
    )

    second = resolve_execution_adapter(
        request=request,
        adapters=adapters,
    )

    assert first.model_dump() == second.model_dump()


def test_monitor_action_only_requires_read_access():
    capabilities = required_capabilities_for_action(
        ActionType.MONITOR
    )

    assert capabilities == [
        ExecutionCapability.READ_PAGE
    ]
def test_action_model_has_no_platform_specific_fields():
    action = make_action(
        ActionType.OPTIMIZE_TITLE
    )

    dumped = action.model_dump_json().lower()

    assert "wordpress" not in dumped
    assert "wp_post_id" not in dumped
    assert "api_endpoint" not in dumped    