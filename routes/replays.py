"""
Round replays routes:
- API: HTTP upload of .barodemo / .barodemo.zip from game servers (API key).
- Admin panel: list, player page, download, delete, settings.

The download endpoint streams the COMPRESSED archive directly to the browser,
which unpacks and parses it client-side (minimal server load).
"""

import os
import secrets
import logging
from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter, Depends, HTTPException, Request, Query, Form, UploadFile, File,
)
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from security import verify_external_api_key_dependency, check_permission
from config import get_settings
from models import AdminUser, AdminSession
import services_replays
from services_replays import ReplayService

settings = get_settings()
logger = logging.getLogger(__name__)

# ── API router (game server → NexusData, API key protected) ───────────────────
api_router = APIRouter(
    prefix="/api/v1/replays",
    tags=["replays"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# ── Admin panel router (session protected) ────────────────────────────────────
panel_router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)


def _fmt_size(n: Optional[int]) -> str:
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


templates.env.globals.update(fmt_size=_fmt_size)


def _get_admin(request: Request, db: Session) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


async def _ensure_csrf(request: Request, db: Session):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    session_csrf = request.session.get("csrf_token")
    if csrf_token and session_csrf and secrets.compare_digest(str(csrf_token), str(session_csrf)):
        return
    token = request.cookies.get("admin_token")
    if token:
        now = datetime.utcnow()
        s = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if s and (s.expires_at is None or s.expires_at > now):
            return
    raise HTTPException(status_code=403, detail="Invalid CSRF token")


# ══════════════════════════════════════════════════════════════════════════════
# API: HTTP upload from game server
# ══════════════════════════════════════════════════════════════════════════════

@api_router.post("/upload")
async def upload_replay(
    request: Request,
    file: Optional[UploadFile] = File(None, description=".barodemo или .barodemo.zip"),
    server_name: Optional[str] = Query(None, description="Имя сервера-отправителя"),
    notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Upload a round replay file.

    Accepts both:
    - multipart/form-data with field "file"
    - raw binary body (application/octet-stream) for simpler game server clients

    Streams to disk in chunks — does NOT load the whole file into RAM.
    """
    svc = ReplayService(db)

    # Determine upload source: multipart file or raw body
    content_type = request.headers.get("content-type", "")

    if file and file.filename:
        # Standard multipart upload
        logger.info(
            "Replay upload (multipart): filename=%s, content_type=%s, server=%s",
            file.filename, file.content_type, server_name,
        )
        upload_source = file
    elif "multipart" not in content_type:
        # Raw binary body (application/octet-stream or similar)
        logger.info(
            "Replay upload (raw body): content_type=%s, server=%s, content_length=%s",
            content_type, server_name, request.headers.get("content-length", "?"),
        )
        # Wrap raw body as a file-like object
        from io import BytesIO
        body = await request.body()
        if not body:
            return JSONResponse(status_code=400, content={"success": False, "error": "Пустое тело запроса"})

        class _FakeUpload:
            def __init__(self, data, fname):
                self._data = data
                self._pos = 0
                self.filename = fname
                self.content_type = content_type

            async def read(self, size=-1):
                if size < 0:
                    chunk = self._data[self._pos:]
                    self._pos = len(self._data)
                else:
                    chunk = self._data[self._pos:self._pos + size]
                    self._pos += len(chunk)
                return chunk

        # Try to get filename from Content-Disposition header or query
        disp = request.headers.get("content-disposition", "")
        fname = "upload.barodemo"
        if 'filename="' in disp:
            fname = disp.split('filename="')[1].split('"')[0]
        elif 'filename=' in disp:
            fname = disp.split('filename=')[1].split(';')[0].strip()

        upload_source = _FakeUpload(body, fname)
    else:
        # Multipart but no file field — log and reject
        logger.warning(
            "Replay upload: multipart request but no 'file' field. Headers: %s",
            dict(request.headers),
        )
        return JSONResponse(status_code=400, content={"success": False, "error": "Поле 'file' не найдено в multipart запросе"})

    try:
        replay = await svc.save_upload_stream(upload_source, server_name=server_name, notes=notes)
    except HTTPException as he:
        db.rollback()
        logger.warning("Replay upload rejected: %s (filename=%s)", he.detail, getattr(upload_source, "filename", None))
        return JSONResponse(status_code=he.status_code, content={"success": False, "error": str(he.detail)})
    except OSError as oe:
        logger.exception("Replay upload OS error (disk full / permissions?): %s", oe)
        db.rollback()
        return JSONResponse(status_code=507, content={"success": False, "error": f"Ошибка диска: {oe}"})
    except Exception as e:
        logger.exception("Replay upload failed")
        db.rollback()
        return JSONResponse(status_code=500, content={"success": False, "error": f"Ошибка сохранения реплея: {e}"})

    return {
        "success": True,
        "status": "ok",
        "replay_id": replay.id,
        "stored_filename": replay.stored_filename,
        "file_size": replay.file_size,
        "has_voice": bool(replay.has_voice),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Admin panel: list page
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.get("/admin/replays", response_class=HTMLResponse)
async def replays_list(
    request: Request,
    page: int = Query(1, ge=1),
    server: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = ReplayService(db)
    page_size = 30
    rows, total = svc.get_all(limit=page_size, offset=(page - 1) * page_size, server_name=server)
    total_pages = (total + page_size - 1) // page_size if total else 1

    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)

    retention_days = services_replays.get_retention_days(db)
    upload_dir = services_replays.get_upload_dir(db)

    return templates.TemplateResponse("admin/replays.html", {
        "request": request,
        "admin": admin,
        "replays": rows,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "selected_server": server,
        "retention_days": retention_days,
        "upload_dir": upload_dir,
        "flashes": flashes,
    })


@panel_router.get("/admin/replays/{replay_id}/view", response_class=HTMLResponse)
async def replay_player_page(request: Request, replay_id: int, db: Session = Depends(get_db)):
    """Web player page for a single replay."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    replay = svc.get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")

    return templates.TemplateResponse("admin/replay_player.html", {
        "request": request,
        "admin": admin,
        "replay": replay,
    })


# ══════════════════════════════════════════════════════════════════════════════
# JSON API (for the React SPA)
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_replay(r) -> dict:
    return {
        "id": r.id, "server_name": r.server_name, "submarine_name": r.submarine_name,
        "level_name": r.level_name, "filename": r.filename, "file_size": r.file_size,
        "duration_ms": r.duration_ms, "has_voice": bool(r.has_voice),
        "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        "notes": r.notes,
    }


@panel_router.get("/api/v1/admin/replays")
async def api_replays_list(
    request: Request, page: int = Query(1, ge=1), server: Optional[str] = Query(None), db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    page_size = 30
    rows, total = svc.get_all(limit=page_size, offset=(page - 1) * page_size, server_name=server)
    total_pages = (total + page_size - 1) // page_size if total else 1

    return {
        "replays": [_serialize_replay(r) for r in rows],
        "total": total, "page": page, "total_pages": total_pages, "selected_server": server,
        "retention_days": services_replays.get_retention_days(db),
        "upload_dir": services_replays.get_upload_dir(db),
        "can_delete": check_permission(admin, "replays", "delete"),
        "is_superuser": admin.is_superuser,
    }


@panel_router.get("/api/v1/admin/replays/{replay_id}")
async def api_replay_detail(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    replay = svc.get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")
    return {"replay": _serialize_replay(replay)}


@panel_router.delete("/api/v1/admin/replays/{replay_id}")
async def api_replay_delete(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    if not svc.delete(replay_id):
        raise HTTPException(status_code=404, detail="Реплей не найден")

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_delete", {"replay_id": replay_id})
    return {"status": "ok"}


@panel_router.post("/api/v1/admin/replays/upload")
async def api_replay_manual_upload(
    request: Request, file: UploadFile = File(...), server_name: Optional[str] = Form(None),
    notes: Optional[str] = Form(None), db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может загружать реплеи вручную")

    svc = ReplayService(db)
    replay = await svc.save_upload_stream(file, server_name=(server_name or None), notes=(notes or "Загружен вручную через сайт"))
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_manual_upload", {"replay_id": replay.id})
    return {"status": "ok", "replay": _serialize_replay(replay)}


@panel_router.post("/api/v1/admin/replays/settings")
async def api_replay_settings(
    request: Request, db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может менять настройки")

    body = await request.json()
    retention_days = max(1, int(body.get("retention_days", 1)))
    upload_dir = (body.get("upload_dir") or "").strip()

    services_replays.set_setting(db, services_replays.SETTING_RETENTION_DAYS, str(retention_days))
    if upload_dir:
        services_replays.set_setting(db, services_replays.SETTING_UPLOAD_DIR, upload_dir)
        os.makedirs(upload_dir, exist_ok=True)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_settings_update", {"retention_days": retention_days, "upload_dir": upload_dir})
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# Download (streams compressed archive to browser)
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.get("/admin/replays/{replay_id}/download")
async def replay_download(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    replay = svc.get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")

    path = svc.get_file_path(replay)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    # Stream the compressed archive directly. The browser unpacks it client-side.
    return FileResponse(
        path=path,
        filename=replay.stored_filename,
        media_type="application/zip",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Delete
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.post("/admin/replays/{replay_id}/delete")
async def replay_delete(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await _ensure_csrf(request, db)

    svc = ReplayService(db)
    if not svc.delete(replay_id):
        raise HTTPException(status_code=404, detail="Реплей не найден")

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_delete", {"replay_id": replay_id})

    flashes = request.session.get("_flashes", [])
    flashes.append({"level": "success", "message": "Реплей удалён"})
    request.session["_flashes"] = flashes
    return RedirectResponse(url="/admin/replays", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# Manual upload via website (superuser only)
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.post("/admin/replays/upload")
async def replay_manual_upload(
    request: Request,
    file: UploadFile = File(...),
    server_name: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Manually upload a replay file through the website. Superuser only."""
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может загружать реплеи вручную")
    await _ensure_csrf(request, db)

    svc = ReplayService(db)
    try:
        replay = await svc.save_upload_stream(
            file,
            server_name=(server_name or None),
            notes=(notes or "Загружен вручную через сайт"),
        )
        from routes.admin import log_admin_action
        log_admin_action(db, admin.id, "replay_manual_upload", {"replay_id": replay.id})
        flashes = request.session.get("_flashes", [])
        flashes.append({"level": "success", "message": f"Реплей #{replay.id} загружен"})
        request.session["_flashes"] = flashes
    except HTTPException as e:
        flashes = request.session.get("_flashes", [])
        flashes.append({"level": "error", "message": e.detail})
        request.session["_flashes"] = flashes

    return RedirectResponse(url="/admin/replays", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# Settings (superuser only): retention days + storage dir
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.post("/admin/replays/settings")
async def replay_settings(
    request: Request,
    retention_days: int = Form(...),
    upload_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может менять настройки")
    await _ensure_csrf(request, db)

    if retention_days < 1:
        retention_days = 1
    services_replays.set_setting(db, services_replays.SETTING_RETENTION_DAYS, str(retention_days))
    if upload_dir.strip():
        services_replays.set_setting(db, services_replays.SETTING_UPLOAD_DIR, upload_dir.strip())
        os.makedirs(upload_dir.strip(), exist_ok=True)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_settings_update", {
        "retention_days": retention_days, "upload_dir": upload_dir.strip(),
    })

    flashes = request.session.get("_flashes", [])
    flashes.append({"level": "success", "message": "Настройки реплеев сохранены"})
    request.session["_flashes"] = flashes
    return RedirectResponse(url="/admin/replays", status_code=302)
