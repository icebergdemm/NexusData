"""
Analytics routes — dashboard with online snapshots, role/submarine popularity, etc.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import secrets
import logging

from database import get_db
from security import check_permission
from models import AdminUser, AdminSession
import services_analytics as sa

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)


def _cached_snapshot() -> Optional[dict]:
    """Return the background-refreshed default-view snapshot, if ready."""
    try:
        from task_scheduler import scheduler
        result = scheduler.get_result("analytics_refresh")
        if result and result.status == "done" and result.data:
            return result.data
    except ImportError:
        pass
    return None

router = APIRouter()


def _get_admin(request: Request, db: Session) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = db.query(AdminUser).options(joinedload(AdminUser.privilege_group)).filter(AdminUser.id == session.admin_id).first()
            if admin:
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require_analytics(request: Request, db: Session) -> AdminUser:
    admin = _get_admin(request, db)
    if not check_permission(admin, "admin", "view"):
        raise HTTPException(status_code=403)
    return admin


@router.get("/admin/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, db: Session = Depends(get_db)):
    admin = _require_analytics(request, db)
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return templates.TemplateResponse("admin/analytics.html", {"request": request, "admin": admin})


@router.get("/api/v1/analytics/online-timeline")
async def api_online_timeline(request: Request, period: str = Query("24h"), server: Optional[str] = Query(None), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    if period == sa.DEFAULT_ONLINE_PERIOD and not server:
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["online_timeline"])
    return JSONResponse(sa.compute_online_timeline(db, period=period, server=server))


@router.get("/api/v1/analytics/role-popularity")
async def api_role_popularity(request: Request, db: Session = Depends(get_db)):
    _require_analytics(request, db)
    cached = _cached_snapshot()
    if cached:
        return JSONResponse(cached["role_popularity"])
    return JSONResponse(sa.compute_role_popularity(db))


@router.get("/api/v1/analytics/submarine-popularity")
async def api_submarine_popularity(request: Request, period: str = Query("30d"), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    if period == sa.DEFAULT_SUB_PERIOD:
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["submarine_popularity"])
    return JSONResponse(sa.compute_submarine_popularity(db, period=period))


@router.get("/api/v1/analytics/peak-hours")
async def api_peak_hours(request: Request, server: Optional[str] = Query(None), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    if not server:
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["peak_hours"])
    return JSONResponse(sa.compute_peak_hours(db, server=server))


@router.get("/api/v1/analytics/session-duration")
async def api_session_duration(request: Request, period: str = Query("30d"), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    if period == sa.DEFAULT_SESSION_PERIOD:
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["session_duration"])
    return JSONResponse(sa.compute_session_duration(db, period=period))


@router.get("/api/v1/analytics/server-comparison")
async def api_server_comparison(request: Request, db: Session = Depends(get_db)):
    _require_analytics(request, db)
    cached = _cached_snapshot()
    if cached:
        return JSONResponse(cached["server_comparison"])
    return JSONResponse(sa.compute_server_comparison(db))


@router.get("/api/v1/analytics/player-retention")
async def api_player_retention(request: Request, db: Session = Depends(get_db)):
    _require_analytics(request, db)
    cached = _cached_snapshot()
    if cached:
        return JSONResponse(cached["player_retention"])
    return JSONResponse(sa.compute_player_retention(db))


@router.get("/api/v1/analytics/top-players")
async def api_top_players(request: Request, metric: str = Query("playtime"), period: str = Query("7d"), limit: int = Query(15), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    if metric == sa.DEFAULT_TOP_METRIC and period == sa.DEFAULT_TOP_PERIOD and limit == 15:
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["top_players"])
    return JSONResponse(sa.compute_top_players(db, metric=metric, period=period, limit=limit))
