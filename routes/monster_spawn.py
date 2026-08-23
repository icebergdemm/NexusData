"""Declarative control plane for the MonsterSpawnController game plugin."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from config import get_settings
from database import get_db, get_game_db
from models import AdminSession, AdminUser, MonsterSpawnControl
from security import check_permission, verify_external_api_key_dependency

settings = get_settings()

game_router = APIRouter(
    prefix="/api/v1/game/monster-spawn",
    tags=["monster-spawn-game"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)
admin_router = APIRouter(prefix="/api/v1/admin/monster-spawn", tags=["monster-spawn-admin"])


class PluginTelemetry(BaseModel):
    server: str
    applied_revision: int = 0
    config: Dict[str, Any] = Field(default_factory=dict)
    memory: Dict[str, Any] = Field(default_factory=dict)
    plans: List[Dict[str, Any]] = Field(default_factory=list)
    telemetry: Dict[str, Any] = Field(default_factory=dict)


class AdminUpdate(BaseModel):
    config: Optional[Dict[str, Any]] = None
    memory: Optional[Dict[str, Any]] = None
    plans: Optional[List[Dict[str, Any]]] = None


def _normalize_server(server: str) -> str:
    value = (server or "").strip().upper()
    if not value:
        raise HTTPException(status_code=400, detail="Не указан сервер")
    return value


def _get_or_create(db: Session, server: str) -> MonsterSpawnControl:
    row = db.query(MonsterSpawnControl).filter(MonsterSpawnControl.server == server).first()
    if row is None:
        row = MonsterSpawnControl(server=server)
        db.add(row)
        db.flush()
    return row


def _serialize(row: MonsterSpawnControl) -> dict:
    return {
        "server": row.server,
        "config": row.config or {},
        "memory": row.memory or {},
        "plans": row.plans or [],
        "telemetry": row.telemetry or {},
        "desired_revision": row.desired_revision or 0,
        "applied_revision": row.applied_revision or 0,
        "pending": (row.desired_revision or 0) != (row.applied_revision or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


def _current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token,
            AdminSession.revoked == 0,
        ).first()
        if session and (session.expires_at is None or session.expires_at > datetime.utcnow()):
            admin = db.query(AdminUser).options(joinedload(AdminUser.privilege_group)).filter(
                AdminUser.id == session.admin_id
            ).first()
            if admin:
                from security import compute_department_site_permissions

                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require(admin: AdminUser, action: str) -> None:
    if not check_permission(admin, "monster_spawn", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


@game_router.get("/state")
def plugin_state(server: str = Query(...), db: Session = Depends(get_game_db)):
    server = _normalize_server(server)
    row = _get_or_create(db, server)
    row.last_seen_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _serialize(row)


@game_router.post("/telemetry")
def plugin_telemetry(payload: PluginTelemetry, db: Session = Depends(get_game_db)):
    server = _normalize_server(payload.server)
    row = _get_or_create(db, server)
    reported_revision = max(0, payload.applied_revision)

    row.applied_revision = reported_revision
    row.telemetry = payload.telemetry
    row.last_seen_at = datetime.utcnow()

    # An old plugin snapshot must not overwrite edits that are still waiting
    # to be applied. Once the desired revision is acknowledged, plugin state
    # becomes authoritative again (including runtime plan status changes).
    if reported_revision >= (row.desired_revision or 0):
        row.config = payload.config
        row.memory = payload.memory
        row.plans = payload.plans

    db.commit()
    db.refresh(row)
    return {"status": "ok", "desired_revision": row.desired_revision}


@admin_router.get("")
def admin_state(
    server: str = Query(...),
    admin: AdminUser = Depends(_current_admin),
    db: Session = Depends(get_game_db),
):
    _require(admin, "view")
    row = _get_or_create(db, _normalize_server(server))
    db.commit()
    db.refresh(row)
    return _serialize(row)


@admin_router.put("")
def admin_update(
    payload: AdminUpdate,
    server: str = Query(...),
    admin: AdminUser = Depends(_current_admin),
    db: Session = Depends(get_game_db),
):
    _require(admin, "edit")
    if payload.memory is not None:
        _require(admin, "manage_memory")

    row = _get_or_create(db, _normalize_server(server))
    changed = False
    if payload.config is not None:
        row.config = payload.config
        changed = True
    if payload.memory is not None:
        row.memory = payload.memory
        changed = True
    if payload.plans is not None:
        row.plans = payload.plans
        changed = True
    if not changed:
        raise HTTPException(status_code=400, detail="Нет изменений")

    row.desired_revision = (row.desired_revision or 0) + 1
    row.updated_by_admin_id = admin.id
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _serialize(row)
