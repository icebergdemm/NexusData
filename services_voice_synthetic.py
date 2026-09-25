"""Background fake-voice classification for replay voice-profile clips."""

from __future__ import annotations

import datetime
import logging
import threading
import tempfile
import time
from pathlib import Path

from sqlalchemy import exists, func, or_
from services_voice_synthetic_client import (
    get_voice_synthetic_client,
)
from tools.voice_synthetic_constants import (
    FAKE_SCORE_THRESHOLD,
    MAX_WINDOWS_PER_CALL,
    MODEL_ID,
    MODEL_REVISION,
    SOURCE_SAMPLE_RATE,
    WINDOW_MS,
    WINDOW_OVERLAP_MS,
)

logger = logging.getLogger(__name__)

_model_status_cache: tuple[float, dict] | None = None
_model_status_lock = threading.Lock()
LABEL_NAME = "Модуляция голоса"
VOICE_SYNTHETIC_WORKER_SETTING = "voice_synthetic_worker_enabled"
VOICE_SYNTHETIC_LABELS_VISIBLE_SETTING = "voice_synthetic_worker_labels_visible"


def voice_synthetic_worker_enabled(db) -> bool:
    from models import AppSetting

    setting = db.query(AppSetting).filter(AppSetting.key == VOICE_SYNTHETIC_WORKER_SETTING).first()
    return bool(setting and str(setting.value or "").strip().casefold() in {"1", "true", "yes", "on"})


def set_voice_synthetic_worker_enabled(db, enabled: bool) -> bool:
    from models import AppSetting

    setting = db.query(AppSetting).filter(AppSetting.key == VOICE_SYNTHETIC_WORKER_SETTING).first()
    if setting is None:
        setting = AppSetting(key=VOICE_SYNTHETIC_WORKER_SETTING)
        db.add(setting)
    setting.value = "true" if enabled else "false"
    db.flush()
    return bool(enabled)


def voice_synthetic_worker_labels_visible(db) -> bool:
    from models import AppSetting

    setting = db.query(AppSetting).filter(
        AppSetting.key == VOICE_SYNTHETIC_LABELS_VISIBLE_SETTING,
    ).first()
    if setting is None:
        return True
    return str(setting.value or "").strip().casefold() in {"1", "true", "yes", "on"}


def set_voice_synthetic_worker_labels_visible(db, visible: bool) -> bool:
    from models import AppSetting

    setting = db.query(AppSetting).filter(
        AppSetting.key == VOICE_SYNTHETIC_LABELS_VISIBLE_SETTING,
    ).first()
    if setting is None:
        setting = AppSetting(key=VOICE_SYNTHETIC_LABELS_VISIBLE_SETTING)
        db.add(setting)
    setting.value = "true" if visible else "false"
    db.flush()
    return bool(visible)


def voice_synthetic_model_status() -> dict:
    global _model_status_cache
    now = time.monotonic()
    with _model_status_lock:
        if _model_status_cache is not None and now - _model_status_cache[0] < 15:
            return dict(_model_status_cache[1])
    try:
        result = get_voice_synthetic_client().status()
    except Exception as exc:
        logger.warning("Synthetic voice service status failed: %s", type(exc).__name__)
        result = {"status": "unavailable", "revision": None, "device": None}
    result.setdefault("revision", None)
    result.setdefault("device", None)
    with _model_status_lock:
        _model_status_cache = (now, result)
    return dict(result)


def voice_synthetic_monitor_payload(db) -> dict:
    """Return the independent analysis switch and one summary per eligible player."""
    from sqlalchemy import case, func
    from models import (
        PlayerData,
        VoiceProfileClip,
        VoiceSyntheticClipResult,
        VoiceSyntheticPlayerDetection,
    )
    from services_voice_profiles import (
        SPEECH_GATE_VERSION,
        VOICEPRINT_MIN_DURATION_MS,
        VOICEPRINT_MIN_SAMPLE_COUNT,
    )

    enabled = voice_synthetic_worker_enabled(db)
    show_worker_labels = voice_synthetic_worker_labels_visible(db)
    model = voice_synthetic_model_status()
    clip_stats = db.query(
        PlayerData.player_id,
        PlayerData.player_steamid,
        PlayerData.player_last_time_name,
        PlayerData.forced_display_name,
        func.count(func.distinct(VoiceProfileClip.source_replay_filename)).label("replays"),
        func.count(VoiceProfileClip.id).label("clips"),
        func.sum(case(
            (
                (VoiceProfileClip.speech_gate_version == SPEECH_GATE_VERSION)
                & (VoiceProfileClip.speech_status == "accepted"),
                VoiceProfileClip.speech_ms,
            ),
            else_=0,
        )).label("speech_ms"),
    ).join(
        VoiceProfileClip,
        VoiceProfileClip.steam_id == PlayerData.player_steamid,
    ).filter(
        VoiceProfileClip.source_format == "nwdemo-v2",
    ).group_by(
        PlayerData.player_id,
        PlayerData.player_steamid,
        PlayerData.player_last_time_name,
        PlayerData.forced_display_name,
    ).all()

    clip_ids = db.query(VoiceProfileClip.id).filter(VoiceProfileClip.source_format == "nwdemo-v2")
    result_stats = db.query(
        VoiceProfileClip.steam_id,
        VoiceSyntheticClipResult.state,
        func.count(VoiceSyntheticClipResult.id).label("clips"),
        func.sum(VoiceSyntheticClipResult.window_count).label("fragments"),
        func.max(VoiceSyntheticClipResult.fake_score).label("confidence"),
    ).join(
        VoiceSyntheticClipResult,
        (VoiceSyntheticClipResult.clip_id == VoiceProfileClip.id)
        & (VoiceSyntheticClipResult.model_revision == MODEL_REVISION),
    ).filter(
        VoiceProfileClip.id.in_(clip_ids),
    ).group_by(
        VoiceProfileClip.steam_id,
        VoiceSyntheticClipResult.state,
    ).all()
    states_by_player: dict[str, dict[str, dict[str, float | int]]] = {}
    for row in result_stats:
        states_by_player.setdefault(str(row.steam_id), {})[str(row.state)] = {
            "clips": int(row.clips or 0),
            "fragments": int(row.fragments or 0),
            "confidence": float(row.confidence) if row.confidence is not None else None,
        }

    detections = {
        str(row.steam_id): row
        for row in db.query(VoiceSyntheticPlayerDetection).filter(
            VoiceSyntheticPlayerDetection.model_revision == MODEL_REVISION,
        ).all()
    }
    players = []
    for row in clip_stats:
        steam_id = str(row.player_steamid)
        detection = detections.get(steam_id)
        replay_count = int(row.replays or 0)
        speech_ms = int(row.speech_ms or 0)
        enough_data = (
            replay_count >= VOICEPRINT_MIN_SAMPLE_COUNT
            and speech_ms >= VOICEPRINT_MIN_DURATION_MS
        )
        if not enough_data and detection is None:
            continue

        states = states_by_player.get(steam_id, {})
        analyzed_clips = sum(int(states.get(state, {}).get("clips", 0)) for state in ("real", "fake"))
        processed_fragments = sum(int(value.get("fragments", 0)) for value in states.values())
        completed_clips = sum(int(states.get(state, {}).get("clips", 0)) for state in ("real", "fake", "no_speech"))
        pending = max(0, int(row.clips or 0) - completed_clips)
        if detection is not None:
            result = "synthetic"
            confidence = float(detection.confidence)
            analyzed_clips = int(detection.analyzed_clip_count or analyzed_clips)
        else:
            confidence = max(
                (float(value["confidence"]) for value in states.values() if value["confidence"] is not None),
                default=None,
            )
            result = "processing" if pending and enabled and model["status"] == "ready" else (
                "waiting" if pending else "clear"
            )
        players.append({
            "player_id": int(row.player_id),
            "steam_id": steam_id,
            "display_name": row.forced_display_name or row.player_last_time_name or steam_id,
            "result": result,
            "confidence": confidence,
            "replays": replay_count,
            "speech_ms": speech_ms,
            "analyzed_clips": analyzed_clips,
            "processed_fragments": processed_fragments,
            "pending_clips": pending,
        })
    players.sort(key=lambda player: (player["result"] != "synthetic", player["display_name"].casefold()))
    return {
        "enabled": enabled,
        "show_worker_labels": show_worker_labels,
        "model": model,
        "criteria": {
            "min_replays": VOICEPRINT_MIN_SAMPLE_COUNT,
            "min_speech_ms": VOICEPRINT_MIN_DURATION_MS,
        },
        "counts": {
            "eligible_players": len(players),
            "synthetic_players": sum(player["result"] == "synthetic" for player in players),
            "processing_players": sum(player["result"] == "processing" for player in players),
            "waiting_players": sum(player["result"] == "waiting" for player in players),
        },
        "players": players,
    }


def _speech_windows(samples: list[int]):
    width = SOURCE_SAMPLE_RATE * WINDOW_MS // 1000
    step = width - SOURCE_SAMPLE_RATE * WINDOW_OVERLAP_MS // 1000
    if len(samples) <= width:
        yield samples[:]
        return
    last = len(samples) - width
    for start in range(0, last + 1, step):
        yield samples[start : start + width]
    if last % step:
        yield samples[last : last + width]


def _speech_window_count(samples: list[int]) -> int:
    width = SOURCE_SAMPLE_RATE * WINDOW_MS // 1000
    step = width - SOURCE_SAMPLE_RATE * WINDOW_OVERLAP_MS // 1000
    if len(samples) <= width:
        return 1
    last = len(samples) - width
    return last // step + 1 + int(last % step != 0)


def _label_note(confidence: float, count: int) -> str:
    return (
        "Автоматически обнаружены признаки синтетического голоса. "
        f"Результат модели: {confidence:.3f}. Обработано фрагментов: {count}. "
        f"Модель: {MODEL_ID}@{MODEL_REVISION[:12]}."
    )


def _apply_pending_label(db) -> dict | None:
    from models import (
        LabelCategory,
        PlayerData,
        VoiceProfileClip,
        VoiceSyntheticClipResult,
        VoiceSyntheticPlayerDetection,
    )
    from services import PlayerLabelService

    detection = db.query(VoiceSyntheticPlayerDetection).filter(
        VoiceSyntheticPlayerDetection.model_revision == MODEL_REVISION,
        VoiceSyntheticPlayerDetection.label_applied_at.is_(None),
    ).order_by(VoiceSyntheticPlayerDetection.detected_at, VoiceSyntheticPlayerDetection.id).first()
    if detection is None:
        return None
    category = db.query(LabelCategory).filter(LabelCategory.name == LABEL_NAME).first()
    player = db.query(PlayerData).filter(PlayerData.player_id == detection.player_id).first()
    if category is None or player is None:
        db.commit()
        return {"status": "label_pending", "steam_id": detection.steam_id}
    fragments = detection.analyzed_window_count
    if not fragments:
        fragments = db.query(func.sum(VoiceSyntheticClipResult.window_count)).filter(
            VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
            VoiceSyntheticClipResult.clip_id.in_(
                db.query(VoiceProfileClip.id).filter(VoiceProfileClip.steam_id == detection.steam_id)
            ),
        ).scalar() or 0
    PlayerLabelService(db).assign_automatically_if_missing(
        int(player.player_id),
        int(category.id),
        _label_note(detection.confidence, int(fragments)),
    )
    detection.label_applied_at = datetime.datetime.utcnow()
    db.commit()
    return {"status": "labeled", "player_id": int(player.player_id)}


def _candidate_query(db, steam_id: str | None = None):
    from models import PlayerData, VoiceProfileClip, VoiceSyntheticClipResult, VoiceSyntheticPlayerDetection

    query = db.query(VoiceProfileClip).join(
        PlayerData, PlayerData.player_steamid == VoiceProfileClip.steam_id,
    ).filter(
        VoiceProfileClip.source_format == "nwdemo-v2",
        VoiceProfileClip.ndvc_filename.isnot(None),
        or_(
            ~exists().where(
                VoiceSyntheticClipResult.clip_id == VoiceProfileClip.id,
                VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
            ),
            exists().where(
                VoiceSyntheticClipResult.clip_id == VoiceProfileClip.id,
                VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
                VoiceSyntheticClipResult.state == "partial",
            ),
        ),
        ~exists().where(
            VoiceSyntheticPlayerDetection.steam_id == VoiceProfileClip.steam_id,
            VoiceSyntheticPlayerDetection.model_revision == MODEL_REVISION,
        ),
    )
    if steam_id is not None:
        query = query.filter(VoiceProfileClip.steam_id == steam_id)
    return query.order_by(
        VoiceProfileClip.is_radio.asc(),
        VoiceProfileClip.recorded_at,
        VoiceProfileClip.id,
    )


def _recover_cached_fake_detection(db) -> dict | None:
    """Finish a positive result if the worker stopped between inference and tagging."""
    from models import PlayerData, VoiceProfileClip, VoiceSyntheticClipResult, VoiceSyntheticPlayerDetection

    cached = db.query(VoiceSyntheticClipResult, VoiceProfileClip, PlayerData).join(
        VoiceProfileClip, VoiceProfileClip.id == VoiceSyntheticClipResult.clip_id,
    ).join(
        PlayerData, PlayerData.player_steamid == VoiceProfileClip.steam_id,
    ).filter(
        VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
        VoiceSyntheticClipResult.state == "fake",
        ~exists().where(
            VoiceSyntheticPlayerDetection.steam_id == VoiceProfileClip.steam_id,
            VoiceSyntheticPlayerDetection.model_revision == MODEL_REVISION,
        ),
    ).order_by(VoiceSyntheticClipResult.created_at, VoiceSyntheticClipResult.id).first()
    if cached is None:
        return None
    result, clip, player = cached
    clip_count = db.query(VoiceSyntheticClipResult).filter(
        VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
        VoiceSyntheticClipResult.clip_id.in_(
            db.query(VoiceProfileClip.id).filter(VoiceProfileClip.steam_id == clip.steam_id)
        ),
    ).count()
    db.add(VoiceSyntheticPlayerDetection(
        steam_id=clip.steam_id,
        player_id=player.player_id,
        model_revision=MODEL_REVISION,
        confidence=float(result.fake_score),
        evidence_clip_id=clip.id,
        analyzed_clip_count=int(clip_count),
        analyzed_window_count=int(db.query(func.sum(VoiceSyntheticClipResult.window_count)).filter(
            VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
            VoiceSyntheticClipResult.clip_id.in_(
                db.query(VoiceProfileClip.id).filter(VoiceProfileClip.steam_id == clip.steam_id)
            ),
        ).scalar() or 0),
    ))
    db.commit()
    label = _apply_pending_label(db)
    return {"status": "fake_detected" if label and label["status"] == "labeled" else "label_pending"}


def _analyze_clip(db, clip, decoder, classifier) -> tuple[float | None, int, bool, bool]:
    from models import VoiceSyntheticClipResult
    from services_replays import get_upload_dir
    from services_voice_profiles import (
        SPEECH_GATE_VERSION,
        NwdemoVoiceProfileService,
        analyze_pcm16_speech,
        write_pcm16_wav,
    )

    existing_gate = (
        clip.speech_gate_version == SPEECH_GATE_VERSION
        and clip.speech_status in {"accepted", "rejected"}
    )
    if existing_gate and clip.speech_status == "rejected":
        db.add(VoiceSyntheticClipResult(
            clip_id=clip.id,
            model_revision=MODEL_REVISION,
            state="no_speech",
            window_count=0,
        ))
        db.commit()
        return None, 0, False, True

    upload_root = Path(get_upload_dir(db)).resolve()
    ndvc_path = (upload_root / str(clip.ndvc_filename)).resolve()
    if not ndvc_path.is_relative_to(upload_root) or not ndvc_path.is_file():
        raise FileNotFoundError(f"voice archive is unavailable for clip {clip.id}")

    decoder_service = NwdemoVoiceProfileService(db, decoder_path=decoder)
    with tempfile.TemporaryDirectory(prefix="voice-synthetic-") as temp_dir:
        temporary = Path(temp_dir)
        wav_path = decoder_service._decode_ndvc(ndvc_path, temporary, str(clip.archive_key))
        samples, _duration_ms = decoder_service._read_canonical_wav(wav_path)
        if not (existing_gate and clip.speech_status == "accepted"):
            speech = analyze_pcm16_speech(samples)
            clip.speech_status = "accepted" if speech.accepted else "rejected"
            clip.speech_ms = speech.speech_ms
            clip.speech_ratio = speech.speech_ratio
            clip.speech_gate_version = SPEECH_GATE_VERSION
            if not speech.accepted:
                db.add(VoiceSyntheticClipResult(
                    clip_id=clip.id,
                    model_revision=MODEL_REVISION,
                    state="no_speech",
                    window_count=0,
                ))
                db.commit()
                return None, 0, False, True

        from models import VoiceSyntheticClipResult
        result = db.query(VoiceSyntheticClipResult).filter(
            VoiceSyntheticClipResult.clip_id == clip.id,
            VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
        ).first()
        best_score = result.fake_score if result is not None else None
        window_count = int(result.window_count or 0) if result is not None else 0
        next_window_index = int(result.next_window_index or 0) if result is not None else 0
        total_windows = _speech_window_count(samples)
        batch_indexes: list[int] = []
        batch_wavs: list[bytes] = []
        for index, window in enumerate(_speech_windows(samples)):
            if index < next_window_index:
                continue
            if not analyze_pcm16_speech(window).accepted:
                next_window_index = index + 1
                continue
            window_path = temporary / f"window-{index}.wav"
            write_pcm16_wav(window_path, window)
            batch_indexes.append(index)
            batch_wavs.append(window_path.read_bytes())
            next_window_index = index + 1
            if len(batch_wavs) >= MAX_WINDOWS_PER_CALL:
                break

        is_fake = False
        if batch_wavs:
            scores = classifier(batch_wavs)
            if not isinstance(scores, list) or not 1 <= len(scores) <= len(batch_wavs):
                raise RuntimeError("remote classifier returned an invalid batch")
            for index, score in zip(batch_indexes, scores):
                score = float(score)
                if not 0.0 <= score <= 1.0:
                    raise RuntimeError("remote classifier returned a score outside [0, 1]")
                next_window_index = index + 1
                best_score = score if best_score is None else max(best_score, score)
                window_count += 1
                if score >= FAKE_SCORE_THRESHOLD:
                    is_fake = True
                    break
            if len(scores) < len(batch_wavs) and not is_fake:
                raise RuntimeError("remote classifier returned an incomplete batch without a positive result")

        if is_fake:
            state = "fake"
        elif next_window_index >= total_windows:
            state = "real" if best_score is not None else "no_speech"
        else:
            state = "partial"

    if result is None:
        result = VoiceSyntheticClipResult(clip_id=clip.id, model_revision=MODEL_REVISION)
        db.add(result)
    result.state = state
    result.fake_score = best_score
    result.window_count = window_count
    result.next_window_index = next_window_index
    db.commit()
    return best_score, window_count, state == "fake", state != "partial"


def process_next_voice_synthetic_player(db) -> dict:
    """Classify one player's remaining clips, stopping at the first 1.000 fake score."""
    from models import PlayerData, VoiceProfileClip, VoiceSyntheticClipResult, VoiceSyntheticPlayerDetection
    from services_voice_profiles import NwdemoVoiceProfileService

    try:
        if not voice_synthetic_worker_enabled(db):
            return {"status": "disabled"}
        pending = _apply_pending_label(db)
        if pending is not None:
            return pending
        recovered = _recover_cached_fake_detection(db)
        if recovered is not None:
            return recovered
        status = voice_synthetic_model_status()
        if status["status"] != "ready":
            return {"status": status["status"]}
        first_clip = _candidate_query(db).first()
        if first_clip is None:
            return {"status": "idle"}
        steam_id = str(first_clip.steam_id)
        player = db.query(PlayerData).filter(PlayerData.player_steamid == steam_id).first()
        if player is None:
            return {"status": "player_unavailable"}
        clip = _candidate_query(db, steam_id).first()
        decoder_service = NwdemoVoiceProfileService(db)
        if not decoder_service.decoder_path:
            return {"status": "awaiting_decoder"}

        if clip.player_id != player.player_id:
            clip.player_id = player.player_id
        score, _windows, is_fake, is_complete = _analyze_clip(
            db,
            clip,
            decoder_service.decoder_path,
            get_voice_synthetic_client().classify_windows,
        )
        if is_fake:
            analyzed_count = db.query(VoiceSyntheticClipResult).filter(
                VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
                VoiceSyntheticClipResult.clip_id.in_(
                    db.query(VoiceProfileClip.id).filter(VoiceProfileClip.steam_id == steam_id)
                ),
            ).count()
            detection = VoiceSyntheticPlayerDetection(
                steam_id=steam_id,
                player_id=player.player_id,
                model_revision=MODEL_REVISION,
                confidence=float(score),
                evidence_clip_id=clip.id,
                analyzed_clip_count=int(analyzed_count),
                analyzed_window_count=int(db.query(func.sum(VoiceSyntheticClipResult.window_count)).filter(
                    VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
                    VoiceSyntheticClipResult.clip_id.in_(
                        db.query(VoiceProfileClip.id).filter(VoiceProfileClip.steam_id == steam_id)
                    ),
                ).scalar() or 0),
            )
            db.add(detection)
            db.flush()
            label_result = _apply_pending_label(db)
            return {
                "status": "fake_detected" if label_result and label_result["status"] == "labeled" else "label_pending",
                "steam_id": steam_id,
                "clips_analyzed": int(analyzed_count),
                "confidence": float(score),
            }
        remaining = _candidate_query(db, steam_id).first() is not None
        return {
            "status": "player_in_progress" if remaining or not is_complete else "player_scanned",
            "steam_id": steam_id,
            "clips_analyzed": int(db.query(VoiceSyntheticClipResult).filter(
                VoiceSyntheticClipResult.model_revision == MODEL_REVISION,
                VoiceSyntheticClipResult.clip_id.in_(
                    db.query(VoiceProfileClip.id).filter(VoiceProfileClip.steam_id == steam_id)
                ),
            ).count()),
        }
    except Exception:
        db.rollback()
        logger.exception("Synthetic voice classifier worker failed")
        raise
