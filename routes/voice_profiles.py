"""Superuser API for the replay-independent voice profile corpus."""

from __future__ import annotations

import gzip
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AdminUser,
    PlayerData,
    ReplayVoiceSpeaker,
    RoundReplay,
    VoiceProfileClip,
    VoiceProfileImport,
    VoiceProfileJob,
    VoiceProfileSelection,
    VoiceProfileSignature,
    VoiceReplayFingerprint,
)
from routes.admin import load_current_admin, log_admin_action
from security import verify_external_api_key_dependency
from services import PlayerService
from services_replays import get_upload_dir
from services_voice_profiles import (
    ONNX_VOICEPRINT_ALGORITHM,
    NwdemoVoiceProfileService,
    VoiceProfileService,
    enqueue_voice_profile_job,
    rank_voiceprint_candidates,
    set_voice_profile_scanner_enabled,
    voiceprint_is_eligible,
    voiceprint_model_status,
    voice_profile_scanner_enabled,
)
from services_voice_profile_import import commit_import_package, validate_import_package


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


def _nwdemo_replay_filter():
    return or_(
        RoundReplay.replay_format == "nwdemo-v2",
        RoundReplay.filename.ilike("%.nwdemo"),
        RoundReplay.stored_filename.ilike("%.nwdemo"),
    )


def _scanner_payload(db: Session) -> dict[str, Any]:
    replays = db.query(RoundReplay).filter(_nwdemo_replay_filter()).order_by(
        RoundReplay.voice_profile_processed_at.desc(),
        RoundReplay.uploaded_at.desc(),
        RoundReplay.id.desc(),
    ).all()
    counts = {"pending": 0, "processing": 0, "ready": 0, "failed": 0}
    for replay in replays:
        state = replay.voice_profile_state or "pending"
        if state in counts:
            counts[state] += 1

    recent = replays[:50]
    replay_ids = [replay.id for replay in recent]
    clips = db.query(VoiceProfileClip).filter(
        VoiceProfileClip.source_format == "nwdemo-v2",
        VoiceProfileClip.source_replay_id.in_(replay_ids or {-1}),
        VoiceProfileClip.player_id.isnot(None),
    ).all()
    player_ids = {clip.player_id for clip in clips if clip.player_id is not None}
    players = db.query(PlayerData).filter(PlayerData.player_id.in_(player_ids or {-1})).all()
    player_by_id = {player.player_id: player for player in players}
    players_by_replay: dict[int, list[dict[str, Any]]] = {}
    seen_by_replay: dict[int, set[int]] = {}
    for clip in clips:
        replay_id = int(clip.source_replay_id)
        player_id = int(clip.player_id)
        if player_id in seen_by_replay.setdefault(replay_id, set()):
            continue
        seen_by_replay[replay_id].add(player_id)
        player = player_by_id.get(player_id)
        if player is None:
            continue
        players_by_replay.setdefault(replay_id, []).append({
            "player_id": player.player_id,
            "display_name": player.display_name or player.player_last_time_name,
        })

    return {
        "enabled": voice_profile_scanner_enabled(db),
        "counts": counts,
        "log": [
            {
                "replay_id": replay.id,
                "state": replay.voice_profile_state or "pending",
                "replay_kind": replay.replay_kind,
                "recorded_at": _iso(replay.recorded_at),
                "processed_at": _iso(replay.voice_profile_processed_at),
                "error": replay.voice_profile_error,
                "players": players_by_replay.get(replay.id, []),
            }
            for replay in recent
        ],
    }


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
        "source_replay_id": clip.source_replay_id,
        "source_replay_filename": clip.source_replay_filename,
        "source_kind": getattr(clip, "source_kind", None),
        "radio": bool(getattr(clip, "is_radio", False)),
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


def _import_payload(row: VoiceProfileImport) -> dict[str, Any]:
    """Expose import progress without leaking the server-side staging path."""
    return {
        "id": row.id,
        "import_key": row.import_key,
        "status": row.status,
        "manifest_sha256": row.manifest_sha256,
        "total_profiles": row.total_profiles,
        "total_clips": row.total_clips,
        "imported_profiles": row.imported_profiles,
        "imported_clips": row.imported_clips,
        "error_message": row.error_message,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


_IMPORT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _import_key(body: dict[str, Any]) -> str:
    value = str(body.get("import_key") or "").strip()
    if not _IMPORT_KEY_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Некорректный import_key")
    return value


def _profile_players_payload(
    clips: list[VoiceProfileClip],
    selections: list[VoiceProfileSelection],
    players: list[PlayerData],
) -> list[dict[str, Any]]:
    player_by_steam = {
        str(player.player_steamid): player
        for player in players
        if player.player_steamid
    }
    selection_by_clip = {selection.clip_id: selection for selection in selections}
    grouped: dict[str, dict[str, Any]] = {}
    for clip in clips:
        selection = selection_by_clip.get(clip.id)
        if selection is None:
            continue
        steam_id = str(clip.steam_id)
        player = player_by_steam.get(steam_id)
        profile = grouped.setdefault(
            steam_id,
            {
                "player_id": player.player_id if player else clip.player_id,
                "steam_id": steam_id,
                "display_name": (
                    (player.display_name or player.player_last_time_name)
                    if player
                    else steam_id
                ),
                "clips": [],
            },
        )
        profile["clips"].append(_clip_payload(clip, selection))
    return sorted(
        grouped.values(),
        key=lambda profile: (str(profile["display_name"]).casefold(), profile["steam_id"]),
    )


@router.post("/replays/{replay_id}/process")
async def process_one_nwdemo_replay(
    replay_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Controlled first-run probe before enabling the periodic scanner."""
    _require_superuser(current_admin)
    try:
        return await run_in_threadpool(NwdemoVoiceProfileService(db).index_replay, replay_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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


@router.get("/runs/latest")
async def get_latest_run(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    job = db.query(VoiceProfileJob).order_by(
        VoiceProfileJob.created_at.desc(),
        VoiceProfileJob.id.desc(),
    ).first()
    return {"run": _job_payload(job) if job is not None else None}


@router.post("/imports")
async def validate_import(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Validate an SFTP-staged local corpus without importing any clips."""
    _require_superuser(current_admin)
    import_key = _import_key(body)
    staging_dir = Path(get_upload_dir(db)) / "voice_profile_imports" / import_key
    try:
        summary = await run_in_threadpool(validate_import_package, staging_dir, import_key)
    except ValueError as exc:
        record = db.query(VoiceProfileImport).filter(
            VoiceProfileImport.import_key == import_key,
        ).first()
        if record is None:
            record = VoiceProfileImport(
                import_key=import_key,
                staging_path=str(staging_dir),
                manifest_sha256="",
            )
            db.add(record)
        record.status = "failed"
        record.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = db.query(VoiceProfileImport).filter(
        VoiceProfileImport.import_key == import_key,
    ).first()
    if record is not None and record.status == "committed" and record.manifest_sha256 == summary["manifest_sha256"]:
        return _import_payload(record)
    if record is None:
        record = VoiceProfileImport(
            import_key=import_key,
            staging_path=str(staging_dir),
            manifest_sha256=summary["manifest_sha256"],
        )
        db.add(record)
    record.status = "validated"
    record.staging_path = str(staging_dir)
    record.manifest_sha256 = summary["manifest_sha256"]
    record.total_profiles = summary["total_profiles"]
    record.total_clips = summary["total_clips"]
    record.error_message = None
    db.commit()
    return _import_payload(record)


@router.get("/imports/latest")
async def get_latest_import(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    row = db.query(VoiceProfileImport).order_by(
        VoiceProfileImport.created_at.desc(),
        VoiceProfileImport.id.desc(),
    ).first()
    return {"import": _import_payload(row) if row is not None else None}


@router.get("/imports/{import_id}")
async def get_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    row = db.query(VoiceProfileImport).filter(VoiceProfileImport.id == import_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Импорт не найден")
    return _import_payload(row)


@router.post("/imports/{import_id}/commit")
async def commit_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    row = db.query(VoiceProfileImport).filter(VoiceProfileImport.id == import_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Импорт не найден")
    if row.status == "committed":
        return _import_payload(row)
    if row.status not in {"validated", "failed", "staged"}:
        raise HTTPException(status_code=409, detail="Импорт уже выполняется")
    try:
        result = await run_in_threadpool(
            commit_import_package,
            db,
            Path(row.staging_path),
            row.import_key,
        )
    except ValueError as exc:
        db.rollback()
        row = db.query(VoiceProfileImport).filter(VoiceProfileImport.id == import_id).first()
        if row is not None:
            row.status = "failed"
            row.error_message = str(exc)
            db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        row = db.query(VoiceProfileImport).filter(VoiceProfileImport.id == import_id).first()
        if row is not None:
            row.status = "failed"
            row.error_message = str(exc)[:2000]
            db.commit()
        raise HTTPException(status_code=500, detail="Импорт завершился ошибкой; состояние сохранено") from exc
    return result


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


@router.get("/players")
async def list_player_profiles(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    clips = db.query(VoiceProfileClip).order_by(
        VoiceProfileClip.steam_id,
        VoiceProfileClip.recorded_at,
        VoiceProfileClip.start_ms,
    ).filter(VoiceProfileClip.source_format == "nwdemo-v2").all()
    steam_ids = sorted({str(clip.steam_id) for clip in clips if clip.steam_id})
    if not steam_ids:
        return {"players": []}
    selections = db.query(VoiceProfileSelection).filter(
        VoiceProfileSelection.steam_id.in_(steam_ids),
    ).all()
    players = db.query(PlayerData).filter(
        PlayerData.player_steamid.in_(steam_ids),
    ).all()
    return {"players": _profile_players_payload(clips, selections, players)}


@router.get("/scanner")
async def get_voice_profile_scanner(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Expose the persisted scanner gate and recent nwdemo-only activity."""
    _require_superuser(current_admin)
    return _scanner_payload(db)


@router.patch("/scanner")
async def set_voice_profile_scanner(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled должен быть boolean")
    set_voice_profile_scanner_enabled(db, enabled)
    db.commit()
    log_admin_action(db, current_admin.id, "voice_profile_scanner_toggle", {"enabled": enabled})
    return _scanner_payload(db)


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
    selected_ids = [selection.clip_id for selection in selections]
    clips = db.query(VoiceProfileClip).filter(
        VoiceProfileClip.id.in_(selected_ids or {-1}),
        VoiceProfileClip.source_format == "nwdemo-v2",
    ).order_by(VoiceProfileClip.recorded_at, VoiceProfileClip.source_replay_id, VoiceProfileClip.start_ms).all()
    selection_by_clip = {selection.clip_id: selection for selection in selections}
    fingerprints = db.query(VoiceReplayFingerprint).filter(
        VoiceReplayFingerprint.steam_id == steam_id,
        VoiceReplayFingerprint.algorithm == ONNX_VOICEPRINT_ALGORITHM,
    ).all()
    signatures = db.query(VoiceProfileSignature).filter(
        VoiceProfileSignature.steam_id == steam_id,
        VoiceProfileSignature.algorithm == ONNX_VOICEPRINT_ALGORITHM,
    ).all()
    signature_rows = [{
        "steam_id": signature.steam_id,
        "source_channel": signature.source_channel,
        "vector": signature.vector,
        "sample_count": signature.sample_count,
        "duration_ms": signature.weighted_duration_ms,
    } for signature in signatures]
    all_signatures = db.query(VoiceProfileSignature).filter(
        VoiceProfileSignature.algorithm == ONNX_VOICEPRINT_ALGORITHM,
    ).all()
    all_signature_rows = [{
        "steam_id": signature.steam_id,
        "source_channel": signature.source_channel,
        "vector": signature.vector,
        "sample_count": signature.sample_count,
        "duration_ms": signature.weighted_duration_ms,
    } for signature in all_signatures]
    best_candidates: dict[str, dict[str, object]] = {}
    for target in signature_rows:
        for candidate in rank_voiceprint_candidates(target, all_signature_rows):
            steam = str(candidate["steam_id"])
            if steam not in best_candidates or float(candidate["score"]) > float(best_candidates[steam]["score"]):
                best_candidates[steam] = candidate
    ranked_candidates = sorted(
        best_candidates.values(), key=lambda candidate: (-float(candidate["score"]), str(candidate["steam_id"]))
    )[:10]
    player_by_steam = {
        str(candidate.player_steamid): candidate
        for candidate in db.query(PlayerData).filter(
            PlayerData.player_steamid.in_([candidate["steam_id"] for candidate in ranked_candidates] or ["-1"]),
        ).all()
    }
    voiceprint_status = "model_missing" if voiceprint_model_status()["status"] != "ready" else (
        "ready" if any(voiceprint_is_eligible(row) for row in signature_rows) else "collecting"
    )
    return {
        "player": {
            "player_id": player.player_id,
            "steam_id": steam_id,
            "display_name": player.display_name or player.player_last_time_name,
        },
        "clips": [_clip_payload(clip, selection_by_clip[clip.id]) for clip in clips],
        "stats": {
            "selected_clips": len(clips),
            "radio_clips": sum(1 for clip in clips if bool(getattr(clip, "is_radio", False))),
            "replay_kinds": sorted({str(clip.source_kind) for clip in clips if clip.source_kind}),
        },
        "voiceprint": {
            "algorithm": ONNX_VOICEPRINT_ALGORITHM,
            "status": voiceprint_status,
            "replay_samples": len(fingerprints),
            "aggregate_channels": sorted({signature.source_channel for signature in signatures}),
            "candidates": [{
                **candidate,
                "player_id": getattr(player_by_steam.get(str(candidate["steam_id"])), "player_id", None),
                "display_name": (
                    getattr(player_by_steam.get(str(candidate["steam_id"])), "display_name", None)
                    or getattr(player_by_steam.get(str(candidate["steam_id"])), "player_last_time_name", None)
                ),
            } for candidate in ranked_candidates],
        },
    }


@router.get("/clips/{clip_id}/audio")
async def get_clip_audio(
    clip_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    clip = db.query(VoiceProfileClip).filter(VoiceProfileClip.id == clip_id).first()
    if clip is None or getattr(clip, "source_format", None) != "nwdemo-v2":
        raise HTTPException(status_code=404, detail="Клип не найден")
    selected = db.query(VoiceProfileSelection).filter(
        VoiceProfileSelection.clip_id == clip.id,
    ).first()
    if selected is None or not clip.wav_filename:
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
