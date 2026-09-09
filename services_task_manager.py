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


def _cpu_pct_for(key: str, total_cpu_seconds: float, now: float, per_core: bool = False) -> tuple[float, float]:
    """Returns (cpu_pct, cached_before).

    per_core=True (thread rows): normalize to ONE core — a busy thread then
    reads ~100 %, like top / Task Manager Details. per_core=False (process
    rows): normalize to all cores — the system-wide Task Manager convention.
    Pct is a delta over the time since the previous call with the same key."""
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
    cores = 1 if per_core else (psutil.cpu_count() or 1)
    return delta / dt / cores * 100.0, total_cpu_seconds


def _prune_cpu_cache(prefix: str, alive_keys: set) -> None:
    """Drop history only within ONE namespace ("pid:"/"tid:").

    The two collectors run in the same build; pruning globally would wipe
    the other dataset's history — deltas need two consecutive samples for
    every key, so a global prune made every CPU% read 0.0 forever."""
    with _cpu_cache_lock:
        for key in list(_cid_cpu_cache.keys()):
            if key.startswith(prefix) and key not in alive_keys:
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


def _build_process_row(proc, now: float) -> dict | None:
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
        return None
    except Exception:
        return None
    cpu_pct, total_cpu = _cpu_pct_for(f"pid:{pid}", cpu_times.user + cpu_times.system, now)
    return {
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
    }


def collect_processes() -> list[dict]:
    """Every visible process, newest CPU first. Own process and all its
    descendants are flagged so the UI can highlight the app lineage.

    Own process + children are collected EXPLICITLY first: the generic
    iteration may be capped (_MAX_PROCESSES yields arbitrary host order)
    and then the app itself silently vanishes from the page."""
    try:
        me = psutil.Process()
    except psutil.Error:
        me = None
    own_pid = me.pid if me else os.getpid()
    now = time.monotonic()
    entries: list[dict] = []

    own_row = _build_process_row(me, now) if me is not None else None
    child_rows: list[dict] = []
    try:
        child_procs = me.children(recursive=True) if me is not None else []
    except Exception:
        child_procs = []
    for child in child_procs:
        row = _build_process_row(child, now)
        if row is not None:
            row["own_child"] = True
            child_rows.append(row)
    if own_row is not None:
        own_row["is_own"] = True
        child_pids = {row["pid"] for row in child_rows}
        own_row["own_child"] = own_row["pid"] in child_pids
        child_rows.append(own_row)  # keep all of ours together
    entries.extend(child_rows)

    seen = {row["pid"] for row in entries}
    for proc in psutil.process_iter(None):
        if len(entries) >= _MAX_PROCESSES:
            break
        try:
            if proc.pid in seen:
                continue
            seen.add(proc.pid)
        except Exception:
            continue
        row = _build_process_row(proc, now)
        if row is not None:
            entries.append(row)

    _prune_cpu_cache("pid:", {f"pid:{row['pid']}" for row in entries})
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
    """What a thread is executing right now (top frames).

    sys._current_frames() is keyed by the PYTHON thread ident, while
    psutil.threads() (and /proc) use the OS thread id — those are different
    numbering spaces. This is exactly why the "Выполняет сейчас" column was
    always empty: the dict lookup never matched. Map native_id → ident via
    threading.enumerate() first."""
    try:
        try:
            ident_map = {t.native_id: t.ident for t in
                         (th for th in threading.enumerate() if getattr(th, "native_id", None) is not None)}
        except Exception:
            ident_map = {}
        ident = ident_map.get(tid)
        if ident is None:
            return ""
        frame = sys._current_frames().get(ident)
    except Exception:
        return ""
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
        # psutil's thread namedtuple exposes user_time/system_time (NOT the
        # user/system fields of the process-level cpu_times).
        cpu_pct, total_cpu = _cpu_pct_for(
            key, getattr(stat, "user_time", 0.0) + getattr(stat, "system_time", 0.0), now,
            per_core=True,
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
    _prune_cpu_cache("tid:", {f"tid:{row['tid']}" for row in rows})
    rows.sort(key=lambda row: (-row["cpu_pct"], -row["cpu_total_s"]))
    return {
        "threads": rows[:_MAX_THREADS],
        "rss_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
    }


def collect_subsystems() -> list[dict]:
    """Long-lived background subsystem loops and whether they run now.

    ``control`` is the actionable target id for execute_control(); null for
    services without a start/stop API (they live as short-lived writers)."""
    rows = []
    try:
        import task_scheduler
        rows.append({
            "name": "TaskScheduler",
            "running": task_scheduler.scheduler._started,
            "detail": f"{task_scheduler.scheduler._worker_count} воркера фоновых задач",
            "source": "task_scheduler.py",
            "control": "scheduler",
        })
    except Exception:
        rows.append({"name": "TaskScheduler", "running": False, "detail": "не загружен", "source": "task_scheduler.py", "control": None})
    try:
        import services_logs
        rows.append({
            "name": "LogWorker (SFTP→SQLite)",
            "running": bool(getattr(services_logs, "_log_worker_running", False)),
            "detail": f"интервал {getattr(services_logs, '_log_refresh_interval', '?')} с",
            "source": "services_logs.py",
            "control": "log_worker",
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
            "control": "replay_worker",
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
            "control": "playtime_worker",
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
            "control": None,
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
            "control": None,
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


# ══════════════════════════════════════════════════════════════════════════════
# Control: process / subsystem actions from the admin UI (superuser only)
# ══════════════════════════════════════════════════════════════════════════════

class ControlError(RuntimeError):
    """Raised with the HTTP detail text for a rejected control action."""


def _own_lineage_pids() -> set[int]:
    pids = {os.getpid()}
    try:
        for child in psutil.Process().children(recursive=True):
            pids.add(child.pid)
    except Exception:
        pass
    return pids


def _process_control(pid: int, action: str) -> dict:
    if pid not in _own_lineage_pids():
        raise ControlError(
            "Управлять можно только процессами приложения (API и его дочерние)."
        )
    is_self = pid == os.getpid()
    try:
        proc = psutil.Process(pid)
        if action == "suspend":
            proc.suspend()
        elif action == "resume":
            proc.resume()
        elif action in ("kill", "terminate", "restart"):
            # terminate() relies on the supervisor (proxy_server's watcher
            # for api_server.py, api_server's watcher for scheduler_process.py)
            # to respawn the same role via runtime_control flags.
            proc.terminate()
        else:
            raise ControlError(f"Неизвестное действие процесса: {action}")
        if action in ("kill", "terminate", "restart"):
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                if action == "kill":
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        pass
    except psutil.Error as exc:
        raise ControlError(f"Процесс {pid} недоступен: {type(exc).__name__}") from exc
    return {
        "pid": pid,
        "action": action,
        "is_self": is_self,
        # A suspended API process stops answering HTTP: only a console
        # restart (update restart) can bring it back.
        "warning": (
            "Приостановлен сам процесс API — сайт не отвечает, вернуться можно только перезапуском (update restart)."
            if is_self and action == "suspend" else None
        ),
    }


def _subsystem_control(subsystem: str, action: str) -> dict:
    """stop persists until manual start or process restart (flags only live
    in-process) — exactly the "выключить до обратного включения" semantics."""
    if subsystem == "scheduler":
        from task_scheduler import scheduler
        if action == "stop":
            scheduler.stop()
        elif action == "start":
            scheduler.start()
        else:
            raise ControlError(f"Неизвестное действие: {action}")
        return {"subsystem": subsystem, "running": scheduler._started}
    if subsystem == "log_worker":
        import services_logs
        if action == "stop":
            services_logs.stop_log_worker()
        elif action == "start":
            services_logs.start_log_worker()
        else:
            raise ControlError(f"Неизвестное действие: {action}")
        return {"subsystem": subsystem, "running": services_logs._log_worker_running}
    if subsystem == "replay_worker":
        import services_replays
        if action == "stop":
            services_replays.stop_replay_worker()
        elif action == "start":
            services_replays.start_replay_worker()
        else:
            raise ControlError(f"Неизвестное действие: {action}")
        return {"subsystem": subsystem, "running": services_replays._replay_worker_running}
    if subsystem == "playtime_worker":
        import services_playtime_stats
        if action == "stop":
            services_playtime_stats.stop_playtime_stats_worker()
        elif action == "start":
            services_playtime_stats.start_playtime_stats_worker()
        else:
            raise ControlError(f"Неизвестное действие: {action}")
        return {"subsystem": subsystem, "running": services_playtime_stats._playtime_stats_worker_running}
    raise ControlError(f"Управляемая служба не найдена: {subsystem}")


def execute_control(target: str, action: str) -> dict:
    """Entry point for the admin route (see routes/perf_monitor.py)."""
    if action not in ("suspend", "resume", "kill", "terminate", "restart", "stop", "start"):
        raise ControlError(f"Неизвестное действие: {action}")
    if target.startswith("pid:"):
        return _process_control(int(target[4:]), action)
    return _subsystem_control(target, action)


def invalidate_snapshot_cache() -> None:
    """Force the next task-manager build to refresh (after a control action)."""
    global _snapshot_cache, _snapshot_at
    with _snapshot_lock:
        _snapshot_cache = None
        _snapshot_at = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Profilers (on demand, superuser only, never writing anything to disk)
# ══════════════════════════════════════════════════════════════════════════════

_cpu_capture_lock = threading.Lock()
_cpu_capture_running = False

_memprofile_lock = threading.Lock()
_memprofile_prev_snapshot = None


def capture_cpu_profile(seconds: float = 6.0, interval: float = 0.02) -> dict:
    """Sample every running Python thread's stack for N seconds and aggregate
    per top-level frame — py-spy-lite: proportional sample counts approximate
    where CPU time goes. Bounded: 1-15 s, no disk writes, single run at a time."""
    global _cpu_capture_running
    seconds = max(1.0, min(float(seconds), 15.0))
    interval = max(0.01, min(float(interval), 0.05))
    with _cpu_capture_lock:
        if _cpu_capture_running:
            raise ControlError("Срез уже снимается — подождите завершения.")
        _cpu_capture_running = True
    own_ident = threading.get_ident()
    counts: dict[tuple[str, str], int] = {}
    total = 0
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                frames = sys._current_frames()
            except Exception:
                break
            for ident, frame in frames.items():
                if ident == own_ident:
                    continue
                observed = frame
                if observed is not None and "services_task_manager" in observed.f_code.co_filename:
                    # skip the sampler's own leaf frames
                    observed = observed.f_back
                while observed is not None and "services_task_manager" in observed.f_code.co_filename:
                    observed = observed.f_back
                if observed is None:
                    continue
                code = observed.f_code
                key = (code.co_name, os.path.basename(code.co_filename))
                counts[key] = counts.get(key, 0) + 1
                total += 1
            time.sleep(interval)
    finally:
        with _cpu_capture_lock:
            _cpu_capture_running = False
    top = [
        {"function": name, "file": fname, "samples": n, "pct": round(n / total * 100.0, 1)}
        for (name, fname), n in sorted(counts.items(), key=lambda kv: -kv[1])[:40]
    ] if total else []
    return {"seconds": seconds, "samples_total": total, "top": top}


def mem_profile(action: str) -> dict:
    """Tracemalloc control: start / snapshot / stop.

    snapshot returns the CURRENT top allocation sites by (file, line) plus
    the growth versus the previous snapshot — the closest answer to
    "какая функция сколько съела RAM"."""
    global _memprofile_prev_snapshot
    with _memprofile_lock:
        import tracemalloc
        if action == "start":
            if not tracemalloc.is_tracing():
                tracemalloc.start(25)
            else:
                tracemalloc.clear_traces()
            _memprofile_prev_snapshot = tracemalloc.take_snapshot()
            return {"status": "tracing", "hint": "Дальше снимки показывают и текущее, и прирост с момента предыдущего снимка."}
        if action == "stop":
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            _memprofile_prev_snapshot = None
            return {"status": "stopped"}
        if action == "snapshot":
            if not tracemalloc.is_tracing():
                raise ControlError("Профайлер памяти не запущен — сначала включите его.")
            now_snap = tracemalloc.take_snapshot()
            top = []
            for stat in now_snap.statistics("lineno")[:40]:
                frame = stat.traceback[0]
                top.append({
                    "location": f"{os.path.basename(frame.filename)}:{frame.lineno}",
                    "size_mb": round(stat.size / 1024 / 1024, 2),
                    "count": stat.count,
                })
            growth = []
            if _memprofile_prev_snapshot is not None:
                try:
                    diffs = now_snap.compare_to(_memprofile_prev_snapshot, "lineno")
                except Exception:
                    diffs = []
                for diff in diffs[:40]:
                    frame = diff.traceback[0]
                    growth.append({
                        "location": f"{os.path.basename(frame.filename)}:{frame.lineno}",
                        "delta_mb": round(diff.size_diff / 1024 / 1024, 2),
                    })
            _memprofile_prev_snapshot = now_snap
            return {"status": "ok", "top": top, "growth": growth, "tracing": True}
        raise ControlError(f"Неизвестное действие профайлера памяти: {action}")

