"""Superuser API for the replay-independent voice profile corpus."""

from __future__ import annotations

import gzip
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AdminUser,
    PlayerData,
    ReplayVoiceSpeaker,
    VoiceProfileClip,
    VoiceProfileJob,
    VoiceProfileSelection,
)
from routes.admin import load_current_admin
from security import verify_external_api_key_dependency
from services import PlayerService
from services_replays import get_upload_dir
from services_voice_profiles import VoiceProfileService, enqueue_voice_profile_job


router = APIRouter(prefix="/api/v1/admin/voice-profiles", tags=["voice-profiles"])


@router.get("/player-search", dependencies=[Depends(verify_external_api_key_dependency)])
def player_search_for_backfill(
    q: str = Query(..., min_length=2, max_length=100),
    db: Session = Depends(get_db),
):
    """Small API-key adapter for an offline backfill machine.

    The offline tool must not scrape the admin HTML page. Production code uses
    PlayerService directly; this endpoint is only the narrow bridge for a
    machine that has the replay files but not the NexusData database.
    """
    return {
        "players": [
            {
                "player_id": player.player_id,
                "player_last_time_name": player.player_last_time_name,
                "forced_display_name": player.forced_display_name,
                "player_steamid": player.player_steamid,
            }
            for player in PlayerService(db).search_players(q, limit=5)
        ]
    }


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    return load_current_admin(request, db)


def _require_superuser(admin: AdminUser) -> None:
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Доступно только суперпользователю")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _job_payload(job: VoiceProfileJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "mode": job.mode,
        "status": job.status,
        "replay_ids": job.replay_ids or [],
        "current_replay_id": job.current_replay_id,
        "total_replays": job.total_replays,
        "processed_replays": job.processed_replays,
        "mapped_speakers": job.mapped_speakers,
        "unresolved_speakers": job.unresolved_speakers,
        "materialized_clips": job.materialized_clips,
        "attempt_count": job.attempt_count,
        "error_message": job.error_message,
        "heartbeat_at": _iso(job.heartbeat_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "created_at": _iso(job.created_at),
    }


def _speaker_payload(row: ReplayVoiceSpeaker) -> dict[str, Any]:
    return {
        "id": row.id,
        "replay_id": row.replay_id,
        "speaker_id": row.speaker_id,
        "names": row.names or [],
        "candidate_profiles": row.candidate_profiles or [],
        "steam_id": row.steam_id,
        "player_id": row.player_id,
        "resolution_status": row.resolution_status,
        "mapping_source": row.mapping_source,
        "packet_count": row.packet_count,
        "radio_packet_count": row.radio_packet_count,
        "start_ms": row.start_ms,
        "end_ms": row.end_ms,
        "decided_by_admin_id": row.decided_by_admin_id,
        "decided_at": _iso(row.decided_at),
    }


def _clip_payload(clip: VoiceProfileClip, selection: VoiceProfileSelection | None = None) -> dict[str, Any]:
    return {
        "id": clip.id,
        "archive_key": clip.archive_key,
        "steam_id": clip.steam_id,
        "player_id": clip.player_id,
        "source_replay_id": clip.source_replay_id,
        "source_replay_filename": clip.source_replay_filename,
        "source_replay_hash": clip.source_replay_hash,
        "speaker_id": clip.speaker_id,
        "start_ms": clip.start_ms,
        "end_ms": clip.end_ms,
        "packet_count": clip.packet_count,
        "radio_packet_count": clip.radio_packet_count,
        "storage_format": clip.storage_format,
        "storage_size": clip.storage_size,
        "recorded_at": _iso(clip.recorded_at),
        "slot": selection.slot if selection else None,
        "band": selection.band if selection else None,
        "audio_url": f"/api/v1/admin/voice-profiles/clips/{clip.id}/audio",
    }


@router.post("/runs")
async def create_run(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    raw_ids = body.get("replay_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="replay_ids должен быть непустым списком")
    try:
        replay_ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="replay_ids должен содержать только числа")
    if any(value <= 0 for value in replay_ids):
        raise HTTPException(status_code=400, detail="replay_id должен быть положительным")
    try:
        job = enqueue_voice_profile_job(db, replay_ids, mode=str(body.get("mode") or "backfill"))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _job_payload(job)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    job = db.query(VoiceProfileJob).filter(VoiceProfileJob.id == run_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return _job_payload(job)


@router.get("/speakers")
async def list_speakers(
    status: str = "unresolved",
    replay_id: int | None = None,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    query = db.query(ReplayVoiceSpeaker)
    if status and status != "all":
        query = query.filter(ReplayVoiceSpeaker.resolution_status == status)
    if replay_id is not None:
        query = query.filter(ReplayVoiceSpeaker.replay_id == replay_id)
    rows = query.order_by(ReplayVoiceSpeaker.replay_id, ReplayVoiceSpeaker.speaker_id).all()
    return {"speakers": [_speaker_payload(row) for row in rows]}


def _resolve_player(db: Session, body: dict[str, Any]) -> PlayerData:
    steam_id = str(body.get("steam_id") or "").strip()
    player_id = body.get("player_id")
    player = None
    if player_id is not None:
        try:
            player = db.query(PlayerData).filter(PlayerData.player_id == int(player_id)).first()
        except (TypeError, ValueError):
            player = None
        if player is None:
            raise HTTPException(status_code=400, detail="Игрок не найден")
        if steam_id and str(player.player_steamid) != steam_id:
            raise HTTPException(status_code=400, detail="player_id и steam_id принадлежат разным игрокам")
    elif steam_id:
        player = db.query(PlayerData).filter(PlayerData.player_steamid == steam_id).first()
        if player is None:
            raise HTTPException(status_code=400, detail="SteamID не найден в NexusData")
    else:
        raise HTTPException(status_code=400, detail="Нужен steam_id или player_id")
    if not player.player_steamid:
        raise HTTPException(status_code=400, detail="У игрока отсутствует SteamID")
    return player


async def _reindex_after_manual_decision(db: Session, replay_id: int) -> dict[str, Any]:
    try:
        return await run_in_threadpool(VoiceProfileService(db).index_replay, replay_id)
    except RuntimeError as exc:
        # Mapping is already durable. A missing decoder only postpones WAV
        # materialisation; it must not roll back the operator's decision.
        return {"materialization_deferred": str(exc)}


@router.post("/speakers/{speaker_row_id}/resolve")
async def resolve_speaker(
    speaker_row_id: int,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    row = db.query(ReplayVoiceSpeaker).filter(ReplayVoiceSpeaker.id == speaker_row_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Speaker не найден")
    player = _resolve_player(db, body)
    row.steam_id = str(player.player_steamid)
    row.player_id = player.player_id
    row.resolution_status = "mapped"
    row.mapping_source = "manual"
    row.decided_by_admin_id = current_admin.id
    row.decided_at = datetime.utcnow()
    db.commit()
    result = await _reindex_after_manual_decision(db, row.replay_id)
    return {"speaker": _speaker_payload(row), "reindex": result}


@router.post("/speakers/{speaker_row_id}/skip")
async def skip_speaker(
    speaker_row_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    row = db.query(ReplayVoiceSpeaker).filter(ReplayVoiceSpeaker.id == speaker_row_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Speaker не найден")
    row.steam_id = None
    row.player_id = None
    row.resolution_status = "skipped"
    row.mapping_source = "manual"
    row.decided_by_admin_id = current_admin.id
    row.decided_at = datetime.utcnow()
    db.commit()
    result = await _reindex_after_manual_decision(db, row.replay_id)
    return {"speaker": _speaker_payload(row), "reindex": result}


@router.get("/players/{player_id}")
async def get_player_profile(
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if player is None:
        raise HTTPException(status_code=404, detail="Игрок не найден")
    steam_id = str(player.player_steamid)
    selections = db.query(VoiceProfileSelection).filter(
        VoiceProfileSelection.steam_id == steam_id,
    ).order_by(VoiceProfileSelection.slot).all()
    clips = db.query(VoiceProfileClip).filter(
        VoiceProfileClip.steam_id == steam_id,
    ).order_by(VoiceProfileClip.recorded_at, VoiceProfileClip.start_ms).all()
    selection_by_clip = {selection.clip_id: selection for selection in selections}
    return {
        "player": {
            "player_id": player.player_id,
            "steam_id": steam_id,
            "display_name": player.display_name or player.player_last_time_name,
        },
        "clips": [_clip_payload(clip, selection_by_clip.get(clip.id)) for clip in clips],
        "selections": [
            {"slot": selection.slot, "band": selection.band, "clip_id": selection.clip_id}
            for selection in selections
        ],
    }


@router.get("/clips/{clip_id}/audio")
async def get_clip_audio(
    clip_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    clip = db.query(VoiceProfileClip).filter(VoiceProfileClip.id == clip_id).first()
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")
    if clip.storage_format != "wav-gzip" or not clip.wav_filename:
        raise HTTPException(status_code=415, detail="Для клипа нет поддерживаемого WAV-хранилища")
    root = Path(get_upload_dir(db)).resolve()
    path = (root / clip.wav_filename).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(status_code=500, detail="Некорректный путь клипа")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл клипа отсутствует")

    def stream():
        with gzip.open(path, "rb") as source:
            while chunk := source.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="voice-profile-{clip.id}.wav"'},
    )
