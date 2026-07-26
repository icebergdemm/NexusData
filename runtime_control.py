"""
Shared runtime control state — the single source of truth for "is API/web/
background-workers serving normally, or in maintenance/disabled mode".

File-backed (not in-memory) on purpose: in multi-worker mode (UVICORN_WORKERS>1,
see api_server.py) the web tier is N separate uvicorn worker processes and the
background scheduler runs in its own scheduler_process.py — none of them share
memory, so a plain module-level flag would only ever affect one of them. A
small JSON file every process reads (with a short in-process cache to avoid
re-reading it on every single request) is the simplest thing that works
identically whether the app is running as one process or several.

Tolerant by design: if the file is missing, unreadable, or corrupt,
read_state() returns safe all-enabled defaults rather than raising — this
mechanism must never itself become a new way to take the site down.
"""

import json
import os
import threading
import time
from dataclasses import dataclass, asdict, replace
from datetime import datetime
from typing import Optional

_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_STATE_FILE = os.path.join(_STATE_DIR, "runtime_state.json")

_CACHE_TTL_SECONDS = 1.0
_lock = threading.Lock()
_cache: Optional["RuntimeState"] = None
_cache_read_at = 0.0


@dataclass
class RuntimeState:
    api_enabled: bool = True
    web_enabled: bool = True
    workers_enabled: bool = True
    # Controls the actual backend (api_server.py) OS process, managed by
    # proxy_server.py — distinct from api_enabled/web_enabled, which only
    # gate request handling INSIDE that same process. Flipping this to False
    # stops the whole backend process (e.g. to safely replace code during a
    # deploy); the proxy keeps serving the maintenance/unavailable page the
    # entire time since it's a separate process that never goes down.
    backend_enabled: bool = True
    maintenance_mode: bool = False
    maintenance_message: str = ""
    db_healthy: bool = True
    updated_at: str = ""


def _default_state() -> RuntimeState:
    return RuntimeState(updated_at=datetime.utcnow().isoformat())


def _read_from_disk() -> RuntimeState:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        defaults = asdict(_default_state())
        # Ignore unknown keys, fill missing ones with defaults — forward/backward
        # compatible with older/newer state files without ever raising.
        merged = {k: raw.get(k, v) for k, v in defaults.items()}
        return RuntimeState(**merged)
    except Exception:
        return _default_state()


def read_state(force: bool = False) -> RuntimeState:
    """Cached read (≤1s stale) — cheap enough to call on every request."""
    global _cache, _cache_read_at
    now = time.monotonic()
    if not force and _cache is not None and (now - _cache_read_at) < _CACHE_TTL_SECONDS:
        return _cache
    with _lock:
        now = time.monotonic()
        if not force and _cache is not None and (now - _cache_read_at) < _CACHE_TTL_SECONDS:
            return _cache
        state = _read_from_disk()
        _cache = state
        _cache_read_at = now
        return state


def write_state(**updates) -> RuntimeState:
    """Read-modify-write, atomic replace so a mid-write crash can never leave
    a half-written (unparseable) state file behind."""
    global _cache, _cache_read_at
    with _lock:
        current = _read_from_disk()
        new_state = replace(current, **updates, updated_at=datetime.utcnow().isoformat())
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp_path = _STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(asdict(new_state), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _STATE_FILE)
        _cache = new_state
        _cache_read_at = time.monotonic()
        return new_state


def set_db_healthy(ok: bool) -> None:
    current = read_state()
    if current.db_healthy != ok:
        write_state(db_healthy=ok)
