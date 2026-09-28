"""NexusData-side dispatch and idempotent result import for external jobs."""
import hashlib
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import or_


def _log(db, node_id, message, level="info", job_id=None):
    from models import ExternalWorkerLog
    db.add(ExternalWorkerLog(worker_id=node_id, job_id=job_id, level=level, message=str(message)[:2000]))


def _apply_voice_result(db, job):
    from models import RoundReplay, VoiceProfileClip
    from services_replays import ReplayService, get_upload_dir
    from services_voice_profiles import (
        NwdemoVoiceProfileService, NWDEMO_PROFILE_ARCHIVE_VERSION,
        SPEECH_GATE_VERSION, make_nwdemo_archive_key,
    )

    result = job.result or {}
    replay_id = int((job.payload or {}).get("replay_id") or 0)
    if result.get("schema") != "nexusdata.voice-profile-extraction.v1" or int(result.get("replay_id") or 0) != replay_id:
        raise ValueError("External voice-profile result has an invalid schema or replay ID")
    replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).with_for_update().first()
    if replay is None:
        raise ValueError("Replay no longer exists")
    archive_path = Path(ReplayService(db).get_file_path(replay))
    digest = hashlib.sha256()
    with archive_path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != result.get("replay_sha256"):
        raise ValueError("Worker output does not match the source archive")
    artifact = Path(job.result_artifact_path or "")
    if not artifact.is_file():
        raise ValueError("Worker result package is missing")
    root = Path(get_upload_dir(db))
    destination = root / "voice_profiles" / "ndvc"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(artifact) as package:
        names = set(package.namelist())
        if "manifest.json" not in names:
            raise ValueError("Worker package has no manifest")
        manifest = __import__("json").loads(package.read("manifest.json"))
        if (manifest.get("schema") != result["schema"] or manifest.get("replay_sha256") != digest.hexdigest()
                or int(manifest.get("replay_id") or 0) != replay_id
                or len(manifest.get("segments", [])) != int(result.get("segment_count") or -1)):
            raise ValueError("Worker manifest does not match the result")
        service = NwdemoVoiceProfileService(db)
        steam_ids = set()
        for segment in manifest.get("segments", []):
            key = str(segment.get("archive_key") or "")
            steam_id = str(segment.get("steam_id") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", key) or not steam_id.isdigit():
                raise ValueError("Worker returned an invalid segment identity")
            expected_key = make_nwdemo_archive_key(
                digest.hexdigest(), steam_id, bool(segment.get("is_radio")),
                int(segment.get("start_ms") or 0), int(segment.get("end_ms") or 0),
            )
            if key != expected_key or int(segment.get("end_ms") or -1) < int(segment.get("start_ms") or 0):
                raise ValueError("Worker returned an inconsistent segment key or time range")
            member = str(segment.get("ndvc_filename") or "")
            path = PurePosixPath(member)
            if path.is_absolute() or ".." in path.parts or member != f"segments/{key}.ndvc" or member not in names:
                raise ValueError("Worker package contains an invalid segment path")
            target_relative = f"voice_profiles/ndvc/{key}.ndvc"
            target = root / target_relative
            temporary = target.with_suffix(".ndvc.part")
            with package.open(member) as input_file, temporary.open("wb") as output_file:
                size = 0
                for block in iter(lambda: input_file.read(1024 * 1024), b""):
                    size += len(block)
                    if size > 256 * 1024 * 1024:
                        raise ValueError("A worker clip exceeded the size limit")
                    output_file.write(block)
            if size != int(segment.get("storage_size") or -1):
                temporary.unlink(missing_ok=True)
                raise ValueError("Worker clip size differs from its manifest")
            os.replace(temporary, target)
            clip = db.query(VoiceProfileClip).filter(VoiceProfileClip.archive_key == key).first()
            speech_status = str(segment.get("speech_status") or "")
            speech_ms = int(segment.get("speech_ms") or 0)
            speech_ratio = float(segment.get("speech_ratio") or 0)
            if (speech_status not in {"accepted", "rejected"}
                    or segment.get("speech_gate_version") != SPEECH_GATE_VERSION
                    or speech_ms < 0 or not 0 <= speech_ratio <= 1):
                raise ValueError("Worker returned invalid speech-gate metrics")
            wav_relative = None
            wav_size = 0
            wav_member = segment.get("wav_member")
            if speech_status == "accepted":
                if wav_member != f"speech/{key}.wav.gz" or wav_member not in names:
                    raise ValueError("Worker omitted an accepted speech clip")
                wav_relative = f"voice_profiles/wav/{key}.wav.gz"
                wav_target = root / wav_relative
                wav_target.parent.mkdir(parents=True, exist_ok=True)
                wav_temporary = wav_target.with_name(wav_target.name + ".part")
                with package.open(wav_member) as input_file, wav_temporary.open("wb") as output_file:
                    for block in iter(lambda: input_file.read(1024 * 1024), b""):
                        wav_size += len(block)
                        if wav_size > 256 * 1024 * 1024:
                            wav_temporary.unlink(missing_ok=True)
                            raise ValueError("Worker speech clip exceeded the size limit")
                        output_file.write(block)
                if wav_size != int(segment.get("wav_size") or -1):
                    wav_temporary.unlink(missing_ok=True)
                    raise ValueError("Worker speech clip size differs from its manifest")
                os.replace(wav_temporary, wav_target)
            elif wav_member is not None:
                raise ValueError("Worker returned an audio file for a rejected speech clip")
            previous_wav = getattr(clip, "wav_filename", None) if clip else None
            if speech_status != "accepted" and previous_wav:
                old_path = PurePosixPath(str(previous_wav))
                if not old_path.is_absolute() and ".." not in old_path.parts:
                    (root / Path(*old_path.parts)).unlink(missing_ok=True)
            values = {
                "steam_id": steam_id,
                "player_id": service._lookup_player_id(steam_id),
                "source_replay_id": replay.id,
                "source_replay_filename": replay.filename,
                "source_replay_hash": digest.hexdigest(),
                "speaker_id": None,
                "start_ms": int(segment["start_ms"]),
                "end_ms": int(segment["end_ms"]),
                "packet_count": int(segment["packet_count"]),
                "radio_packet_count": int(segment["radio_packet_count"]),
                "ndvc_filename": target_relative,
                "wav_filename": wav_relative,
                "storage_format": "ndvc+wav-gzip" if wav_relative else "ndvc",
                "storage_size": size + wav_size,
                "archive_version": NWDEMO_PROFILE_ARCHIVE_VERSION,
                "recorded_at": replay.recorded_at or replay.uploaded_at,
                "source_format": "nwdemo-v2",
                "source_kind": replay.replay_kind,
                "is_radio": bool(segment["is_radio"]),
                "speech_status": speech_status, "speech_ms": speech_ms,
                "speech_ratio": speech_ratio, "speech_gate_version": SPEECH_GATE_VERSION,
            }
            if clip is None:
                clip = VoiceProfileClip(archive_key=key, **values)
                db.add(clip)
            else:
                for field, value in values.items():
                    setattr(clip, field, value)
            steam_ids.add(steam_id)
    db.flush()
    pending_players = []
    for steam_id in sorted(steam_ids):
        service.reconcile_steam_id(steam_id, new_replay_id=replay_id)
        if service.has_unknown_speech_gate_clips(steam_id, replay_id):
            pending_players.append(steam_id)
    replay.replay_format = "nwdemo-v2"
    replay.voice_profile_state = "pending" if pending_players else "ready"
    replay.voice_profile_error = None
    replay.voice_profile_processed_at = datetime.utcnow()
    replay.voice_profile_started_at = None
    replay.voice_profile_heartbeat_at = None
    replay.voice_profile_progress = ({"mode": "speech_gate_queue", "pending_steam_ids": pending_players}
                                     if pending_players else None)
    return {"status": replay.voice_profile_state, "replay_id": replay_id,
            "segments": len(manifest.get("segments", [])), "speakers": len(steam_ids)}


def _apply_voiceprint_result(db, job):
    import math
    from models import RoundReplay, VoiceReplayFingerprint
    from services_replays import ReplayService
    from services_voice_profiles import (
        ONNX_VOICEPRINT_ALGORITHM, ONNX_VOICEPRINT_DIMENSIONS, VoiceProfileService,
    )

    result = job.result or {}
    replay_id = int((job.payload or {}).get("replay_id") or 0)
    if result.get("schema") != "nexusdata.voiceprint.v1" or int(result.get("replay_id") or 0) != replay_id:
        raise ValueError("External voiceprint result has an invalid schema or replay ID")
    replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).with_for_update().first()
    if replay is None or replay.replay_format != "nwdemo-v2":
        raise ValueError("Voiceprint source replay is no longer ready")
    digest = hashlib.sha256()
    with Path(ReplayService(db).get_file_path(replay)).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != result.get("replay_sha256"):
        raise ValueError("Worker output does not match the source archive")
    processed = result.get("processed_steam_ids")
    fingerprints = result.get("fingerprints")
    if not isinstance(processed, list) or not isinstance(fingerprints, list):
        raise ValueError("Worker returned an invalid voiceprint payload")
    steam_ids = {str(value) for value in processed}
    if any(not value.isdigit() for value in steam_ids):
        raise ValueError("Worker returned an invalid Steam ID")
    by_steam_id = {}
    for item in fingerprints:
        steam_id = str(item.get("steam_id") or "")
        vector = item.get("vector")
        if (steam_id not in steam_ids or item.get("source_channel") not in {"normal", "radio"}
                or item.get("quality") not in {"good", "low"}
                or not isinstance(vector, list) or len(vector) != ONNX_VOICEPRINT_DIMENSIONS
                or any(not math.isfinite(float(value)) for value in vector)):
            raise ValueError("Worker returned an invalid embedding")
        if steam_id in by_steam_id:
            raise ValueError("Worker returned duplicate embeddings for one player")
        by_steam_id[steam_id] = item
    service = VoiceProfileService(db)
    for steam_id in steam_ids:
        row = db.query(VoiceReplayFingerprint).filter(
            VoiceReplayFingerprint.replay_id == replay_id,
            VoiceReplayFingerprint.steam_id == steam_id,
            VoiceReplayFingerprint.algorithm == ONNX_VOICEPRINT_ALGORITHM,
        ).first()
        item = by_steam_id.get(steam_id)
        if item is None:
            if row is not None:
                db.delete(row)
        else:
            values = {
                "source_channel": item["source_channel"],
                "vector": [float(value) for value in item["vector"]],
                "quality": item["quality"],
                "duration_ms": max(0, int(item.get("duration_ms") or 0)),
                "packet_count": max(0, int(item.get("packet_count") or 0)),
            }
            if row is None:
                db.add(VoiceReplayFingerprint(
                    replay_id=replay_id, steam_id=steam_id,
                    algorithm=ONNX_VOICEPRINT_ALGORITHM, **values,
                ))
            else:
                for field, value in values.items():
                    setattr(row, field, value)
        db.flush()
        service._update_signature(steam_id, "normal", ONNX_VOICEPRINT_ALGORITHM)
        service._update_signature(steam_id, "radio", ONNX_VOICEPRINT_ALGORITHM)
    replay.voiceprint_processed_at = datetime.utcnow()
    replay.voice_profile_state = "ready"
    replay.voice_profile_error = None
    replay.voice_profile_started_at = None
    replay.voice_profile_heartbeat_at = None
    replay.voice_profile_progress = None
    return {"status": "voiceprint_processed", "replay_id": replay_id,
            "fingerprints": len(by_steam_id), "speakers": len(steam_ids)}


def dispatch_voice_profile_work(db) -> dict:
    """Dispatch archive extraction and ONNX embeddings to remote worker plugins."""
    from models import ExternalWorkerJob, ExternalWorkerNode, RoundReplay, VoiceProfileClip
    from services_voice_profiles import (
        NwdemoVoiceProfileService, _process_nwdemo_speech_gate_queue,
        recover_stale_nwdemo_processing, voice_profile_scanner_enabled,
        voiceprint_model_path, voiceprint_worker_enabled,
    )
    from sqlalchemy import exists

    recovery = recover_stale_nwdemo_processing(db)
    if not voice_profile_scanner_enabled(db):
        return {"status": "disabled", **recovery}
    nodes = db.query(ExternalWorkerNode).filter(ExternalWorkerNode.enabled.is_(True)).all()
    online = [node for node in nodes if node.last_seen_at and
              (datetime.utcnow() - node.last_seen_at).total_seconds() < 60]
    capable_extract = any("voice_profile.extract" in (node.capabilities or []) for node in online)
    capable_voiceprint = any("voice_profile.voiceprint" in (node.capabilities or []) for node in online)

    completed_ids = [row[0] for row in db.query(ExternalWorkerJob.id).filter(
        ExternalWorkerJob.task_type.in_(("voice_profile.extract", "voice_profile.voiceprint")),
        ExternalWorkerJob.status == "completed",
        ExternalWorkerJob.payload.isnot(None),
    ).order_by(ExternalWorkerJob.id).all()]
    imported = 0
    for job_id in completed_ids:
        job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job_id).with_for_update(skip_locked=True).first()
        if job is None or job.status != "completed":
            continue
        if (job.result or {}).get("applied"):
            continue
        try:
            if job.task_type == "voice_profile.extract":
                result = _apply_voice_result(db, job)
            else:
                result = _apply_voiceprint_result(db, job)
            job.result = {**(job.result or {}), "applied": True, "import": result}
            db.commit()
            if job.result_artifact_path:
                Path(job.result_artifact_path).unlink(missing_ok=True)
                job.result_artifact_path = None
                db.commit()
            imported += 1
        except Exception as exc:
            db.rollback()
            job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job.id).first()
            job.status = "failed"
            job.error = f"Result import failed: {exc}"[:2000]
            job.finished_at = datetime.utcnow()
            replay_id = int((job.payload or {}).get("replay_id") or 0)
            replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
            if replay:
                replay.voice_profile_state = "failed"
                replay.voice_profile_error = job.error
            db.commit()

    enqueued = 0
    if capable_extract:
        pending = db.query(RoundReplay).filter(
            or_(RoundReplay.replay_format == "nwdemo-v2", RoundReplay.filename.ilike("%.nwdemo"),
                RoundReplay.stored_filename.ilike("%.nwdemo")),
            or_(RoundReplay.voice_profile_state.in_(("pending", "awaiting_decoder")),
                RoundReplay.voice_profile_state.is_(None)),
        ).order_by(RoundReplay.id).with_for_update(skip_locked=True).limit(50).all()
        pending.sort(key=lambda replay: (
            (replay.voice_profile_progress or {}).get("mode") != "speech_gate_queue",
            replay.id,
        ))
        for replay in pending:
            if enqueued >= 10:
                break
            dedupe = f"voice-profile:{replay.id}"
            existing = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.dedupe_key == dedupe).first()
            is_speech_gate_retry = (replay.voice_profile_progress or {}).get("mode") == "speech_gate_queue"
            if (existing and existing.status not in {"failed", "cancelled"}
                    and not (is_speech_gate_retry and existing.status == "completed"
                             and (existing.result or {}).get("applied"))):
                continue
            if existing:
                if existing.result_artifact_path:
                    Path(existing.result_artifact_path).unlink(missing_ok=True)
                existing.status = "queued"
                existing.payload = {"replay_id": replay.id,
                                    "allow_empty_world": replay.replay_kind in {"lobby", "lobby_dump"}}
                existing.result = None
                existing.result_artifact_path = None
                existing.error = None
                existing.attempts = 0
            else:
                job = ExternalWorkerJob(task_type="voice_profile.extract", dedupe_key=dedupe,
                                        payload={"replay_id": replay.id,
                                                 "allow_empty_world": replay.replay_kind in {"lobby", "lobby_dump"}},
                                        status="queued")
                db.add(job)
            replay.voice_profile_state = "processing"
            replay.voice_profile_started_at = datetime.utcnow()
            replay.voice_profile_heartbeat_at = datetime.utcnow()
            replay.voice_profile_progress = {"mode": "external_worker", "phase": "queued"}
            replay.voice_profile_error = None
            enqueued += 1
        db.commit()

    voiceprints_queued = 0
    voiceprint_enabled = voiceprint_worker_enabled(db)
    nd_model_ready = voiceprint_model_path().is_file()
    if capable_voiceprint and voiceprint_enabled and nd_model_ready:
        candidates = db.query(RoundReplay).filter(
            RoundReplay.replay_format == "nwdemo-v2",
            RoundReplay.voice_profile_state == "ready",
            RoundReplay.voiceprint_processed_at.is_(None),
            exists().where(VoiceProfileClip.source_replay_id == RoundReplay.id),
        ).order_by(RoundReplay.id).with_for_update(skip_locked=True).limit(50).all()
        for replay in candidates:
            if voiceprints_queued >= 10:
                break
            dedupe = f"voiceprint:{replay.id}"
            existing = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.dedupe_key == dedupe).first()
            if existing:
                continue
            db.add(ExternalWorkerJob(
                task_type="voice_profile.voiceprint", dedupe_key=dedupe,
                payload={"replay_id": replay.id,
                         "allow_empty_world": replay.replay_kind in {"lobby", "lobby_dump"}},
            ))
            replay.voice_profile_state = "processing"
            replay.voice_profile_started_at = datetime.utcnow()
            replay.voice_profile_heartbeat_at = datetime.utcnow()
            replay.voice_profile_progress = {"mode": "external_worker", "phase": "voiceprint"}
            voiceprints_queued += 1
        db.commit()

    gate_rows = db.query(RoundReplay).filter(
        RoundReplay.voice_profile_state == "pending",
    ).order_by(RoundReplay.id).with_for_update(skip_locked=True).limit(50).all()
    gate_replay = next((row for row in gate_rows
                        if (row.voice_profile_progress or {}).get("mode") == "speech_gate_queue"), None)
    if gate_replay is not None:
        if capable_extract:
            result = {"status": "waiting_for_external_worker", "replay_id": gate_replay.id}
        else:
            service = NwdemoVoiceProfileService(db)
            if service.decoder_path:
                result = _process_nwdemo_speech_gate_queue(db, gate_replay, service)
            else:
                result = {"status": "awaiting_decoder", "replay_id": gate_replay.id}
    elif not voiceprint_enabled:
        result = {"status": "voiceprint_paused"}
    else:
        candidates = db.query(RoundReplay.id).filter(
            RoundReplay.replay_format == "nwdemo-v2",
            RoundReplay.voice_profile_state == "ready",
            RoundReplay.voiceprint_processed_at.is_(None),
            exists().where(VoiceProfileClip.source_replay_id == RoundReplay.id),
        ).first()
        if candidates is not None and not nd_model_ready:
            result = {"status": "awaiting_voiceprint_model"}
        elif candidates is not None and not capable_voiceprint:
            result = {"status": "no_online_voiceprint_worker"}
        elif voiceprints_queued:
            result = {"status": "voiceprint_queued", "voiceprints_queued": voiceprints_queued}
        elif enqueued:
            result = {"status": "waiting_for_external_worker"}
        else:
            result = {"status": "idle"}
    return {**recovery, "imported": imported, "enqueued": enqueued,
            "voiceprints_queued": voiceprints_queued, **result}
