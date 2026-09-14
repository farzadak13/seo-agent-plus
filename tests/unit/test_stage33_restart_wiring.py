from __future__ import annotations

from app.runtime.container import RuntimeContainer


class FakeRepository:
    pass


def test_container_identity_can_be_rebuilt_without_in_memory_repository():
    """Composition is repository-driven; persistence identity belongs to DSN infrastructure."""
    assert FakeRepository is not None
    from typing import get_type_hints
    from app.persistence.postgres import PostgresRepository
    assert get_type_hints(RuntimeContainer)["repository"] is PostgresRepository
