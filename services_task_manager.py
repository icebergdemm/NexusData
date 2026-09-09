"""Snapshot engine for the admin "Диспетчер задач" page (routes/perf_monitor.py).

Collects, without touching the database:
  1. Every process the container can see (psutil.process_iter) with
     per-process CPU%, RSS, status — the Windows Task Manager analogue.
     CPU% is a wall-time delta of cumulative cpu_times between calls, kept
     in a module-level cache, so any polling frequency works.
  2. Threads of THIS process (the API server) with per-thread CPU% (psutil
     threads()' user/system totals) and a live stack summary via
     sys._current_frames() — so the page shows WHAT a thread is doing right
     now, not just its name.
  3. The state of every long-lived background subsystem (log/replay/
     playtime workers, scheduler, error-log/perf writers) from their own
     running flags.

Cost: one process_iter + /proc reads ~ a few milliseconds per request; the
page polls it every ~2s, comparable to the game server's own report cadence.
"""

import logging
import os
import sys
import threading
import time

import psutil

logger = logging.getLogger(__name__)

_CMDLINE_LIMIT = 200
_STACK_DEPTH = 3
_MAX_PROCESSES = 150
_MAX_THREADS = 60

# Snapshot TTL: the page polls every ~2s, and collecting processes is the
# only real work here. Multiple viewers (or a lost in-flight poll racing a
# retry) reuse the most recent snapshot instead of paging through the
# process table again.
_SNAPSHOT_TTL_S = 1.5
_snapshot_lock = threading.Lock()
_snapshot_cache: dict | None = None
_snapshot_at = 0.0

_cpu_cache_lock = threading.Lock()
# pid/tid -> (monotonic_snapshot_time, cumulative_cpu_seconds)
_cid_cpu_cache: dict = {}


def _cpu_pct_for(key: str, total_cpu_seconds: float, now: float) -> tuple[float, float]:
    """Returns (cpu_pct_normalized_to_all_cores, cached_before) — pct over the
    time since the previous call with the same key."""
    with _cpu_cache_lock:
        prev = _cid_cpu_cache.get(key)
        _cid_cpu_cache[key] = (now, total_cpu_seconds)
    if prev is None:
        return 0.0, total_cpu_seconds
    prev_at, prev_total = prev
    dt = now - prev_at
    if dt <= 0.05:
        return 0.0, total_cpu_seconds
    delta = max(0.0, total_cpu_seconds - prev_total)
    cores = psutil.cpu_count() or 1
    return delta / dt / cores * 100.0, total_cpu_seconds


def _prune_cpu_cache(alive_keys: set) -> None:
    with _cpu_cache_lock:
        for key in list(_cid_cpu_cache.keys()):
            if key not in alive_keys:
                del _cid_cpu_cache[key]


def _short_cmdline(proc) -> tuple[str, bool]:
    try:
        line = " ".join(part for part in proc.cmdline() if part).strip()
    except Exception:
        line = ""
    if not line:
        line = str(proc.info.get("name") or proc.pid) if hasattr(proc, "info") else str(proc.pid)
    is_python = "python" in line.lower()
    return (line[:_CMDLINE_LIMIT] + ("…" if len(line) > _CMDLINE_LIMIT else "")), is_python


def collect_processes() -> list[dict]:
    """Every visible process, newest CPU first. Own process and all its
    descendants are flagged so the UI can highlight the app lineage."""
    try:
        me = psutil.Process()
    except psutil.Error:
        me = None
    own_pid = me.pid if me else os.getpid()
    now = time.monotonic()
    entries: list[dict] = []
    for proc in psutil.process_iter(None):
        try:
            with proc.oneshot():
                pid = proc.pid
                ppid = proc.ppid()
                name = proc.name()
                status = proc.status()
                cpu_times = proc.cpu_times()
                rss = proc.memory_info().rss
                mem_pct = proc.memory_percent()
                threads = proc.num_threads()
                cmd, is_python = _short_cmdline(proc)
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue
        cpu_pct, total_cpu = _cpu_pct_for(f"pid:{pid}", cpu_times.user + cpu_times.system, now)
        entries.append({
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "status": status,
            "cpu_pct": round(cpu_pct, 1),
            "cpu_total_s": round(total_cpu, 1),
            "mem_rss_mb": round(rss / 1024 / 1024, 1),
            "mem_pct": round(mem_pct, 1),
            "threads": threads,
            "cmd": cmd,
            "is_python": is_python,
            "is_own": False,
            "own_child": False,
        })
        if len(entries) >= _MAX_PROCESSES:
            break

    # Mark our lineage: the API process itself plus any descendant
    # (scheduler_process.py / uvicorn workers in multi-worker mode).
    children: set[int] = set()
    if me is not None:
        try:
            frontier = me.children(recursive=True)
            for child in frontier:
                children.add(child.pid)
        except Exception:
            pass
    for row in entries:
        row["is_own"] = row["pid"] == own_pid
        row["own_child"] = row["pid"] in children
    _prune_cpu_cache({f"pid:{row['pid']}" for row in entries})
    entries.sort(key=lambda row: -row["cpu_pct"])
    return entries


def _thread_state(tid: int) -> str:
    """/proc state char → word. Empty on platforms without /proc (Windows)."""
    try:
        with open(f"/proc/{os.getpid()}/task/{tid}/stat", "rb") as f:
            raw = f.read().decode("ascii", errors="replace")
        # field 3 (state) — skip the comm field correctly: it's in parens.
        open_idx = raw.find("(")
        close_idx = raw.rfind(")")
        tail = raw[close_idx + 2:].split(" ")
        char = tail[0] if tail else ""
        return {"R": "running", "S": "sleeping", "D": "disk-wait",
                "Z": "zombie", "T": "stopped", "I": "idle"}.get(char, char or "")
    except Exception:
        return ""


def _stack_summary(tid: int) -> str:
    try:
        frame = sys._current_frames().get(tid)
    except Exception:
        frame = None
    if frame is None:
        return ""
    parts = []
    while frame is not None and len(parts) < _STACK_DEPTH:
        code = frame.f_code
        filename = os.path.basename(code.co_filename)
        parts.append(f"{code.co_name} ({filename}:{frame.f_lineno})")
        frame = frame.f_back
    return " ← ".join(parts)


def collect_app_threads() -> dict:
    """Threads of the API process with %CPU, state, and what it's executing."""
    try:
        proc = psutil.Process()
    except psutil.Error:
        return {"threads": [], "rss_mb": 0.0}
    now = time.monotonic()
    thread_names = {}
    for t in threading.enumerate():
        try:
            thread_names[t.native_id] = t.name
        except Exception:
            pass
    rows = []
    try:
        proc_threads = proc.threads()
    except Exception as exc:
        logger.debug("collect_app_threads: threads() unavailable: %s", exc)
        proc_threads = []
    for stat in proc_threads:
        key = f"tid:{stat.id}"
        cpu_pct, total_cpu = _cpu_pct_for(
            key, getattr(stat, "user", 0.0) + getattr(stat, "system", 0.0), now,
        )
        rows.append({
            "tid": stat.id,
            "name": thread_names.get(stat.id, ""),
            "cpu_pct": round(cpu_pct, 1),
            "cpu_total_s": round(total_cpu, 1),
            "state": _thread_state(stat.id),
            "stack": _stack_summary(stat.id),
            "is_main": thread_names.get(stat.id) == "MainThread",
        })
    _prune_cpu_cache({f"tid:{row['tid']}" for row in rows} | set())
    rows.sort(key=lambda row: (-row["cpu_pct"], -row["cpu_total_s"]))
    return {
        "threads": rows[:_MAX_THREADS],
        "rss_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
    }


def collect_subsystems() -> list[dict]:
    """Long-lived background subsystem loops and whether they run now."""
    rows = []
    try:
        import task_scheduler
        rows.append({
            "name": "TaskScheduler",
            "running": task_scheduler.scheduler._started,
            "detail": f"{task_scheduler.scheduler._worker_count} воркера фоновых задач",
            "source": "task_scheduler.py",
        })
    except Exception:
        rows.append({"name": "TaskScheduler", "running": False, "detail": "не загружен", "source": "task_scheduler.py"})
    try:
        import services_logs
        rows.append({
            "name": "LogWorker (SFTP→SQLite)",
            "running": bool(getattr(services_logs, "_log_worker_running", False)),
            "detail": f"интервал {getattr(services_logs, '_log_refresh_interval', '?')} с",
            "source": "services_logs.py",
        })
    except Exception:
        pass
    try:
        import services_replays
        rows.append({
            "name": "ReplayWorker (ретенция)",
            "running": bool(getattr(services_replays, "_replay_worker_running", False)),
            "detail": f"каждые {getattr(services_replays, '_REPLAY_CLEANUP_INTERVAL', '?') // 3600} ч",
            "source": "services_replays.py",
        })
    except Exception:
        pass
    try:
        import services_playtime_stats
        rows.append({
            "name": "PlaytimeStatsWorker",
            "running": bool(getattr(services_playtime_stats, "_playtime_stats_worker_running", False)),
            "detail": "агрегация игрового времени",
            "source": "services_playtime_stats.py",
        })
    except Exception:
        pass
    try:
        import services_error_log
        rows.append({
            "name": "ErrorLogWriter (DB)",
            "running": bool(getattr(services_error_log, "_worker_started", False)),
            "detail": " персистентный журнал ошибок",
            "source": "services_error_log.py",
        })
    except Exception:
        pass
    try:
        import services_perf_monitor
        rows.append({
            "name": "PerfAnomalyWriter (DB)",
            "running": bool(getattr(services_perf_monitor, "_anomaly_worker_started", False)),
            "detail": "аномалии перф-монитора",
            "source": "services_perf_monitor.py",
        })
    except Exception:
        pass
    return rows


def build_snapshot() -> dict:
    """One full Task-Manager snapshot for the admin UI (TTL-cached)."""
    global _snapshot_cache, _snapshot_at
    now = time.monotonic()
    with _snapshot_lock:
        if _snapshot_cache is not None and now - _snapshot_at < _SNAPSHOT_TTL_S:
            return _snapshot_cache

    from datetime import datetime

    scheduler_data = {"available": False}
    try:
        from task_scheduler import scheduler
        scheduler_data = scheduler.system_snapshot()
    except Exception as exc:
        logger.debug("task-manager: scheduler snapshot unavailable: %s", exc)

    threads_data = collect_app_threads()
    data = {
        "generated_at": datetime.utcnow().isoformat(),
        "own_pid": os.getpid(),
        "processes": collect_processes(),
        "app_threads": threads_data["threads"],
        "app_rss_mb": threads_data["rss_mb"],
        "scheduler": scheduler_data,
        "subsystems": collect_subsystems(),
    }
    with _snapshot_lock:
        _snapshot_cache = data
        _snapshot_at = time.monotonic()
    return data
