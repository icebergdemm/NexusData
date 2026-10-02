"""Resilient outbound-only worker. Run with: python -m external_worker.runner"""
import json
import logging
import importlib
import hashlib
import os
import pkgutil
from logging.handlers import RotatingFileHandler
import shutil
import signal
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from external_worker import plugins

VERSION = "1.0.0"
HANDLERS = {}
for _plugin_info in pkgutil.iter_modules(plugins.__path__):
    _plugin = importlib.import_module(f"external_worker.plugins.{_plugin_info.name}")
    if getattr(_plugin, "available", lambda: True)():
        HANDLERS.update(getattr(_plugin, "HANDLERS", {}))
CAPABILITIES = set(HANDLERS)
ROOT = Path(os.environ.get("NEXUSDATA_WORKER_DATA", "./worker-data")).resolve()
SPOOL = ROOT / "spool"
LOG = ROOT / "worker.log"
DB = ROOT / "worker.sqlite3"
STOP = threading.Event()
TASK_STOP = threading.Event()
RESTART_REQUESTED = threading.Event()
CURRENT_JOB = None
COMMAND = None
WORK_ENABLED = True
LAST_NETWORK_ERROR = 0.0
HTTP: httpx.Client | None = None
BASE_URL = os.environ.get("NEXUSDATA_URL", "").rstrip("/")
TOKEN = os.environ.get("NEXUSDATA_WORKER_TOKEN", "")
NAME = os.environ.get("NEXUSDATA_WORKER_NAME", "voice-worker")

ROOT.mkdir(parents=True, exist_ok=True)
SPOOL.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[RotatingFileHandler(LOG, maxBytes=5 * 1024 * 1024, backupCount=4, encoding="utf-8"),
                              logging.StreamHandler()])
logger = logging.getLogger("nexusdata.worker")


def _init_spool():
    with sqlite3.connect(DB) as db:
        db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP, level TEXT, message TEXT, sent INTEGER NOT NULL DEFAULT 0, job_id INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS pending_results (job_id INTEGER PRIMARY KEY, metadata TEXT NOT NULL, artifact TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
        if "sent" not in columns:
            db.execute("ALTER TABLE events ADD COLUMN sent INTEGER NOT NULL DEFAULT 0")
        if "job_id" not in columns:
            db.execute("ALTER TABLE events ADD COLUMN job_id INTEGER")


def _event(level: str, message: str):
    getattr(logger, level if level in {"info", "warning", "error"} else "info")(message)
    with sqlite3.connect(DB) as db:
        db.execute("INSERT INTO events(level,message,job_id) VALUES (?,?,?)", (level, message[:1900], CURRENT_JOB))
        db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)")
    _flush_events()


def _flush_events():
    if HTTP is None:
        return
    with sqlite3.connect(DB) as db:
        rows = db.execute("SELECT id,at,level,message,job_id FROM events WHERE sent=0 ORDER BY id LIMIT 20").fetchall()
    for event_id, at, level, message, job_id in rows:
        try:
            response = HTTP.post("/api/v1/external-worker/v1/log", json={
                "level": level, "message": f"{at} {message}", "job_id": job_id,
            }, timeout=8)
            response.raise_for_status()
            with sqlite3.connect(DB) as db:
                db.execute("UPDATE events SET sent=1 WHERE id=?", (event_id,))
        except Exception:
            return


def _network_error(message: str):
    global LAST_NETWORK_ERROR
    now = time.monotonic()
    if now - LAST_NETWORK_ERROR >= 60:
        LAST_NETWORK_ERROR = now
        _event("warning", message[:1800])


def _heartbeat_loop():
    global COMMAND, WORK_ENABLED
    while not STOP.wait(10):
        try:
            response = HTTP.post("/api/v1/external-worker/v1/heartbeat", json={
                "status": "busy" if CURRENT_JOB else "idle", "current_job_id": CURRENT_JOB,
                "capabilities": sorted(CAPABILITIES), "version": VERSION,
            }, timeout=15)
            response.raise_for_status()
            data = response.json()
            COMMAND = data.get("command")
            WORK_ENABLED = bool(data.get("enabled", True))
            if COMMAND == "restart":
                RESTART_REQUESTED.set()
            if COMMAND in {"stop", "stop_task"}:
                TASK_STOP.set()
            if COMMAND == "resume":
                TASK_STOP.clear()
        except Exception as exc:
            _network_error(f"NexusData heartbeat unavailable: {exc}")
        if CURRENT_JOB is not None:
            try:
                HTTP.post(f"/api/v1/external-worker/v1/jobs/{CURRENT_JOB}/heartbeat", timeout=8)
            except Exception:
                pass


def _save_result(job_id: int, metadata: dict, artifact: Path | None):
    target = SPOOL / f"{job_id}.zip" if artifact else None
    if artifact and target:
        temporary = target.with_suffix(".zip.part")
        shutil.copyfile(artifact, temporary)
        os.replace(temporary, target)
    with sqlite3.connect(DB) as db:
        db.execute("INSERT OR REPLACE INTO pending_results(job_id,metadata,artifact) VALUES (?,?,?)",
                   (job_id, json.dumps(metadata, separators=(",", ":")), str(target) if target else None))


def _flush_results():
    global CURRENT_JOB
    with sqlite3.connect(DB) as db:
        rows = db.execute("SELECT job_id,metadata,artifact FROM pending_results ORDER BY created_at").fetchall()
    for job_id, metadata, artifact in rows:
        if artifact and Path(artifact).is_file():
            with Path(artifact).open("rb") as stream:
                response = HTTP.put(f"/api/v1/external-worker/v1/jobs/{job_id}/artifact", content=stream,
                                    headers={"Content-Type": "application/zip"}, timeout=None)
                response.raise_for_status()
        response = HTTP.post(f"/api/v1/external-worker/v1/jobs/{job_id}/complete", json=json.loads(metadata), timeout=60)
        response.raise_for_status()
        if artifact:
            Path(artifact).unlink(missing_ok=True)
        with sqlite3.connect(DB) as db:
            db.execute("DELETE FROM pending_results WHERE job_id=?", (job_id,))
        if CURRENT_JOB == job_id:
            CURRENT_JOB = None
        _event("info", f"Job {job_id} result acknowledged by NexusData")


def _run_job(job: dict):
    global CURRENT_JOB
    job_id = int(job["id"])
    CURRENT_JOB = job_id
    TASK_STOP.clear()
    work = Path(tempfile.mkdtemp(prefix=f"job-{job_id}-", dir=ROOT))
    try:
        input_path = work / "input.nwdemo"
        with HTTP.stream("GET", f"/api/v1/external-worker/v1/jobs/{job_id}/input", timeout=None) as stream:
            stream.raise_for_status()
            if "application/json" in stream.headers.get("content-type", ""):
                input_path.write_text(json.dumps(stream.json()), encoding="utf-8")
            else:
                with input_path.open("wb") as output_file:
                    for chunk in stream.iter_bytes(1024 * 1024):
                        if TASK_STOP.is_set():
                            raise RuntimeError("Job cancelled")
                        output_file.write(chunk)
        output = work / "output"
        output.mkdir()
        if job["task_type"] not in CAPABILITIES:
            raise RuntimeError(f"Unsupported task type: {job['task_type']}")
        handler = HANDLERS[job["task_type"]]
        previous_model_path = os.environ.get("NEXUSDATA_JOB_MODEL_PATH")
        if job["task_type"] == "voice_profile.voiceprint":
            from services_voice_profiles import ONNX_VOICEPRINT_MODEL_SHA256
            model_path = SPOOL / "voiceprint-model.onnx"
            def valid_model(path: Path) -> bool:
                if not path.is_file():
                    return False
                digest = hashlib.sha256()
                with path.open("rb") as model_file:
                    for block in iter(lambda: model_file.read(1024 * 1024), b""):
                        digest.update(block)
                return digest.hexdigest() == ONNX_VOICEPRINT_MODEL_SHA256
            if not valid_model(model_path):
                temporary_model = model_path.with_suffix(".onnx.part")
                with HTTP.stream("GET", f"/api/v1/external-worker/v1/jobs/{job_id}/voiceprint-model", timeout=None) as stream:
                    stream.raise_for_status()
                    with temporary_model.open("wb") as output_file:
                        for chunk in stream.iter_bytes(1024 * 1024):
                            if TASK_STOP.is_set():
                                raise RuntimeError("Job cancelled")
                            output_file.write(chunk)
                if not valid_model(temporary_model):
                    temporary_model.unlink(missing_ok=True)
                    raise RuntimeError("NexusData returned an invalid voiceprint model")
                os.replace(temporary_model, model_path)
            os.environ["NEXUSDATA_JOB_MODEL_PATH"] = str(model_path)
        try:
            metadata, artifact = handler(job, input_path, output, TASK_STOP, lambda value: None)
        finally:
            if previous_model_path is None:
                os.environ.pop("NEXUSDATA_JOB_MODEL_PATH", None)
            else:
                os.environ["NEXUSDATA_JOB_MODEL_PATH"] = previous_model_path
        _save_result(job_id, metadata, artifact)
        try:
            _flush_events()
            _flush_results()
        except Exception as exc:
            _event("warning", f"Job {job_id} result is spooled; NexusData acknowledgement will be retried: {exc}")
    except Exception as exc:
        _event("error", f"Job {job_id} failed: {exc}")
        try:
            HTTP.post(f"/api/v1/external-worker/v1/jobs/{job_id}/fail", json={"error": str(exc)[:2000]}, timeout=15)
        except Exception:
            # The lease will expire and NexusData will retry after restart/outage.
            pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with sqlite3.connect(DB) as db:
            pending = db.execute("SELECT 1 FROM pending_results WHERE job_id=?", (job_id,)).fetchone()
        if pending is None:
            CURRENT_JOB = None
        TASK_STOP.clear()


def _restart():
    os.execv(sys.executable, [sys.executable, *sys.argv])


def main():
    global HTTP
    if not BASE_URL or not TOKEN:
        raise SystemExit("Set NEXUSDATA_URL and NEXUSDATA_WORKER_TOKEN")
    parsed_url = urlparse(BASE_URL)
    if parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("NEXUSDATA_URL must use HTTPS outside localhost")
    _init_spool()
    HTTP = httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"},
                        timeout=httpx.Timeout(30, read=180), follow_redirects=False)
    def request_shutdown(*_):
        TASK_STOP.set()
        STOP.set()
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    thread = threading.Thread(target=_heartbeat_loop, name="worker-heartbeat", daemon=True)
    thread.start()
    _event("info", f"Worker {NAME} started, capabilities={','.join(sorted(CAPABILITIES))}")
    while not STOP.is_set():
        try:
            _flush_results()
            if RESTART_REQUESTED.is_set() and CURRENT_JOB is None:
                _event("warning", "Restart command received")
                _restart()
            if not WORK_ENABLED:
                STOP.wait(2)
                continue
            response = HTTP.post("/api/v1/external-worker/v1/claim", json={"capabilities": sorted(CAPABILITIES)}, timeout=20)
            response.raise_for_status()
            job = response.json().get("job")
            if job:
                _run_job(job)
            else:
                STOP.wait(3)
            if RESTART_REQUESTED.is_set() and CURRENT_JOB is None:
                _event("warning", "Restart command received")
                _restart()
        except Exception as exc:
            _network_error(f"NexusData connection failed: {exc}")
            STOP.wait(5)
    _event("warning", "Worker stopped")
    HTTP.close()


if __name__ == "__main__":
    main()
