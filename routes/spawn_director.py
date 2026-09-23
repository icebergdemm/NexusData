"""Spawn-director plugin endpoints: telemetry snapshots, health floors,
config delivery, and admin management."""

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from config import get_settings
from database import get_db, get_game_db
from security import check_permission, verify_external_api_key_dependency
from models import AdminUser, SpawnDirectorConfig

settings = get_settings()
router = APIRouter(
    prefix="/api/v1/spawn-director",
    tags=["spawn-director"],
    **({"dependencies": [Depends(verify_external_api_key_dependency)]} if settings.require_auth else {}),
)
admin_router = APIRouter(prefix="/api/v1/admin/spawn-director", tags=["spawn-director-admin"])
_snapshots: dict[str, dict[str, Any]] = {}

_BUNDLED_CATALOG_PATH = Path(__file__).resolve().parent.parent / "static" / "creature_catalog.json"
_DEPLOYED_CATALOG_PATH = Path("/home/container/static/creature_catalog.json")
_CWD_CATALOG_PATH = Path.cwd() / "static" / "creature_catalog.json"
_catalog_cache: dict[str, Any] = {"mtime": None, "creatures": []}

logger = logging.getLogger(__name__)


def _default_config() -> dict[str, Any]:
    """Return the baseline spawn-director config merged with defaults."""
    return {
        "mode": "observe",
        "base_budget": 1.0,
        "alpha": 0.65,
        "min_multiplier": 0.55,
        "max_multiplier": 1.75,
        "hp_multiplier_min": 0.8,
        "hp_multiplier_max": 1.35,
        "min_monster_health_multiplier": 0.0,
        "min_monster_health_fixed": 0.0,
        "update_seconds": 6.0,
        "debug": False,
        "spawn_frequency": 1.0,
        "initial_grace_progress": 0.05,
        "initial_grace_seconds": 900,
        "large_crew_threshold": 15,
        "large_crew_grace_seconds": 300,
        "species_hp_overrides": {},
    }


def _resolve_config(db: Session, server: str) -> dict[str, Any]:
    """Load the config row for *server*, merge with defaults, return flat dict."""
    row = db.execute(
        select(SpawnDirectorConfig).where(SpawnDirectorConfig.server == server)
    ).scalar_one_or_none()
    merged = _default_config()
    if row and row.config:
        merged.update(row.config)
    return merged


def _revision_for(db: Session, server: str) -> int:
    """Return desired_revision for *server*, or 0 if no row exists."""
    row = db.execute(
        select(SpawnDirectorConfig).where(SpawnDirectorConfig.server == server)
    ).scalar_one_or_none()
    return row.desired_revision if row else 0


# ---------------------------------------------------------------------------
# Catalog helpers (unchanged)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Game-facing endpoints (router, uses get_game_db)
# ---------------------------------------------------------------------------


@router.post("/snapshot")
async def ingest_snapshot(body: dict[str, Any], db: Session = Depends(get_game_db)) -> dict[str, str]:
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

    # Accept applied_revision from the plugin and update last_seen_at
    applied = body.get("applied_revision")
    if applied is not None:
        row = db.execute(
            select(SpawnDirectorConfig).where(SpawnDirectorConfig.server == server)
        ).scalar_one_or_none()
        if row:
            row.applied_revision = int(applied)
            row.last_seen_at = datetime.utcnow()
            db.commit()

    return {"status": "ok"}


@router.get("/health-floor")
def get_health_floor(server_name: str = "UNKNOWN", db: Session = Depends(get_game_db)) -> dict[str, float]:
    server = server_name.strip().upper()
    cfg = _resolve_config(db, server)
    return {
        "MinMonsterHealthMultiplier": cfg.get("min_monster_health_multiplier", 0.0),
        "MinMonsterHealthFixed": cfg.get("min_monster_health_fixed", 0.0),
    }


@router.get("/config")
def get_game_config(server_name: str = "UNKNOWN", db: Session = Depends(get_game_db)) -> dict[str, Any]:
    """Return the flat config dict the plugin polls every ~3 s."""
    server = server_name.strip().upper()
    cfg = _resolve_config(db, server)
    cfg["revision"] = _revision_for(db, server)
    return cfg


# ---------------------------------------------------------------------------
# Admin endpoints (admin_router, uses get_db)
# ---------------------------------------------------------------------------


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


@admin_router.get("/config")
def get_admin_config(request: Request, server_name: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the full config for the admin UI, optionally filtered by server."""
    _require_view(request, db)
    server = server_name.strip().upper() if server_name else None

    if server:
        cfg = _resolve_config(db, server)
        row = db.execute(
            select(SpawnDirectorConfig).where(SpawnDirectorConfig.server == server)
        ).scalar_one_or_none()
        return {
            "server": server,
            "config": cfg,
            "desired_revision": row.desired_revision if row else 1,
            "applied_revision": row.applied_revision if row else 0,
            "updated_at": (row.updated_at.isoformat() + "Z") if row and row.updated_at else None,
            "last_seen_at": (row.last_seen_at.isoformat() + "Z") if row and row.last_seen_at else None,
        }

    # Return all servers
    rows = db.execute(select(SpawnDirectorConfig)).scalars().all()
    return {
        "servers": [
            {
                "server": r.server,
                "config": _default_config() | (r.config or {}),
                "desired_revision": r.desired_revision,
                "applied_revision": r.applied_revision,
                "updated_at": (r.updated_at.isoformat() + "Z") if r.updated_at else None,
                "last_seen_at": (r.last_seen_at.isoformat() + "Z") if r.last_seen_at else None,
            }
            for r in rows
        ]
    }


@admin_router.put("/config/{server_name}")
def set_admin_config(
    server_name: str,
    body: dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Update spawn-director config for a server. Bumps desired_revision."""
    admin = _require_view(request, db)
    if not check_permission(admin, "world_state", "edit") and not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Недостаточно прав для изменения конфигурации директора волн")

    server = server_name.strip().upper()
    row = db.execute(
        select(SpawnDirectorConfig).where(SpawnDirectorConfig.server == server)
    ).scalar_one_or_none()

    # Merge incoming config with existing (or defaults for new rows)
    base = _default_config()
    if row and row.config:
        base.update(row.config)

    incoming = body.get("config", body)  # accept flat dict or {"config": {...}}
    base.update(incoming)

    if row is None:
        row = SpawnDirectorConfig(
            server=server,
            config=base,
            desired_revision=1,
            applied_revision=0,
            updated_by_admin_id=admin.id,
            last_seen_at=None,
        )
        db.add(row)
    else:
        row.config = base
        row.desired_revision += 1
        row.updated_by_admin_id = admin.id

    db.commit()
    db.refresh(row)

    return {
        "server": row.server,
        "config": row.config,
        "desired_revision": row.desired_revision,
        "applied_revision": row.applied_revision,
        "updated_at": (row.updated_at.isoformat() + "Z") if row.updated_at else None,
    }


@admin_router.put("/health-floor/{server_name}")
def set_health_floor(server_name: str, body: dict[str, Any], request: Request, db: Session = Depends(get_db)) -> dict[str, float]:
    """Set health floor values in the spawn-director config JSON."""
    admin = _require_view(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Изменять порог здоровья может только superuser")

    server = server_name.strip().upper()
    multiplier = max(0.0, float(body.get("multiplier", 0.0)))
    fixed = max(0.0, float(body.get("fixed", 0.0)))

    row = db.execute(
        select(SpawnDirectorConfig).where(SpawnDirectorConfig.server == server)
    ).scalar_one_or_none()

    if row is None:
        row = SpawnDirectorConfig(
            server=server,
            config=_default_config(),
            desired_revision=1,
            applied_revision=0,
            updated_by_admin_id=admin.id,
        )
        db.add(row)

    if row.config is None:
        row.config = _default_config()
    row.config["min_monster_health_multiplier"] = multiplier
    row.config["min_monster_health_fixed"] = fixed
    row.desired_revision += 1
    row.updated_by_admin_id = admin.id
    db.commit()

    return {"MinMonsterHealthMultiplier": multiplier, "MinMonsterHealthFixed": fixed}
