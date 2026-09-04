"""Moderator-facing AI player dossiers."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import AIPlayerDossier, AdminUser, AppSetting, PlayerData
from routes.admin import get_current_admin, log_admin_action
from security import check_permission
from services_ai_dossier import (
    dossier_public_dict,
    enqueue_dossier,
    get_dossier_config,
    process_one_dossier,
    select_active_player_ids,
)

router = APIRouter()


def _require_player_view(admin: AdminUser) -> None:
    if not (check_permission(admin, "players", "view") or check_permission(admin, "players_special", "view")):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _require_player_access(db: Session, player: PlayerData, admin: AdminUser) -> None:
    _require_player_view(admin)
    linked_superuser = db.query(AdminUser).filter(
        AdminUser.discord_id == player.discord_id,
        AdminUser.is_superuser.is_(True),
    ).first() if player.discord_id else None
    if (player.is_secret or linked_superuser) and not (
        admin.is_superuser or check_permission(admin, "players_special", "view")
    ):
        raise HTTPException(status_code=403, detail="Профиль засекречен")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "yes"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "no"}:
        return False
    raise HTTPException(status_code=422, detail="Boolean setting must be true or false")


def _wake_worker() -> None:
    try:
        from task_scheduler import Priority, scheduler
        scheduler.submit(process_one_dossier, priority=Priority.NORMAL, task_id="ai_dossier_worker", timeout=None)
    except Exception:
        # Startup may not have the scheduler in a maintenance/test process.
        # The durable row remains queued and will be picked up on the next run.
        return


@router.get("/api/v1/admin/players/{player_id}/ai-dossier")
async def get_player_ai_dossier(
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    _require_player_access(db, player, current_admin)
    report = db.query(AIPlayerDossier).filter(AIPlayerDossier.player_id == player_id).first()
    return dossier_public_dict(report)


@router.post("/api/v1/admin/players/{player_id}/ai-dossier", status_code=202)
async def request_player_ai_dossier(
    player_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    _require_player_access(db, player, current_admin)
    body = await request.json() if await request.body() else {}
    force = _as_bool(body["force"]) if "force" in body else False
    report, queued = enqueue_dossier(db, player_id, current_admin.id, force=force)
    if queued:
        log_admin_action(db, current_admin.id, "queue_ai_player_dossier", {"target": {"player_id": player_id}, "force": force})
        _wake_worker()
    return {"queued": queued, "dossier": dossier_public_dict(report)}


@router.post("/api/v1/admin/ai-dossiers/test-run", status_code=202)
async def test_run_ai_dossiers(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    ids = select_active_player_ids(db, datetime.utcnow(), 10)
    queued = 0
    for player_id in ids:
        _, created = enqueue_dossier(db, player_id, current_admin.id, force=True)
        queued += int(created)
    log_admin_action(db, current_admin.id, "queue_ai_player_dossier_test_run", {"player_ids": ids})
    if queued:
        _wake_worker()
    return {"selected": len(ids), "queued": queued, "player_ids": ids}


@router.get("/api/v1/admin/ai-dossiers/config")
async def get_ai_dossier_config(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    return get_dossier_config(db)


@router.patch("/api/v1/admin/ai-dossiers/config")
async def set_ai_dossier_config(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    body = await request.json()
    allowed = {"enabled", "daily_enabled", "test_mode", "daily_limit"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings: {', '.join(sorted(unknown))}")
    for key, value in body.items():
        if key == "daily_limit":
            try:
                value = max(1, min(10, int(value)))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="daily_limit must be 1..10")
        else:
            value = _as_bool(value)
        setting_key = f"ai_dossier_{key}"
        row = db.query(AppSetting).filter(AppSetting.key == setting_key).first()
        if row:
            row.value = str(value).lower() if isinstance(value, bool) else str(value)
        else:
            db.add(AppSetting(key=setting_key, value=str(value).lower() if isinstance(value, bool) else str(value)))
    db.commit()
    log_admin_action(db, current_admin.id, "ai_dossier_config_update", body)
    return get_dossier_config(db)
