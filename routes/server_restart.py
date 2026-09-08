"""Superuser controls for the durable Barotrauma server restart schedules."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser
from routes.admin import get_current_admin, log_admin_action
from services_server_restart import (
    PterodactylError,
    manual_restart_async,
    normalize_server,
    postpone_schedule,
    save_schedule,
    schedules_snapshot,
)


router = APIRouter()


def _require_superuser(admin: AdminUser) -> None:
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")


def _schedule_payload(body: dict) -> dict:
    conditions = body.get("conditions") if isinstance(body.get("conditions"), dict) else {}
    return {
        "enabled": body.get("enabled"),
        "daily_time": body.get("daily_time", body.get("time")),
        "msg_box": body.get("msg_box", body.get("message")),
        "no_online": conditions.get("no_online", body.get("no_online")),
        "round_finished": conditions.get("round_finished", body.get("round_finished")),
    }


def _one_snapshot(db: Session, server: str) -> dict:
    normalized = normalize_server(server)
    for item in schedules_snapshot(db):
        if item["server"] == normalized:
            return item
    raise HTTPException(status_code=500, detail="Расписание не найдено после сохранения")


@router.get("/api/v1/admin/server-restarts")
async def api_server_restart_list(
    current_admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _require_superuser(current_admin)
    return {"schedules": schedules_snapshot(db)}


@router.patch("/api/v1/admin/server-restarts/{server}")
async def api_server_restart_update(
    server: str,
    request: Request,
    current_admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _require_superuser(current_admin)
    try:
        body = await request.json()
        row = save_schedule(
            db,
            server,
            _schedule_payload(body),
            admin_id=current_admin.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_admin_action(db, current_admin.id, "server_restart_config_update", {
        "server": row.server_name,
        "enabled": bool(row.enabled),
        "daily_time": row.daily_time,
        "no_online": bool(row.require_no_online),
        "round_finished": bool(row.require_round_finished),
    })
    return {"status": "ok", "schedule": _one_snapshot(db, row.server_name)}


@router.post("/api/v1/admin/server-restarts/{server}/postpone")
async def api_server_restart_postpone(
    server: str,
    current_admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _require_superuser(current_admin)
    try:
        row = postpone_schedule(db, server, admin_id=current_admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_admin_action(db, current_admin.id, "server_restart_postpone", {"server": row.server_name})
    return {"status": "ok", "schedule": _one_snapshot(db, row.server_name)}


@router.post("/api/v1/admin/server-restarts/{server}/restart-now")
async def api_server_restart_now(
    server: str,
    current_admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _require_superuser(current_admin)
    try:
        result = await manual_restart_async(db, server, admin_id=current_admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PterodactylError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    normalized = normalize_server(server)
    log_admin_action(db, current_admin.id, "server_restart_manual", {
        "server": normalized,
        "warning_error": result.get("warning"),
    })
    return {
        "status": "ok",
        "warning": result.get("warning"),
        "schedule": _one_snapshot(db, normalized),
    }
