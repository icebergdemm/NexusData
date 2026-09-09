"""
NexusData Task Scheduler — background job queue with priority levels.

Architecture:
  - Main uvicorn event loop: handles ALL incoming HTTP requests with zero blocking
  - Task scheduler: runs in a SEPARATE DAEMON THREAD pool
  - Priority levels: CRITICAL > HIGH > NORMAL > LOW > BACKGROUND
  - Result cache: completed task outputs are cached in memory for API retrieval
  - No task can starve the API thread pool

Priority hierarchy:
  CRITICAL (0): internal — never used for user tasks
  HIGH     (1): rating recalculation (triggered by admin)
  NORMAL   (2): Sauron analysis (triggered by admin click)
  LOW      (3): rating background worker (every 5 min)
  BACKGROUND(4): log refresh, retention cleanup (every 30 min)

Usage:
  from task_scheduler import scheduler
  scheduler.submit(task_fn, priority=Priority.NORMAL, task_id="sauron_twinks")
  result = scheduler.get_result("sauron_twinks")  # None if not ready yet
"""

import threading
import queue
import time
import logging
import traceback
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass, field
from enum import IntEnum

logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Raised inside a cooperatively cancellable background task."""


_current_cancel_event: ContextVar[threading.Event | None] = ContextVar(
    "current_task_cancel_event", default=None
)


def task_cancelled() -> bool:
    """Return whether the task currently running in this thread was cancelled."""
    event = _current_cancel_event.get()
    return event.is_set() if event is not None else False


def raise_if_task_cancelled() -> None:
    if task_cancelled():
        raise TaskCancelled("task cancelled by operator")


class Priority(IntEnum):
    CRITICAL   = 0
    HIGH       = 1
    NORMAL     = 2
    LOW        = 3
    BACKGROUND = 4


@dataclass(order=True)
class _Task:
    priority: int
    submitted_at: float = field(compare=False)
    task_id: str = field(compare=False)
    fn: Callable = field(compare=False)
    args: tuple = field(compare=False)
    kwargs: dict = field(compare=False)
    timeout: Optional[float] = field(compare=False, default=None)
    cancel_event: threading.Event = field(compare=False, default_factory=threading.Event)


class TaskResult:
    __slots__ = ("task_id", "status", "data", "error", "started_at", "finished_at", "duration_ms")

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.status = "pending"   # pending | running | done | error | timeout
        self.data = None
        self.error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.duration_ms: Optional[int] = None

    def to_dict(self):
        return {
            "task_id":     self.task_id,
            "status":      self.status,
            "data":        self.data,
            "error":       self.error,
            "started_at":  self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
        }


class TaskScheduler:
    """
    Priority-based background task scheduler.

    - Uses a min-heap priority queue (lower number = higher priority).
    - Worker threads are DAEMON threads: they die with the main process.
    - Worker count is intentionally kept LOW (default 2) so background jobs
      never starve the main uvicorn thread pool.
    - Results are cached in memory for up to result_ttl seconds.
    - Duplicate task_ids: if a task with the same id is already pending/running,
      a new submission is silently dropped to prevent pile-ups.
    """

    def __init__(self, worker_count: int = 2, result_ttl: int = 600):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._results: Dict[str, TaskResult] = {}
        self._active_ids: set = set()
        self._cancel_events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._result_ttl = result_ttl
        self._worker_count = worker_count
        self._workers: list = []
        self._started = False
        # Periodic tasks: (interval_seconds, priority, task_id, fn, args, kwargs)
        self._periodic: list = []
        self._periodic_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Start worker threads. Call once at app startup."""
        if self._started:
            return
        self._started = True
        self._stop_event.clear()

        for i in range(self._worker_count):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"TaskWorker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

        # Periodic scheduler thread
        self._periodic_thread = threading.Thread(
            target=self._periodic_loop,
            name="TaskScheduler-Periodic",
            daemon=True,
        )
        self._periodic_thread.start()

        logger.info(f"[Scheduler] Started with {self._worker_count} workers")

    def stop(self, wait: bool = True, timeout: float = 8.0):
        """Signal workers to stop — and, unlike the old fire-and-forget
        version, actually wait for them to exit (bounded by `timeout`) and
        reset internal state so a later start() genuinely restarts the
        subsystem instead of being a no-op (self._started never used to be
        cleared). Used by the "workers off/restart" console command and
        workers.py's watch_runtime_state(), not just process shutdown."""
        self._stop_event.set()
        if wait:
            deadline = time.monotonic() + timeout
            for t in self._workers:
                t.join(timeout=max(0.0, deadline - time.monotonic()))
            if self._periodic_thread:
                self._periodic_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        self._workers = []
        self._periodic_thread = None
        self._started = False

    def submit(
        self,
        fn: Callable,
        priority: Priority = Priority.NORMAL,
        task_id: Optional[str] = None,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        timeout: Optional[float] = 120.0,
        deduplicate: bool = True,
    ) -> str:
        """
        Submit a task to the background queue.

        Returns task_id. If deduplicate=True and a task with the same id is
        already queued or running, the submission is dropped and the existing
        id is returned.
        """
        if task_id is None:
            task_id = f"{fn.__name__}_{time.monotonic_ns()}"

        with self._lock:
            if deduplicate and task_id in self._active_ids:
                return task_id  # already scheduled / running

            self._active_ids.add(task_id)
            cancel_event = threading.Event()
            self._cancel_events[task_id] = cancel_event
            result = TaskResult(task_id)
            self._results[task_id] = result

        task = _Task(
            priority=int(priority),
            submitted_at=time.monotonic(),
            task_id=task_id,
            fn=fn,
            args=args,
            kwargs=kwargs or {},
            timeout=timeout,
            cancel_event=cancel_event,
        )
        self._queue.put(task)
        logger.debug(f"[Scheduler] Queued task '{task_id}' priority={priority.name}")
        return task_id

    def cancel(self, task_id: str) -> bool:
        """Cancel a queued or running task without leaving its worker blocked."""
        with self._lock:
            result = self._results.get(task_id)
            if result is None or result.status in ("done", "error", "timeout", "cancelled"):
                return False
            result.status = "cancelled"
            result.error = "Cancelled by operator"
            result.finished_at = datetime.utcnow()
            if result.started_at:
                result.duration_ms = int(
                    (result.finished_at - result.started_at).total_seconds() * 1000
                )
            # The worker checks this event before executing and while the
            # cancellable task is reading its input.
            cancel_event = self._cancel_events.get(task_id)
            if cancel_event is not None:
                cancel_event.set()
            return True

    def cancel_matching(self, prefix: str) -> int:
        """Cancel every queued/running task whose ID starts with ``prefix``."""
        with self._lock:
            task_ids = [
                task_id for task_id in self._active_ids
                if task_id.startswith(prefix)
            ]
        return sum(1 for task_id in task_ids if self.cancel(task_id))

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        """Return TaskResult for task_id, or None if unknown."""
        return self._results.get(task_id)

    def status(self, task_id: str) -> str:
        r = self._results.get(task_id)
        return r.status if r else "unknown"

    def queue_size(self) -> int:
        return self._queue.qsize()

    def list_tasks(self) -> list:
        """Return summary of all known task results."""
        with self._lock:
            return [r.to_dict() for r in self._results.values()]

    def register_periodic(
        self,
        fn: Callable,
        interval_secs: int,
        priority: Priority = Priority.BACKGROUND,
        task_id: Optional[str] = None,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        timeout: Optional[float] = 120.0,
    ):
        """Register a function to run periodically in the background."""
        self._periodic.append({
            "fn": fn,
            "interval": interval_secs,
            "priority": priority,
            "task_id": task_id or fn.__name__,
            "args": args,
            "kwargs": kwargs or {},
            "timeout": timeout,
            "last_run": 0.0,
        })
        logger.info(f"[Scheduler] Registered periodic task '{task_id or fn.__name__}' every {interval_secs}s")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _worker_loop(self):
        thread_name = threading.current_thread().name
        logger.info(f"[Scheduler] Worker thread {thread_name} started")

        while not self._stop_event.is_set():
            try:
                task: _Task = self._queue.get(timeout=2.0)
            except queue.Empty:
                continue

            result = self._results.get(task.task_id)
            if result is None:
                self._queue.task_done()
                continue
            if result.status == "cancelled":
                task.cancel_event.set()
                with self._lock:
                    self._active_ids.discard(task.task_id)
                    self._cancel_events.pop(task.task_id, None)
                self._queue.task_done()
                continue

            result.status = "running"
            result.started_at = datetime.utcnow()
            logger.debug(f"[Scheduler] [{thread_name}] Running '{task.task_id}'")

            try:
                if task.timeout:
                    # Run with timeout via a sub-thread
                    output_holder = [None]
                    exc_holder = [None]

                    def _run():
                        token = _current_cancel_event.set(task.cancel_event)
                        try:
                            output_holder[0] = task.fn(*task.args, **task.kwargs)
                        except Exception as e:
                            exc_holder[0] = e
                        finally:
                            _current_cancel_event.reset(token)

                    sub = threading.Thread(target=_run, daemon=True)
                    sub.start()
                    sub.join(timeout=task.timeout)

                    if sub.is_alive():
                        result.status = "timeout"
                        result.error = f"Task exceeded {task.timeout}s timeout"
                        logger.warning(f"[Scheduler] '{task.task_id}' timed out after {task.timeout}s")
                    elif exc_holder[0]:
                        raise exc_holder[0]
                    else:
                        result.data = output_holder[0]
                        result.status = "done"
                else:
                    token = _current_cancel_event.set(task.cancel_event)
                    try:
                        result.data = task.fn(*task.args, **task.kwargs)
                    finally:
                        _current_cancel_event.reset(token)
                    result.status = "done"

            except TaskCancelled:
                result.status = "cancelled"
                result.error = "Cancelled by operator"
            except Exception as exc:
                result.status = "error"
                result.error = f"{type(exc).__name__}: {exc}"
                logger.error(f"[Scheduler] Task '{task.task_id}' failed: {exc}\n{traceback.format_exc()}")

            finally:
                result.finished_at = datetime.utcnow()
                if result.started_at:
                    result.duration_ms = int(
                        (result.finished_at - result.started_at).total_seconds() * 1000
                    )
                with self._lock:
                    self._active_ids.discard(task.task_id)
                    self._cancel_events.pop(task.task_id, None)
                self._queue.task_done()
                logger.info(
                    f"[Scheduler] [{thread_name}] '{task.task_id}' → {result.status} "
                    f"({result.duration_ms}ms)"
                )
                # Evict old results to prevent memory growth
                self._evict_old_results()

    def _periodic_loop(self):
        logger.info("[Scheduler] Periodic scheduler started")
        while not self._stop_event.is_set():
            now = time.monotonic()
            for p in self._periodic:
                if now - p["last_run"] >= p["interval"]:
                    p["last_run"] = now
                    self.submit(
                        p["fn"],
                        priority=p["priority"],
                        task_id=p["task_id"],
                        args=p["args"],
                        kwargs=p["kwargs"],
                        timeout=p["timeout"],
                        deduplicate=True,
                    )
            time.sleep(5)  # check every 5 seconds

    def _evict_old_results(self):
        """Remove completed/errored results older than result_ttl."""
        cutoff = datetime.utcnow() - timedelta(seconds=self._result_ttl)
        to_delete = []
        for tid, r in self._results.items():
            if r.status in ("done", "error", "timeout") and r.finished_at and r.finished_at < cutoff:
                to_delete.append(tid)
        for tid in to_delete:
            self._results.pop(tid, None)

    def system_snapshot(self) -> dict:
        """Introspection for the admin "Диспетчер задач" page (routes/perf_monitor.py).

        Reads ONLY this scheduler's in-memory state: no locking of resources
        beyond the brief _lock guard, no DB, safe to call from any thread.
        Periodic rows include the planned next run so the UI can show
        "когда запустится", not just "что запускалось"."""
        now = time.monotonic()
        with self._lock:
            active = []
            for r in self._results.values():
                if r.status == "running":
                    active.append(r.to_dict())
            active.sort(key=lambda r: r.get("started_at") or "")
            periodic = []
            for p in self._periodic:
                task_id = p["task_id"]
                last = self._results.get(task_id)
                last_status = last.status if last else None
                last_duration = last.duration_ms if last else None
                running = task_id in self._active_ids
                periodic.append({
                    "task_id": task_id,
                    "interval_secs": p["interval"],
                    "priority": p["priority"].name if isinstance(p["priority"], Priority) else str(p["priority"]),
                    "timeout_secs": p["timeout"],
                    # <=0 means the tick is already due and waiting for a
                    # worker (or deduplicated away from a previous run).
                    "due_in_secs": round(p["interval"] - (now - p["last_run"]), 1),
                    "running": running,
                    "last_status": last_status,
                    "last_duration_ms": last_duration,
                })
            periodic.sort(key=lambda row: row["due_in_secs"])
            recent = [
                r.to_dict() for r in self._results.values()
                if r.status in ("done", "error", "timeout", "cancelled")
            ]
            recent.sort(key=lambda r: r.get("finished_at") or "", reverse=True)
            recent = recent[:15]
        return {
            "available": self._started,
            "workers": self._worker_count,
            "queue_size": self._queue.qsize(),
            "active": active,
            "periodic": periodic,
            "recent": recent,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

# Conservative: only 2 worker threads so background jobs don't compete with
# uvicorn's async handlers or the DB connection pool.
scheduler = TaskScheduler(worker_count=2, result_ttl=600)
