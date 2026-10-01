"""Single-flight worker for the durable LLM gateway queue."""

from __future__ import annotations

import logging
import threading
import time

from services_llm_gateway import claim_next, finish, get_queue_snapshot, post_backend, set_wake_callback

logger = logging.getLogger(__name__)
TASK_ID = "ai_llm_worker"


class AILlmWorker:
    def __init__(self):
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._processing = False
        self._started = None
        self._last_client = None
        self.last_status = None

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                if not self._stop.is_set():
                    set_wake_callback(self.wake)
                    return
                self._thread.join()
            self._stop.clear()
            set_wake_callback(self.wake)
            self._thread = threading.Thread(target=self._loop, name="AILlmWorker", daemon=True)
            self._thread.start()

    def stop(self, timeout=8):
        with self._lock:
            set_wake_callback(None)
            self._stop.set(); self._wake.set()
            thread = self._thread
            if thread: thread.join(timeout)
            if thread and not thread.is_alive():
                self._thread = None
        try:
            from services_ai_client import close_ai_client
            close_ai_client()
        except Exception:
            logger.exception("Failed to close AI transport")

    def wake(self): self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            row = claim_next(self._last_client)
            if not row:
                self._wake.wait(30); self._wake.clear(); continue
            self._processing, self._started = True, time.monotonic()
            self._last_client = row.client_id
            try:
                from services_ai_tasklog import ai_task_log
                from database import SessionLocal
                db = SessionLocal(); ai_task_log(db, row.client_id, str(row.id), "claimed"); db.close()
                db = SessionLocal(); ai_task_log(db, row.client_id, str(row.id), "request_sent"); db.close()
                result, _elapsed = post_backend(row.backend, row.request_json["payload"], row.request_json["timeout"])
                finished = finish(row.id, "succeeded", result=result)
                if not finished:
                    self.last_status = "cancelled"
                    continue
                db = SessionLocal(); ai_task_log(db, row.client_id, str(row.id), "response_received"); db.close()
                db = SessionLocal(); ai_task_log(db, row.client_id, str(row.id), "validated"); db.close()
                self.last_status = "succeeded"
                self._stop.wait(1.5)
            except Exception as exc:
                finished = finish(row.id, "failed", error=exc)
                if not finished:
                    self.last_status = "cancelled"
                    continue
                try:
                    from services_ai_tasklog import ai_task_log
                    from database import SessionLocal
                    db = SessionLocal(); ai_task_log(db, row.client_id, str(row.id), "failed", str(exc)); db.close()
                except Exception:
                    logger.exception("Failed to write LLM task log")
                self.last_status = "failed"
                logger.exception("LLM request %s failed: %s", row.id, exc)
                self._stop.wait(30)
            finally:
                self._processing = False; self._started = None

    def snapshot(self):
        snap = get_queue_snapshot()
        snap.update({"task_id": TASK_ID, "thread_alive": bool(self._thread and self._thread.is_alive()),
                     "processing": self._processing, "last_status": self.last_status,
                     "elapsed_ms": int((time.monotonic() - self._started) * 1000) if self._started else None})
        return snap

_worker = AILlmWorker()

def start_ai_llm_worker(): _worker.start()
def stop_ai_llm_worker(): _worker.stop()
def wake_ai_llm_worker(): _worker.wake()
def get_ai_llm_worker_snapshot(): return _worker.snapshot()
