from __future__ import annotations

import logging

from app.runtime.container import create_runtime_app

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    """Send the application's own logs to stderr, which systemd puts in journald.

    Without this they reached the journal only through logging's last-resort
    handler: warnings only, with no level or source, and silently gone the day
    a logging config disables existing loggers. basicConfig does nothing if
    the root logger is already configured, so a deliberate --log-config wins.
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def app_factory():
    """Uvicorn factory for the fully wired application runtime."""
    configure_logging()
    return create_runtime_app()


# Kept as a callable factory target so importing this module does not create
# connections or worker threads. Run with:
#   uvicorn app.api.main:app_factory --factory
app = app_factory
