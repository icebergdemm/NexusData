"""Small, dependency-light health response helpers.

Keeping the status calculation outside ``main.py`` makes the contract easy to
test without importing the whole application (which also imports Linux-only
file-locking support used by some routes).
"""

from typing import Any

from starlette.responses import JSONResponse


def healthz_status(state: Any) -> str:
    if state.maintenance_mode:
        return "maintenance"
    if not state.db_healthy or not state.api_enabled or not state.web_enabled:
        return "degraded"
    return "ok"


def build_healthz_response(state: Any) -> JSONResponse:
    status = healthz_status(state)
    response = JSONResponse(
        {
            "status": status,
            "db_healthy": state.db_healthy,
            "api_enabled": state.api_enabled,
            "web_enabled": state.web_enabled,
            "workers_enabled": state.workers_enabled,
            "maintenance_mode": state.maintenance_mode,
            "maintenance_message": state.maintenance_message or None,
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


def build_readiness_response(state: Any) -> JSONResponse:
    """Readiness has the same diagnostic body as healthz, with probe semantics."""
    return build_healthz_response(state)
