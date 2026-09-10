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
from datetime import datetime, timedelta
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass, field
from enum import IntEnum

logger = logging.getLogger(__name__)

SLOW_TASK_WARNING_SECONDS = 5.0


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
        self._lock = threading.Lock()
        self._result_ttl = result_ttl
        self._worker_count = worker_count
        self._workers: list = []
        self._started = False
        # Periodic tasks: (interval_seconds, priority, task_id, fn, args, kwargs)
        self._periodic: list = []
        self._periodic_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._task_stats: Dict[str, dict] = {}

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
        )
        self._queue.put(task)
        logger.debug(f"[Scheduler] Queued task '{task_id}' priority={priority.name}")
        return task_id

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

    def task_stats(self) -> list:
        """Return compact lifetime counters for each known task id."""
        with self._lock:
            public_fields = (
                "task_id", "runs", "errors", "timeouts", "last_duration_ms",
                "avg_duration_ms", "max_duration_ms", "last_status",
                "last_finished_at", "queue_wait_ms",
            )
            return [
                {field: stats[field] for field in public_fields}
                for stats in self._task_stats.values()
            ]

    def record_external_run(
        self,
        task_id: str,
        *,
        status: str,
        duration_ms: int,
        queue_wait_ms: int = 0,
    ) -> None:
        """Record a run performed by a worker outside this scheduler queue.

        Some durable workers need their own execution lane so a slow external
        service cannot occupy the scheduler's small critical pool. Their
        telemetry still belongs in the same admin health surface.
        """
        with self._lock:
            self._record_task_stats_locked(
                task_id,
                status=status,
                duration_ms=duration_ms,
                queue_wait_ms=queue_wait_ms,
                finished_at=datetime.utcnow(),
            )

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

            result.status = "running"
            result.started_at = datetime.utcnow()
            started_monotonic = time.monotonic()
            logger.debug(f"[Scheduler] [{thread_name}] Running '{task.task_id}'")

            try:
                if task.timeout:
                    # Run with timeout via a sub-thread
                    output_holder = [None]
                    exc_holder = [None]

                    def _run():
                        try:
                            output_holder[0] = task.fn(*task.args, **task.kwargs)
                        except Exception as e:
                            exc_holder[0] = e

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
                    result.data = task.fn(*task.args, **task.kwargs)
                    result.status = "done"

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
                duration_ms = result.duration_ms or 0
                queue_wait_ms = max(0, int((started_monotonic - task.submitted_at) * 1000))
                with self._lock:
                    self._active_ids.discard(task.task_id)
                    self._record_task_stats_locked(
                        task.task_id,
                        status=result.status,
                        duration_ms=duration_ms,
                        queue_wait_ms=queue_wait_ms,
                        finished_at=result.finished_at,
                    )
                self._queue.task_done()
                if result.status == "done" and duration_ms >= SLOW_TASK_WARNING_SECONDS * 1000:
                    logger.warning(
                        f"[Scheduler] [{thread_name}] '{task.task_id}' completed slowly "
                        f"({result.duration_ms}ms)"
                    )
                # Evict old results to prevent memory growth
                self._evict_old_results()

    def _record_task_stats_locked(
        self,
        task_id: str,
        *,
        status: str,
        duration_ms: int,
        queue_wait_ms: int,
        finished_at: Optional[datetime],
    ) -> None:
        stats = self._task_stats.setdefault(task_id, {
            "task_id": task_id,
            "runs": 0,
            "errors": 0,
            "timeouts": 0,
            "total_duration_ms": 0,
            "last_duration_ms": None,
            "avg_duration_ms": None,
            "max_duration_ms": None,
            "last_status": None,
            "last_finished_at": None,
            "queue_wait_ms": None,
        })
        duration_ms = max(0, int(duration_ms or 0))
        stats["runs"] += 1
        stats["errors"] += int(status in {"error", "failed"})
        stats["timeouts"] += int(status == "timeout")
        stats["total_duration_ms"] += duration_ms
        stats["last_duration_ms"] = duration_ms
        stats["avg_duration_ms"] = round(stats["total_duration_ms"] / stats["runs"], 1)
        stats["max_duration_ms"] = max(stats["max_duration_ms"] or 0, duration_ms)
        stats["last_status"] = status
        stats["last_finished_at"] = finished_at.isoformat() if finished_at else None
        stats["queue_wait_ms"] = max(0, int(queue_wait_ms or 0))

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


# ── Singleton ─────────────────────────────────────────────────────────────────

# Conservative: only 2 worker threads so background jobs don't compete with
# uvicorn's async handlers or the DB connection pool.
scheduler = TaskScheduler(worker_count=2, result_ttl=600)
