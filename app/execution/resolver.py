from typing import Protocol

from app.models.execution import (
    ExecutionCapability,
    ExecutionRequest,
    ExecutionResolution,
)


class ExecutionAdapter(Protocol):
    @property
    def adapter_id(self) -> str:
        ...

    @property
    def capabilities(self) -> set[ExecutionCapability]:
        ...


def _missing_capabilities(
    request: ExecutionRequest,
    adapter: ExecutionAdapter,
) -> list[ExecutionCapability]:
    supported = adapter.capabilities

    return [
        capability
        for capability in request.required_capabilities
        if capability not in supported
    ]


def resolve_execution_adapter(
    *,
    request: ExecutionRequest,
    adapters: list[ExecutionAdapter],
) -> ExecutionResolution:
    if not adapters:
        raise ValueError(
            "No execution adapters are registered."
        )

    for adapter in adapters:
        missing = _missing_capabilities(
            request=request,
            adapter=adapter,
        )

        if not missing:
            return ExecutionResolution(
                adapter_id=adapter.adapter_id,
                supported_capabilities=sorted(
                    adapter.capabilities,
                    key=lambda item: item.value,
                ),
                request=request,
            )

    required = ", ".join(
        capability.value
        for capability in request.required_capabilities
    )

    raise ValueError(
        "No execution adapter supports the required "
        f"capabilities: {required}"
    )