"""Superuser controls for the one-shot lobby Discord advertisement."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser
from routes.admin import get_current_admin, log_admin_action
from services_lobby_advertisement_runtime import (
    lobby_advertisements_snapshot,
    save_lobby_ad_config,
    send_lobby_advertisement_now,
)
from services_server_restart import PterodactylError, normalize_server


router = APIRouter()


def _require_superuser(admin: AdminUser) -> None:
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")


def _one_snapshot(db: Session, server: str) -> dict:
    normalized = normalize_server(server)
    for item in lobby_advertisements_snapshot(db):
        if item["server"] == normalized:
            return item
    raise HTTPException(status_code=500, detail="Настройка рекламы не найдена после сохранения")


@router.get("/api/v1/admin/lobby-advertisements")
async def api_lobby_advertisement_list(
    current_admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _require_superuser(current_admin)
    return {"advertisements": lobby_advertisements_snapshot(db)}


@router.patch("/api/v1/admin/lobby-advertisements/{server}")
async def api_lobby_advertisement_update(
    server: str,
    request: Request,
    current_admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _require_superuser(current_admin)
    try:
        body = await request.json()
        row = save_lobby_ad_config(db, server, body, admin_id=current_admin.id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_admin_action(db, current_admin.id, "lobby_advertisement_config_update", {
        "server": row.server_name,
        "enabled": bool(row.enabled),
        "delay_seconds": int(row.delay_seconds or 0),
        "early_trigger_enabled": bool(row.early_trigger_enabled),
    })
    return {"status": "ok", "advertisement": _one_snapshot(db, row.server_name)}


@router.post("/api/v1/admin/lobby-advertisements/{server}/test")
async def api_lobby_advertisement_test(
    server: str,
    current_admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _require_superuser(current_admin)
    try:
        result = send_lobby_advertisement_now(db, server)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PterodactylError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_admin_action(db, current_admin.id, "lobby_advertisement_manual_test", {
        "server": result["server"],
        "discord_source": result["discord_source"],
    })
    return {"status": "ok", **result}
