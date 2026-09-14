"""Live adaptive monster-wave telemetry for the administrator dashboard."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from security import check_permission, verify_external_api_key_dependency
from models import AdminUser

settings = get_settings()
router = APIRouter(prefix="/api/v1/spawn-director", tags=["spawn-director"], dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [])
admin_router = APIRouter(prefix="/api/v1/admin/spawn-director", tags=["spawn-director-admin"])
_snapshots: dict[str, dict[str, Any]] = {}
_health_floors: dict[str, dict[str, float]] = {}


def _require_view(request: Request, db: Session) -> AdminUser:
    from routes.admin import load_current_admin
    admin = load_current_admin(request, db)
    if not check_permission(admin, "world_state", "view"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для просмотра директора волн")
    return admin


@router.post("/snapshot")
async def ingest_snapshot(body: dict[str, Any]) -> dict[str, str]:
    server = str(body.get("server_name") or "UNKNOWN").strip().upper()
    _snapshots[server] = {
        "server_name": server,
        "mode": body.get("mode", "observe"),
        "crew_power": body.get("crew_power", 0),
        "threat_multiplier": body.get("threat_multiplier", 1),
        "waves": body.get("waves", []),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    return {"status": "ok"}


@router.get("/health-floor")
def get_health_floor(server_name: str = "UNKNOWN") -> dict[str, float]:
    return _health_floors.get(server_name.strip().upper(), {"MinMonsterHealthMultiplier": 0.0, "MinMonsterHealthFixed": 0.0})


@admin_router.get("/state")
def get_state(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_view(request, db)
    return {"servers": list(_snapshots.values())}


@admin_router.put("/health-floor/{server_name}")
def set_health_floor(server_name: str, body: dict[str, Any], request: Request, db: Session = Depends(get_db)) -> dict[str, float]:
    admin = _require_view(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Изменять порог здоровья может только superuser")
    multiplier = max(0.0, float(body.get("multiplier", 0.0)))
    fixed = max(0.0, float(body.get("fixed", 0.0)))
    value = {"MinMonsterHealthMultiplier": multiplier, "MinMonsterHealthFixed": fixed}
    _health_floors[server_name.strip().upper()] = value
    return value
