"""Live adaptive monster-wave telemetry for the administrator dashboard."""

from datetime import datetime
import json
import logging
from pathlib import Path
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
_BUNDLED_CATALOG_PATH = Path(__file__).resolve().parent.parent / "static" / "creature_catalog.json"
_DEPLOYED_CATALOG_PATH = Path("/home/container/static/creature_catalog.json")
_CWD_CATALOG_PATH = Path.cwd() / "static" / "creature_catalog.json"
_catalog_cache: dict[str, Any] = {"mtime": None, "creatures": []}

logger = logging.getLogger(__name__)


def _read_catalog_file(catalog_path: Path) -> list[dict[str, Any]] | None:
    try:
        with open(catalog_path, encoding="utf-8") as source:
            creatures = json.load(source).get("creatures", [])
    except (OSError, ValueError, TypeError):
        return None
    return creatures if creatures else None


def _creature_catalog() -> list[dict[str, Any]]:
    """Read the generated JSON catalog (see scripts/build_creature_catalog.py).

    The result is cached by file mtime so a missing file at startup does not
    stick as an empty catalog forever: the next request retries the disk.
    """
    for catalog_path in (_DEPLOYED_CATALOG_PATH, _BUNDLED_CATALOG_PATH, _CWD_CATALOG_PATH):
        try:
            mtime = catalog_path.stat().st_mtime
        except OSError:
            continue
        if _catalog_cache["mtime"] == mtime and _catalog_cache["creatures"]:
            return _catalog_cache["creatures"]
        creatures = _read_catalog_file(catalog_path)
        if creatures:
            _catalog_cache["mtime"] = mtime
            _catalog_cache["creatures"] = creatures
            return creatures
    if not _catalog_cache["creatures"]:
        logger.warning("creature catalog not found in deployment; checked %s, %s and %s",
                       _DEPLOYED_CATALOG_PATH, _BUNDLED_CATALOG_PATH, _CWD_CATALOG_PATH)
    return _catalog_cache["creatures"]


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
        "learned_baseline": body.get("learned_baseline", 0),
        "baseline_samples": body.get("baseline_samples", 0),
        "explanation": body.get("explanation", ""),
        "waves": body.get("waves", []),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    return {"status": "ok"}


@router.get("/health-floor")
def get_health_floor(server_name: str = "UNKNOWN") -> dict[str, float]:
    return _health_floors.get(server_name.strip().upper(), {"MinMonsterHealthMultiplier": 0.0, "MinMonsterHealthFixed": 0.0})


@admin_router.get("/state")
def get_state(request: Request, server_name: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_view(request, db)
    server = server_name.strip().upper() if server_name else None
    snapshots = list(_snapshots.values())
    return {"servers": [snapshot for snapshot in snapshots if not server or snapshot["server_name"] == server]}


@admin_router.get("/creatures")
def get_creatures(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_view(request, db)
    creatures = _creature_catalog()
    if not creatures:
        raise HTTPException(status_code=503, detail="Каталог JSON недоступен")
    return {"creatures": creatures}


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
