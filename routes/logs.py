"""
Server Logs routes - beautiful log viewer with filtering and search.
Also includes SFTP file manager routes for sub-projects.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import secrets
import logging
import json

from database import get_db
from security import check_permission
from config import get_settings
from models import AdminUser, AdminSession
from services_logs import (
    search_logs, get_unique_players, get_log_stats,
    force_refresh_logs, LogEvent, get_player_map,
)
from services_sftp_filemanager import SFTPFileManager

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)


# ── Reusable helpers ─────────────────────────────────────────────────────────

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
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
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def push_flash(request: Request, level: str, message: str):
    flashes = request.session.get("_flashes", [])
    flashes.append({"level": level, "message": message})
    request.session["_flashes"] = flashes


def pop_flashes(request: Request):
    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)
    return flashes


# ══════════════════════════════════════════════════════════════════════════════
# SERVER LOGS API (JSON endpoints for the log viewer)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/logs/events")
async def api_logs_events(
    request: Request,
    server: Optional[str] = Query(None),
    player: Optional[str] = Query(None),
    player_names: Optional[str] = Query(None, description="Comma-separated list of player names to search"),
    action_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None, description="player or technical"),
    search: Optional[str] = Query(None),
    time_from: Optional[str] = Query(None),
    time_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=10, le=500),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get filtered log events as JSON."""
    if not check_permission(current_admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    # Parse time filters
    dt_from = None
    dt_to = None
    if time_from:
        try:
            dt_from = datetime.fromisoformat(time_from)
        except ValueError:
            pass
    if time_to:
        try:
            dt_to = datetime.fromisoformat(time_to)
        except ValueError:
            pass

    # Category: player = events with known player actions, technical = system/noise
    PLAYER_ACTIONS = {'picked_up', 'placed', 'removed', 'opened', 'closed', 'equipped',
                      'unequipped', 'dropped', 'reactor', 'repair', 'fabrication',
                      'fabrication_cancel', 'activated', 'deactivated', 'wire', 'pump',
                      'settings_change', 'console', 'death', 'chat', 'login', 'logout',
                      'attack', 'structure', 'kick', 'name_change'}
    TECHNICAL_ACTIONS = {'connection', 'radio', 'other'}

    # If player_names provided (comma-separated), search across all names
    effective_player = player
    names_list = None
    if player_names:
        names_list = [n.strip() for n in player_names.split(',') if n.strip()]

    offset = (page - 1) * page_size

    events, total, total_is_capped = await run_in_threadpool(
        search_logs,
        server_name=server if server else None,
        player_name=effective_player if effective_player else None,
        action_type=action_type if action_type else None,
        search_text=search if search else None,
        time_from=dt_from,
        time_to=dt_to,
        category=category,
        limit=page_size,
        offset=offset,
        names_list=names_list,
    )

    return JSONResponse({
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "player_name": e.player_name,
                "action_type": e.action_type,
                "details": e.details,
                "server_name": e.server_name,
                "source_file": e.source_file,
            }
            for e in events
        ],
        "total": total,
        # True when there are more matches than `total` reports (the count is
        # capped — see services_logs.COUNT_CAP): the UI shows "5000+" instead
        # of a precise number rather than paying for a full-table COUNT(*).
        "total_is_capped": total_is_capped,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 1,
    })


@router.get("/api/v1/admin/logs/players")
async def api_logs_players(
    request: Request,
    server: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get unique player names from logs."""
    if not check_permission(current_admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    players = await run_in_threadpool(get_unique_players, server)
    return JSONResponse({"players": players})


@router.get("/api/v1/admin/logs/player-map")
async def api_logs_player_map(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get player name→steamid mapping from parsed logs."""
    if not check_permission(current_admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    pmap = await run_in_threadpool(get_player_map)
    return JSONResponse(pmap)


@router.get("/api/v1/admin/logs/stats")
async def api_logs_stats(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get log cache statistics."""
    if not check_permission(current_admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    stats = await run_in_threadpool(get_log_stats)
    # Values are already strings from JSON storage, no conversion needed
    return JSONResponse(stats)


@router.post("/api/v1/admin/logs/refresh")
async def api_logs_refresh(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Force refresh logs from SFTP servers."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может обновлять логи")

    force_refresh_logs()
    return JSONResponse({"status": "ok", "message": "Обновление логов запущено в фоне"})


# ══════════════════════════════════════════════════════════════════════════════
# SFTP FILE MANAGER (for sub-projects page)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/sftp/files")
def api_sftp_list(
    request: Request,
    server: int = Query(..., ge=1, le=2),
    path: str = Query(""),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """List files in SFTP directory."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)

    mgr = SFTPFileManager(server)
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail=f"SFTP сервер {server} не настроен")

    try:
        files = mgr.list_directory(path)
        return JSONResponse({
            "files": [
                {
                    "name": f.name,
                    "path": f.path,
                    "is_dir": f.is_dir,
                    "size": f.size,
                    "mtime": f.mtime.isoformat() if f.mtime else None,
                    "permissions": f.permissions,
                }
                for f in files
            ],
            "current_path": path,
            "server": server,
            "server_name": mgr.name,
        })
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/admin/sftp/read")
def api_sftp_read(
    request: Request,
    server: int = Query(..., ge=1, le=2),
    path: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Read file content from SFTP."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)

    mgr = SFTPFileManager(server)
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail=f"SFTP сервер {server} не настроен")

    try:
        content = mgr.read_file(path)
        return JSONResponse({"content": content, "path": path, "server": server})
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/admin/sftp/write")
async def api_sftp_write(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Write/save file content to SFTP."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)

    body = await request.json()
    server = body.get("server")
    path = body.get("path")
    content = body.get("content", "")

    if not server or not path:
        raise HTTPException(status_code=400, detail="server and path required")

    mgr = SFTPFileManager(int(server))
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail=f"SFTP сервер {server} не настроен")

    import asyncio
    try:
        await asyncio.to_thread(mgr.write_file, path, content)
        from routes.admin import log_admin_action
        log_admin_action(db, current_admin.id, "sftp_file_edit", {"server": server, "path": path})
        return JSONResponse({"status": "ok", "message": "Файл сохранён"})
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/admin/sftp/upload")
async def api_sftp_upload(
    request: Request,
    server: int = Form(...),
    path: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Upload a file to SFTP server."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)

    mgr = SFTPFileManager(server)
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail=f"SFTP сервер {server} не настроен")

    try:
        file_data = await file.read()
        import asyncio
        await asyncio.to_thread(mgr.upload_file, path, file_data, file.filename)
        from routes.admin import log_admin_action
        log_admin_action(db, current_admin.id, "sftp_file_upload", {
            "server": server, "path": path, "filename": file.filename
        })
        return JSONResponse({"status": "ok", "message": f"Файл {file.filename} загружен"})
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/admin/sftp/download")
def api_sftp_download(
    request: Request,
    server: int = Query(..., ge=1, le=2),
    path: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Download a file from SFTP server."""
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403)

    mgr = SFTPFileManager(server)
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail=f"SFTP сервер {server} не настроен")

    try:
        data = mgr.download_file(path)
        filename = path.split('/')[-1] if '/' in path else path
        # RFC 5987: encode non-ASCII filenames properly
        from urllib.parse import quote
        encoded_filename = quote(filename)
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/admin/sftp/delete")
async def api_sftp_delete(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Delete a file from SFTP server."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)

    body = await request.json()
    server = body.get("server")
    path = body.get("path")

    if not server or not path:
        raise HTTPException(status_code=400, detail="server and path required")

    mgr = SFTPFileManager(int(server))
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail=f"SFTP сервер {server} не настроен")

    try:
        import asyncio
        await asyncio.to_thread(mgr.delete_file, path)
        from routes.admin import log_admin_action
        log_admin_action(db, current_admin.id, "sftp_file_delete", {"server": server, "path": path})
        return JSONResponse({"status": "ok", "message": "Файл удалён"})
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/admin/sftp/mkdir")
async def api_sftp_mkdir(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Create a directory on SFTP server."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)

    body = await request.json()
    server = body.get("server")
    path = body.get("path")

    if not server or not path:
        raise HTTPException(status_code=400, detail="server and path required")

    mgr = SFTPFileManager(int(server))
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail=f"SFTP сервер {server} не настроен")

    try:
        import asyncio
        await asyncio.to_thread(mgr.create_directory, path)
        return JSONResponse({"status": "ok", "message": "Директория создана"})
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# PLAYTIME STATS & REWARD GROUPS API
# ══════════════════════════════════════════════════════════════════════════════

from services_playtime_stats import (
    calculate_playtime_stats, get_latest_playtime_stats,
    load_reward_groups, save_reward_groups,
    load_playtime_conditions, save_playtime_conditions,
    load_rating_rewards, save_rating_rewards,
    check_and_apply_rating_rewards, check_and_apply_playtime_rewards,
)


@router.get("/api/v1/admin/playtime/stats")
async def api_playtime_stats(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get latest playtime statistics."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403)
    data = get_latest_playtime_stats()
    return JSONResponse(data or {"players": {}, "generated_at": None})


@router.post("/api/v1/admin/playtime/generate")
async def api_playtime_generate(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Force generate playtime stats now."""
    if not check_permission(current_admin, "ratings", "recalculate"):
        raise HTTPException(status_code=403)
    import threading
    def _gen():
        calculate_playtime_stats()
    threading.Thread(target=_gen, daemon=True).start()
    return JSONResponse({"status": "ok", "message": "Генерация запущена"})


@router.get("/api/v1/admin/reward-groups")
async def api_get_reward_groups(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get all configured reward groups."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403)
    return JSONResponse(load_reward_groups())


@router.post("/api/v1/admin/reward-groups")
async def api_save_reward_groups(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Save reward groups configuration."""
    if not check_permission(current_admin, "ratings", "manage_rewards"):
        raise HTTPException(status_code=403)
    body = await request.json()
    save_reward_groups(body if isinstance(body, list) else [])
    return JSONResponse({"status": "ok"})


@router.get("/api/v1/admin/playtime/conditions")
async def api_get_playtime_conditions(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get playtime reward conditions."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403)
    return JSONResponse(load_playtime_conditions())


@router.post("/api/v1/admin/playtime/conditions")
async def api_save_playtime_conditions(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Save playtime reward conditions."""
    if not check_permission(current_admin, "ratings", "manage_rewards"):
        raise HTTPException(status_code=403)
    body = await request.json()
    save_playtime_conditions(body if isinstance(body, list) else [])
    return JSONResponse({"status": "ok"})


@router.get("/api/v1/admin/rating-rewards")
async def api_get_rating_rewards(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get rating-based reward conditions."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403)
    return JSONResponse(load_rating_rewards())


@router.post("/api/v1/admin/rating-rewards")
async def api_save_rating_rewards(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Save rating-based reward conditions."""
    if not check_permission(current_admin, "ratings", "manage_rewards"):
        raise HTTPException(status_code=403)
    body = await request.json()
    save_rating_rewards(body if isinstance(body, list) else [])
    return JSONResponse({"status": "ok"})


@router.post("/api/v1/admin/rewards/apply-now")
async def api_apply_rewards_now(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Force apply all reward checks (rating + playtime) now."""
    if not check_permission(current_admin, "ratings", "recalculate"):
        raise HTTPException(status_code=403)
    import threading
    def _apply():
        check_and_apply_rating_rewards()
        check_and_apply_playtime_rewards()
    threading.Thread(target=_apply, daemon=True).start()
    return JSONResponse({"status": "ok", "message": "Проверка наград запущена"})
