from __future__ import annotations

from app.runtime.container import create_runtime_app


def app_factory():
    """Uvicorn factory for the fully wired application runtime."""
    return create_runtime_app()


# Kept as a callable factory target so importing this module does not create
# connections or worker threads. Run with:
#   uvicorn app.api.main:app_factory --factory
app = app_factory
