"""Dedicated execution lane for durable AI dossier generation."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

TASK_ID = "ai_dossier_worker"
RECOVERY_INTERVAL_SECONDS = 30.0
SUCCESS_COOLDOWN_SECONDS = 1.5
ERROR_BACKOFF_SECONDS = 30.0
SLOW_WARNING_SECONDS = 5.0


class AIDossierWorker:
    """Process one durable dossier at a time without occupying scheduler workers."""

    def __init__(
        self,
        process: Optional[Callable[[], dict]] = None,
        *,
        recovery_interval: float = RECOVERY_INTERVAL_SECONDS,
        cooldown: float = SUCCESS_COOLDOWN_SECONDS,
        error_backoff: float = ERROR_BACKOFF_SECONDS,
    ):
        self._process = process or self._default_process
        self._recovery_interval = recovery_interval
        self._cooldown = cooldown
        self._error_backoff = error_backoff
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self._processing = False
        self._restart_requested = False
        self.last_status: Optional[str] = None

    @staticmethod
    def _default_process() -> dict:
        from services_ai_dossier import process_one_dossier

        return process_one_dossier()

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                if self._stop_event.is_set():
                    self._restart_requested = True
                return
            self._restart_requested = False
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="AIDossierWorker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread:
            thread.join(timeout=max(0.0, timeout))
        with self._state_lock:
            if self._thread is not None and not self._thread.is_alive():
                self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def _wait(self) -> bool:
        return self._wake_event.wait(self._recovery_interval)

    def _sleep_cooldown(self, seconds: float) -> None:
        self._stop_event.wait(seconds)

    def _run_once(self, *, wait_for_signal: bool = True) -> Optional[dict]:
        if self._processing or self._stop_event.is_set():
            return None
        if wait_for_signal:
            self._wait()
        if self._stop_event.is_set():
            return None
        if wait_for_signal:
            self._wake_event.clear()
        if self._processing:
            return None

        self._processing = True
        started = time.monotonic()
        try:
            result = self._process() or {"status": "idle"}
            raw_status = str(result.get("status") or "done")
            status = "done" if raw_status == "succeeded" else raw_status
            self.last_status = raw_status
            duration_ms = int((time.monotonic() - started) * 1000)
            self._record_stats(status, duration_ms)
            if raw_status == "failed":
                self._sleep_cooldown(self._error_backoff)
            elif raw_status != "idle":
                self._sleep_cooldown(self._cooldown)
            if raw_status != "idle" and duration_ms >= SLOW_WARNING_SECONDS * 1000:
                logger.warning(
                    "AI dossier worker completed slowly (%sms, status=%s)",
                    duration_ms,
                    raw_status,
                )
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            self.last_status = "error"
            self._record_stats("error", duration_ms)
            logger.exception("AI dossier worker failed: %s", exc)
            self._sleep_cooldown(self._error_backoff)
            return {"status": "error", "error": str(exc)}
        finally:
            self._processing = False

    def _record_stats(self, status: str, duration_ms: int) -> None:
        try:
            from task_scheduler import scheduler

            scheduler.record_external_run(
                TASK_ID,
                status=status,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.exception("Failed to record AI dossier worker stats")

    def _loop(self) -> None:
        process_immediately = False
        while not self._stop_event.is_set():
            result = self._run_once(wait_for_signal=not process_immediately)
            process_immediately = bool(
                result and result.get("status") not in {"idle", None}
            )
        with self._state_lock:
            should_restart = self._restart_requested
            self._restart_requested = False
            if self._thread is threading.current_thread():
                self._thread = None
        if should_restart:
            self.start()


_worker: Optional[AIDossierWorker] = None
_worker_lock = threading.Lock()


def start_ai_dossier_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = AIDossierWorker()
        _worker.start()


def stop_ai_dossier_worker() -> None:
    with _worker_lock:
        worker = _worker
    if worker:
        worker.stop()


def wake_ai_dossier_worker() -> None:
    with _worker_lock:
        worker = _worker
    if worker:
        worker.wake()
