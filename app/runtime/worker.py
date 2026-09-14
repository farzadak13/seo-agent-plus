from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
import threading
from app.jobs.scheduler import JobScheduler


@dataclass
class WorkerHandle:
    scheduler: JobScheduler
    poll_interval_seconds: float
    lease_factory: object = field(default=nullcontext, repr=False)

    def __post_init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self._lifecycle_lock = threading.Lock()
        self.last_error_type = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lifecycle_lock:
            if self.running:
                if self._stop_event.is_set():
                    raise RuntimeError("Previous worker is still stopping.")
                return
            lease = self.lease_factory()
            lease.__enter__()
            self._stop_event.clear()
            self.last_error_type = None
            self._thread = threading.Thread(target=self._run, args=(lease,), name="seo-agent-worker", daemon=True)
            try:
                self._thread.start()
            except BaseException:
                lease.__exit__(None, None, None)
                self._thread = None
                raise

    def stop(self, timeout_seconds=10.0):
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
        if thread is None:
            return
        thread.join(timeout_seconds)
        # Preserve a still-running thread and its lease after a bounded shutdown.
        # start() must never revive its stop_event or launch a second worker.
        with self._lifecycle_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def _run(self, lease):
        try:
            self.scheduler.run_forever(poll_interval_seconds=self.poll_interval_seconds,
                                       stop_event=self._stop_event)
        except Exception as exc:
            self.last_error_type = type(exc).__name__
        finally:
            try:
                lease.__exit__(None, None, None)
            except Exception as exc:
                self.last_error_type = type(exc).__name__
