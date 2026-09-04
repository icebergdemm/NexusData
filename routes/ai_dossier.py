"""Moderator-facing AI player dossiers."""

from datetime import datetime
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import get_db
from models import AIPlayerDossier, AdminUser, AppSetting, PlayerData
from routes.admin import get_current_admin, log_admin_action
from security import check_permission
from services_ai_dossier import (
    dossier_response_with_run,
    enqueue_dossier,
    get_dossier_overview,
    get_dossier_config,
    list_dossier_runs,
    process_one_dossier,
    run_ai_health_check,
    search_dossier_players,
    select_active_player_ids,
    validate_dossier_ca_pem,
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
    return dossier_response_with_run(db, report, include_raw=current_admin.is_superuser)


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
    report, queued = enqueue_dossier(db, player_id, current_admin.id, force=force, source="manual")
    if queued:
        log_admin_action(db, current_admin.id, "queue_ai_player_dossier", {"target": {"player_id": player_id}, "force": force})
        _wake_worker()
    return {"queued": queued, "dossier": dossier_response_with_run(db, report, include_raw=current_admin.is_superuser)}


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
        _, created = enqueue_dossier(db, player_id, current_admin.id, force=True, source="test")
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
    return get_dossier_config(db, include_prompt=True)


@router.get("/api/v1/admin/ai-dossiers/overview")
async def get_ai_dossier_overview(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_player_view(current_admin)
    include_secret = current_admin.is_superuser or check_permission(current_admin, "players_special", "view")
    return get_dossier_overview(db, include_secret=include_secret)


@router.get("/api/v1/admin/ai-dossiers/runs")
async def get_ai_dossier_runs(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_player_view(current_admin)
    include_secret = current_admin.is_superuser or check_permission(current_admin, "players_special", "view")
    return {"runs": list_dossier_runs(db, limit, include_secret)}


@router.get("/api/v1/admin/ai-dossiers/players")
async def get_ai_dossier_players(
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_player_view(current_admin)
    include_secret = current_admin.is_superuser or check_permission(current_admin, "players_special", "view")
    return {"players": search_dossier_players(db, q, limit, include_secret)}


@router.post("/api/v1/admin/ai-dossiers/health-check")
async def ai_dossier_health_check(current_admin: AdminUser = Depends(get_current_admin)):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    return await run_in_threadpool(run_ai_health_check)


@router.patch("/api/v1/admin/ai-dossiers/config")
async def set_ai_dossier_config(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    body = await request.json()
    allowed = {"enabled", "daily_enabled", "test_mode", "daily_limit", "schedule_hour", "active_days", "max_log_events", "prompt"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings: {', '.join(sorted(unknown))}")
    for key, value in body.items():
        if key == "prompt":
            if not isinstance(value, str) or len(value) > 20000:
                raise HTTPException(status_code=422, detail="prompt must be text no longer than 20000 characters")
        elif key in {"daily_limit", "schedule_hour", "active_days", "max_log_events"}:
            try:
                bounds = {
                    "daily_limit": (1, 10),
                    "schedule_hour": (0, 23),
                    "active_days": (1, 365),
                    "max_log_events": (1, 500),
                }
                low, high = bounds[key]
                value = max(low, min(high, int(value)))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"{key} has an invalid value")
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
    return get_dossier_config(db, include_prompt=True)


@router.post("/api/v1/admin/ai-dossiers/connection-ca")
async def upload_ai_dossier_ca(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Store the public CA certificate used to verify the Qwen TLS endpoint."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    content = await file.read(64 * 1024 + 1)
    try:
        content = validate_dossier_ca_pem(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    target_dir = Path("config") / "ai_dossier"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "koboldcpp-ca.pem"
    fd, temp_name = tempfile.mkstemp(prefix=".koboldcpp-ca.", suffix=".pem", dir=target_dir)
    try:
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fchmod(temp_file.fileno(), 0o644)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    log_admin_action(db, current_admin.id, "ai_dossier_ca_upload", {"path": str(target)})
    return {"status": "ok", "path": str(target)}
