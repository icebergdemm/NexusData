"""Small, dependency-light health response helpers.

Keeping the status calculation outside ``main.py`` makes the contract easy to
test without importing the whole application (which also imports Linux-only
file-locking support used by some routes).
"""

from typing import Any

from starlette.responses import JSONResponse


def healthz_status(state: Any, lockdown: Any = None, lockdown_bypassed: bool = False) -> str:
    lockdown_enabled = bool(getattr(lockdown, "enabled", False) if lockdown is not None else getattr(state, "web_lockdown", False))
    if state.maintenance_mode or (lockdown_enabled and not lockdown_bypassed):
        return "maintenance"
    if not state.db_healthy or not state.api_enabled or not state.web_enabled:
        return "degraded"
    return "ok"


def build_healthz_response(state: Any, lockdown: Any = None, lockdown_bypassed: bool = False) -> JSONResponse:
    status = healthz_status(state, lockdown, lockdown_bypassed)
    lockdown_enabled = bool(getattr(lockdown, "enabled", False) if lockdown is not None else getattr(state, "web_lockdown", False))
    lockdown_message = getattr(lockdown, "message", "") if lockdown is not None else getattr(state, "web_lockdown_message", "")
    maintenance_message = lockdown_message if lockdown_enabled and not lockdown_bypassed else state.maintenance_message
    response = JSONResponse(
        {
            "status": status,
            "db_healthy": state.db_healthy,
            "api_enabled": state.api_enabled,
            "web_enabled": state.web_enabled,
            "workers_enabled": state.workers_enabled,
            "maintenance_mode": bool(state.maintenance_mode or (lockdown_enabled and not lockdown_bypassed)),
            "maintenance_message": maintenance_message or None,
            "web_lockdown": lockdown_enabled,
            "lockdown_bypassed": bool(lockdown_bypassed and lockdown_enabled),
        },
        status_code=200 if status == "ok" else 503,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def build_liveness_response() -> JSONResponse:
    """Process-only probe. It must stay useful while the database is broken."""
    response = JSONResponse({"status": "ok"}, status_code=200)
    response.headers["Cache-Control"] = "no-store"
    return response


def build_readiness_response(state: Any, lockdown: Any = None) -> JSONResponse:
    """Readiness has the same diagnostic body as healthz, with probe semantics."""
    return build_healthz_response(state, lockdown)
