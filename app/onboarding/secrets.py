from __future__ import annotations

import os
from typing import Protocol

from app.models.sites import SecretProvider, SecretRef


class SecretResolutionError(RuntimeError):
    pass


class SecretResolver(Protocol):
    def resolve(self, reference: SecretRef) -> str:
        ...


class EnvironmentSecretResolver:
    def resolve(self, reference: SecretRef) -> str:
        if reference.provider != SecretProvider.ENVIRONMENT:
            raise SecretResolutionError(
                f"Unsupported secret provider: {reference.provider.value}"
            )
        value = os.getenv(reference.key)
        if value is None or not value.strip():
            raise SecretResolutionError(
                f"Secret is not configured: {reference.key}"
            )
        return value
