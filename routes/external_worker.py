"""Admin and narrow worker-to-NexusData API for the durable external queue."""
import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import AdminUser, ExternalWorkerJob, ExternalWorkerLog, ExternalWorkerNode, RoundReplay
from routes.admin import get_current_admin

router = APIRouter()
LEASE_SECONDS = 180


def _admin(admin: AdminUser):
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Требуются права суперпользователя")


def _worker(request: Request, db: Session, allow_disabled: bool = False) -> ExternalWorkerNode:
    authorization = request.headers.get("authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
    node = db.query(ExternalWorkerNode).filter(ExternalWorkerNode.token_hash == digest).first()
    if node is None or (not node.enabled and not allow_disabled):
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return node


def _log(db, node_id: int, message: str, level: str = "info", job_id: int | None = None):
    db.add(ExternalWorkerLog(worker_id=node_id, job_id=job_id, level=level[:12], message=message[:2000]))
    db.flush()
    cutoff = db.query(ExternalWorkerLog.id).order_by(ExternalWorkerLog.id.desc()).offset(1000).limit(1).scalar()
    if cutoff:
        db.query(ExternalWorkerLog).filter(ExternalWorkerLog.id < cutoff).delete(synchronize_session=False)


@router.get("/api/v1/admin/external-workers")
def admin_workers(db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)):
    _admin(admin)
    now = datetime.utcnow()
    nodes = db.query(ExternalWorkerNode).order_by(ExternalWorkerNode.name).all()
    jobs = db.query(ExternalWorkerJob).order_by(ExternalWorkerJob.created_at.desc()).limit(30).all()
    logs = db.query(ExternalWorkerLog).order_by(ExternalWorkerLog.id.desc()).limit(100).all()
    names = {row.id: row.name for row in nodes}
    for node in nodes:
        if node.last_seen_at and now - node.last_seen_at > timedelta(seconds=60) and node.status not in {"disabled", "stopping"}:
            node.status = "offline"
    db.commit()
    return {
        "nodes": [{"id": n.id, "name": n.name, "enabled": n.enabled, "status": n.status,
                   "current_job_id": n.current_job_id, "capabilities": n.capabilities or [],
                   "version": n.version, "last_seen_at": n.last_seen_at.isoformat() if n.last_seen_at else None,
                   "pending_command": n.pending_command} for n in nodes],
        "jobs": [{"id": j.id, "task_type": j.task_type, "status": j.status,
                  "worker": names.get(j.worker_id), "attempts": j.attempts,
                  "created_at": j.created_at.isoformat() if j.created_at else None,
                  "error": j.error} for j in jobs],
        "logs": [{"id": l.id, "worker": names.get(l.worker_id), "job_id": l.job_id,
                  "level": l.level, "message": l.message,
                  "created_at": l.created_at.isoformat() if l.created_at else None} for l in logs],
    }


@router.post("/api/v1/admin/external-workers/register")
def register_worker(body: dict, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)):
    _admin(admin)
    name = str(body.get("name") or "").strip()[:120]
    if not name:
        raise HTTPException(status_code=400, detail="Укажите имя воркера")
    if db.query(ExternalWorkerNode).filter(ExternalWorkerNode.name == name).first():
        raise HTTPException(status_code=409, detail="Такой воркер уже зарегистрирован")
    token = secrets.token_urlsafe(48)
    node = ExternalWorkerNode(name=name, token_hash=hashlib.sha256(token.encode()).hexdigest())
    db.add(node)
    db.flush()
    _log(db, node.id, "Воркер зарегистрирован")
    db.commit()
    return {"id": node.id, "name": name, "token": token}


@router.post("/api/v1/admin/external-workers/jobs")
def enqueue_external_job(body: dict, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)):
    _admin(admin)
    task_type = str(body.get("task_type") or "").strip()[:100]
    payload = body.get("payload") or {}
    if not task_type or not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Укажите тип задания и JSON-объект payload")
    if len(str(payload)) > 16000:
        raise HTTPException(status_code=413, detail="Слишком большой payload")
    job = ExternalWorkerJob(task_type=task_type, payload=payload,
                            dedupe_key=(str(body["dedupe_key"])[:160] if body.get("dedupe_key") else None))
    db.add(job)
    db.commit()
    return {"id": job.id, "task_type": job.task_type, "status": job.status}


@router.post("/api/v1/admin/external-workers/{worker_id}/rotate-token")
def rotate_worker_token(worker_id: int, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)):
    _admin(admin)
    node = db.query(ExternalWorkerNode).filter(ExternalWorkerNode.id == worker_id).first()
    if node is None:
        raise HTTPException(status_code=404, detail="Воркер не найден")
    token = secrets.token_urlsafe(48)
    node.token_hash = hashlib.sha256(token.encode()).hexdigest()
    _log(db, node.id, "Токен подключения заменён", "warning")
    db.commit()
    return {"name": node.name, "token": token}


@router.post("/api/v1/admin/external-workers/jobs/{job_id}/retry")
def retry_external_job(job_id: int, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)):
    _admin(admin)
    job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Повторить можно только завершённое с ошибкой или отменённое задание")
    job.status = "queued"
    job.attempts = 0
    job.error = None
    job.result = None
    if job.result_artifact_path:
        Path(job.result_artifact_path).unlink(missing_ok=True)
    job.result_artifact_path = None
    job.finished_at = None
    job.worker_id = None
    replay_id = int((job.payload or {}).get("replay_id") or 0)
    if replay_id:
        replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if replay:
            replay.voice_profile_state = "pending"
            replay.voice_profile_error = None
    db.commit()
    return {"ok": True, "status": job.status}


@router.post("/api/v1/admin/external-workers/{worker_id}/command")
def worker_command(worker_id: int, body: dict, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)):
    _admin(admin)
    command = str(body.get("command") or "")
    if command not in {"restart", "stop", "resume", "stop_task"}:
        raise HTTPException(status_code=400, detail="Неизвестная команда")
    node = db.query(ExternalWorkerNode).filter(ExternalWorkerNode.id == worker_id).first()
    if node is None:
        raise HTTPException(status_code=404, detail="Воркер не найден")
    if command == "stop":
        node.enabled = False
        node.pending_command = command
        node.status = "stopping"
    elif command == "resume":
        node.enabled = True
        node.pending_command = command
    else:
        node.pending_command = command
    _log(db, node.id, f"Команда: {command}", "warning")
    db.commit()
    return {"ok": True, "command": command}


@router.post("/api/v1/external-worker/v1/heartbeat")
async def heartbeat(request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db, allow_disabled=True)
    body = await request.json()
    node.status = str(body.get("status") or "idle")[:24] if node.enabled else "paused"
    node.current_job_id = body.get("current_job_id")
    node.capabilities = [str(x)[:100] for x in body.get("capabilities", [])[:100]]
    node.version = str(body.get("version") or "")[:80] or None
    node.last_seen_at = datetime.utcnow()
    command = node.pending_command
    node.pending_command = None
    enabled = node.enabled
    db.commit()
    return {"command": command, "enabled": enabled, "lease_seconds": LEASE_SECONDS}


@router.post("/api/v1/external-worker/v1/claim")
async def claim_job(request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db)
    body = await request.json()
    supported = [str(x)[:100] for x in body.get("capabilities", [])[:100]]
    now = datetime.utcnow()
    if not node.enabled:
        node.status = "paused"
        node.last_seen_at = now
        db.commit()
        return {"job": None}
    from services_voice_profiles import voice_profile_scanner_enabled, voiceprint_worker_enabled
    if not voice_profile_scanner_enabled(db):
        supported = [task for task in supported if not task.startswith("voice_profile.")]
    elif not voiceprint_worker_enabled(db):
        supported = [task for task in supported if task != "voice_profile.voiceprint"]
    if not supported:
        node.status = "idle"
        node.current_job_id = None
        db.commit()
        return {"job": None}
    expired = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.status == "running",
                                                  ExternalWorkerJob.lease_until < now).all()
    for job in expired:
        job.worker_id = None
        job.lease_until = None
        job.status = "queued" if job.attempts < job.max_attempts else "failed"
        job.error = "Lease expired; worker stopped responding"
        if job.status == "failed":
            job.finished_at = now
            replay_id = int((job.payload or {}).get("replay_id") or 0)
            if replay_id:
                replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
                if replay:
                    replay.voice_profile_state = "failed"
                    replay.voice_profile_error = job.error
        _log(db, node.id, f"Job {job.id}: lease expired", "warning", job.id)
    query = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.status == "queued")
    query = query.filter(ExternalWorkerJob.task_type.in_(supported))
    job = query.order_by(ExternalWorkerJob.created_at, ExternalWorkerJob.id).with_for_update(skip_locked=True).first()
    node.last_seen_at = now
    if job is None:
        node.status = "idle"
        node.current_job_id = None
        db.commit()
        return {"job": None}
    job.status = "running"
    job.worker_id = node.id
    job.attempts += 1
    job.heartbeat_at = now
    job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
    node.status = "busy"
    node.current_job_id = job.id
    db.commit()
    return {"job": {"id": job.id, "task_type": job.task_type, "payload": job.payload,
                     "attempt": job.attempts, "lease_seconds": LEASE_SECONDS}}


@router.get("/api/v1/external-worker/v1/jobs/{job_id}/input")
def job_input(job_id: int, request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db)
    job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job_id, ExternalWorkerJob.worker_id == node.id,
                                              ExternalWorkerJob.status == "running").first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job unavailable")
    replay_id = (job.payload or {}).get("replay_id")
    if not replay_id:
        return {"payload": job.payload}
    replay = db.query(RoundReplay).filter(RoundReplay.id == int(replay_id)).first()
    if replay is None:
        raise HTTPException(status_code=404, detail="Replay not found")
    from services_replays import ReplayService
    path = Path(ReplayService(db).get_file_path(replay))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Replay file unavailable")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream",
                        headers={"X-NexusData-Replay-Id": str(replay.id)})


@router.post("/api/v1/external-worker/v1/jobs/{job_id}/heartbeat")
def job_heartbeat(job_id: int, request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db)
    job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job_id, ExternalWorkerJob.worker_id == node.id,
                                              ExternalWorkerJob.status == "running").first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job unavailable")
    job.heartbeat_at = datetime.utcnow()
    job.lease_until = datetime.utcnow() + timedelta(seconds=LEASE_SECONDS)
    replay_id = int((job.payload or {}).get("replay_id") or 0)
    if replay_id and job.task_type in {"voice_profile.extract", "voice_profile.voiceprint"}:
        replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if replay and replay.voice_profile_state == "processing":
            replay.voice_profile_heartbeat_at = datetime.utcnow()
            replay.voice_profile_progress = {"mode": "external_worker", "phase": "processing", "job_id": job.id}
    db.commit()
    return {"lease_seconds": LEASE_SECONDS}


@router.get("/api/v1/external-worker/v1/jobs/{job_id}/voiceprint-model")
def voiceprint_model_for_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db)
    job = db.query(ExternalWorkerJob).filter(
        ExternalWorkerJob.id == job_id,
        ExternalWorkerJob.worker_id == node.id,
        ExternalWorkerJob.task_type == "voice_profile.voiceprint",
        ExternalWorkerJob.status == "running",
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Voiceprint model is only available to its assigned job")
    from services_voice_profiles import voiceprint_model_status
    model = voiceprint_model_status()
    if model.get("status") != "ready":
        raise HTTPException(status_code=503, detail="Voiceprint model is unavailable on NexusData")
    return FileResponse(model["path"], filename="redimnet_b2_vox2.onnx", media_type="application/octet-stream")


@router.post("/api/v1/external-worker/v1/jobs/{job_id}/complete")
async def complete_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db)
    job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job_id, ExternalWorkerJob.worker_id == node.id).first()
    if job is not None and job.status == "completed":
        return {"ok": True, "already_completed": True}
    if job is None or job.status != "running":
        raise HTTPException(status_code=404, detail="Job unavailable")
    result = await request.json()
    if not isinstance(result, dict):
        raise HTTPException(status_code=400, detail="Result must be a JSON object")
    job.result = result
    job.status = "completed"
    job.lease_until = None
    job.finished_at = datetime.utcnow()
    node.current_job_id = None
    node.status = "idle"
    _log(db, node.id, f"Job {job.id} completed", job_id=job.id)
    db.commit()
    return {"ok": True}


@router.put("/api/v1/external-worker/v1/jobs/{job_id}/artifact")
async def upload_artifact(job_id: int, request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db)
    job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job_id, ExternalWorkerJob.worker_id == node.id).first()
    if job is not None and job.status == "completed" and job.result_artifact_path:
        return {"ok": True, "already_uploaded": True}
    if job is None or job.status != "running":
        raise HTTPException(status_code=404, detail="Job unavailable")
    from services_replays import get_upload_dir
    import os
    target_dir = Path(get_upload_dir(db)) / "external_worker" / "results"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{job.id}.zip"
    temporary = target.with_suffix(".zip.part")
    size = 0
    with temporary.open("wb") as output:
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2 * 1024 * 1024 * 1024:
                output.close()
                temporary.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Worker artifact too large")
            output.write(chunk)
    if size == 0:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Empty artifact")
    os.replace(temporary, target)
    job.result_artifact_path = str(target)
    job.heartbeat_at = datetime.utcnow()
    job.lease_until = datetime.utcnow() + timedelta(seconds=LEASE_SECONDS)
    db.commit()
    return {"ok": True, "size": size}


@router.post("/api/v1/external-worker/v1/jobs/{job_id}/fail")
async def fail_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db, allow_disabled=True)
    job = db.query(ExternalWorkerJob).filter(ExternalWorkerJob.id == job_id, ExternalWorkerJob.worker_id == node.id,
                                              ExternalWorkerJob.status == "running").first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job unavailable")
    body = await request.json()
    job.error = str(body.get("error") or "Worker reported failure")[:2000]
    cancelled = job.error == "Job cancelled"
    job.status = "cancelled" if cancelled else "queued" if job.attempts < job.max_attempts else "failed"
    job.worker_id = None
    job.lease_until = None
    if job.status == "failed":
        job.finished_at = datetime.utcnow()
    replay_id = int((job.payload or {}).get("replay_id") or 0)
    if replay_id and job.status in {"failed", "cancelled"}:
        replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if replay:
            replay.voice_profile_state = "failed"
            replay.voice_profile_started_at = None
            replay.voice_profile_heartbeat_at = None
            replay.voice_profile_error = "Отменено администратором" if cancelled else job.error
    node.current_job_id = None
    node.status = "idle"
    _log(db, node.id, f"Job {job.id} failed: {job.error}", "error", job.id)
    db.commit()
    return {"ok": True, "status": job.status}


@router.post("/api/v1/external-worker/v1/log")
async def worker_log(request: Request, db: Session = Depends(get_db)):
    node = _worker(request, db)
    body = await request.json()
    _log(db, node.id, str(body.get("message") or ""), str(body.get("level") or "info"), body.get("job_id"))
    node.last_seen_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
