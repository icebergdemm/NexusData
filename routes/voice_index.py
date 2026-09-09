"""Superuser controls for the durable replay voice index."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser, PlayerData, ReplayVoiceSpeaker, RoundReplay, VoiceIndexRun
from services_voice_index import (
    VOICE_RUN_LOG_RETENTION_DAYS,
    _voice_run_log,
    get_active_voice_index_run,
    get_voice_index_status,
    list_voice_index_run_logs,
    queue_replay_voice_index,
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
        "partial_count": run.partial_count,
        "error_count": run.error_count,
        "segment_count": run.segment_count,
        "profile_count": run.profile_count,
        "archive_clip_count": run.archive_clip_count,
        "last_replay_id": run.last_replay_id,
        "last_message": run.last_message,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
    }


def _speaker_response(row: ReplayVoiceSpeaker) -> dict:
    return {
        "id": row.id,
        "replay_id": row.replay_id,
        "speaker_id": row.speaker_id,
        "names": row.names or [],
        "candidate_profiles": row.candidate_profiles or [],
        "steam_id": row.steam_id,
        "resolution_status": row.resolution_status,
        "mapping_source": row.mapping_source,
        "packet_count": row.packet_count,
        "start_ms": row.start_ms,
        "end_ms": row.end_ms,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
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


@router.get("/api/v1/admin/voice-index/replays/{replay_id}/speakers")
async def api_get_voice_speaker_details(
    replay_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Return persisted speaker details without reparsing the replay in HTTP."""
    _require_superuser(current_admin)
    replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")
    rows = (
        db.query(ReplayVoiceSpeaker)
        .filter(ReplayVoiceSpeaker.replay_id == replay_id)
        .order_by(ReplayVoiceSpeaker.speaker_id.asc())
        .all()
    )
    return {
        "replay_id": replay_id,
        "replay_filename": replay.filename,
        "status": replay.voice_index_status,
        "voice_index_error": replay.voice_index_error,
        "items": [_speaker_response(row) for row in rows],
    }


@router.post("/api/v1/admin/voice-index/replays/{replay_id}/speakers/resolve")
async def api_resolve_voice_speakers(
    replay_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Persist moderator mapping/skip decisions and reindex asynchronously."""
    _require_superuser(current_admin)
    replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Некорректный JSON") from exc
    decisions = body.get("decisions") if isinstance(body, dict) else None
    if not isinstance(decisions, list) or not decisions:
        raise HTTPException(status_code=422, detail="decisions must be a non-empty array")

    rows = {
        row.speaker_id: row
        for row in db.query(ReplayVoiceSpeaker)
        .filter(ReplayVoiceSpeaker.replay_id == replay_id)
        .all()
    }
    now = datetime.utcnow()
    updated = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise HTTPException(status_code=422, detail="Each decision must be an object")
        try:
            speaker_id = int(decision.get("speaker_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="speaker_id must be an integer") from exc
        row = rows.get(speaker_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"speaker_id {speaker_id} не найден в реплее")

        action = str(decision.get("action") or "").strip().lower()
        if action == "skip":
            row.steam_id = None
            row.resolution_status = "skipped"
            row.mapping_source = "manual_skip"
        elif action == "map":
            steam_id = str(decision.get("steam_id") or "").strip()
            if not steam_id:
                raise HTTPException(status_code=422, detail="steam_id required for map action")
            player = db.query(PlayerData).filter(PlayerData.player_steamid == steam_id).first()
            if not player:
                raise HTTPException(status_code=404, detail="Профиль с таким Steam/EOS ID не найден")
            row.steam_id = player.player_steamid
            row.resolution_status = "mapped"
            row.mapping_source = "manual"
        else:
            raise HTTPException(status_code=422, detail="action must be map or skip")
        row.decided_by_admin_id = current_admin.id
        row.decided_at = now
        updated.append(row)

    replay.voice_index_status = "partial"
    replay.voice_index_version = 0
    # Operator-initiated requeue restarts the bounded retry budget (services_
    # voice_index.VOICE_INDEX_MAX_ATTEMPTS) — an exhausted replay must become
    # pickable again once a human has fixed something for it.
    replay.voice_index_attempts = 0
    db.commit()
    task_id = queue_replay_voice_index(replay_id)
    if not task_id:
        raise HTTPException(status_code=503, detail="Не удалось поставить переиндексацию в очередь")

    from routes.admin import log_admin_action

    log_admin_action(db, current_admin.id, "voice_index_speaker_resolution", {
        "replay_id": replay_id,
        "speaker_ids": [row.speaker_id for row in updated],
    })
    return {
        "status": "queued",
        "task_id": task_id,
        "replay_id": replay_id,
        "items": [_speaker_response(row) for row in updated],
    }


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
    previous_status = run.status
    run.status = "cancelled"
    run.finished_at = now
    run.heartbeat_at = now
    run.last_message = (
        "Индексация остановлена до запуска"
        if previous_status == "pending"
        else "Остановка запрошена; текущий реплей будет завершён"
    )
    log_status = "cancelled"
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
