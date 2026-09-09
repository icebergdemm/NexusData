"""Superuser controls for the durable replay voice index."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser, VoiceIndexRun
from services_voice_index import (
    VOICE_RUN_LOG_RETENTION_DAYS,
    _voice_run_log,
    get_active_voice_index_run,
    get_voice_index_status,
    list_voice_index_run_logs,
    queue_voice_index_backfill,
)

router = APIRouter()


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin

    return load_current_admin(request, db)


def _require_superuser(admin: AdminUser):
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Доступно только суперпользователям")


def _run_response(run: VoiceIndexRun | None):
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "total": run.total,
        "processed": run.processed,
        "ready_count": run.ready_count,
        "error_count": run.error_count,
        "last_replay_id": run.last_replay_id,
        "last_message": run.last_message,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
    }


@router.post("/api/v1/admin/voice-index/runs")
async def api_start_voice_index(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    if get_active_voice_index_run(db):
        raise HTTPException(status_code=409, detail="Индексация уже выполняется")

    run = VoiceIndexRun(requested_by_admin_id=current_admin.id, status="pending")
    db.add(run)
    db.commit()
    db.refresh(run)

    task_id = queue_voice_index_backfill(run.id)
    if not task_id:
        run.status = "failed"
        run.error = "Не удалось поставить индексацию в фоновую очередь"
        db.commit()
        raise HTTPException(status_code=503, detail=run.error)

    _voice_run_log(db, run, status="pending", message="Запуск индексации создан и поставлен в очередь")
    db.commit()
    from routes.admin import log_admin_action

    log_admin_action(db, current_admin.id, "voice_index_backfill_start", {"run_id": run.id})
    return _run_response(run)


@router.get("/api/v1/admin/voice-index/status")
async def api_voice_index_status(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    return get_voice_index_status(db)


@router.post("/api/v1/admin/voice-index/runs/{run_id}/cancel")
async def api_cancel_voice_index(
    run_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    run = db.query(VoiceIndexRun).filter(VoiceIndexRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Запуск индексации не найден")
    if run.status not in ("pending", "running", "cancelling"):
        raise HTTPException(status_code=409, detail="Индексация уже завершена")
    if run.status == "cancelling":
        return _run_response(run)

    now = datetime.utcnow()
    if run.status == "pending":
        run.status = "cancelled"
        run.finished_at = now
        run.heartbeat_at = now
        run.last_message = "Индексация остановлена до запуска"
        log_status = "cancelled"
    else:
        run.status = "cancelling"
        run.last_message = "Остановка запрошена; текущий реплей будет завершён"
        log_status = "cancelling"
    _voice_run_log(db, run, status=log_status, message=run.last_message)
    db.commit()

    from routes.admin import log_admin_action

    log_admin_action(db, current_admin.id, "voice_index_backfill_cancel", {"run_id": run.id})
    return _run_response(run)


@router.get("/api/v1/admin/voice-index/runs/{run_id}/logs")
async def api_get_voice_index_logs(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    run = db.query(VoiceIndexRun).filter(VoiceIndexRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Запуск индексации не найден")
    items, total = list_voice_index_run_logs(db, run_id, page, page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "run": _run_response(run),
        "retention_days": VOICE_RUN_LOG_RETENTION_DAYS,
    }
