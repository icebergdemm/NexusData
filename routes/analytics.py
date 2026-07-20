"""
Analytics routes — dashboard with online snapshots, role/submarine popularity, etc.

Every chart endpoint (except role-popularity, whose source table carries no
timestamp) accepts the SAME range shape: either `preset` (one of
"24h"/"week"/"month"/"year") or an explicit `start`/`end` (YYYY-MM-DD) pair —
_resolve_range turns whichever was given into a concrete (since, until) the
frontend's single shared date-range picker drives every chart with.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
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

_PRESET_DELTAS = {
    "24h": timedelta(hours=24),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
}


def _resolve_range(preset: Optional[str], start: Optional[str], end: Optional[str]):
    now = datetime.utcnow()
    if start or end:
        try:
            since = datetime.strptime(start, "%Y-%m-%d") if start else now - timedelta(days=7)
        except ValueError:
            since = now - timedelta(days=7)
        try:
            until = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)) if end else now
        except ValueError:
            until = now
        return since, until

    delta = _PRESET_DELTAS.get(preset or sa.DEFAULT_PRESET, _PRESET_DELTAS["week"])
    return now - delta, now


def _is_default_range(preset: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    """True exactly for "no custom dates, default preset" — the one
    combination the background snapshot keeps warm."""
    if start or end:
        return False
    return (preset or sa.DEFAULT_PRESET) == sa.DEFAULT_PRESET


def _get_admin(request: Request, db: Session) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = db.query(AdminUser).options(joinedload(AdminUser.privilege_group)).filter(AdminUser.id == session.admin_id).first()
            if admin:
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require_analytics(request: Request, db: Session) -> AdminUser:
    admin = _get_admin(request, db)
    if not check_permission(admin, "analytics", "view"):
        raise HTTPException(status_code=403)
    return admin


@router.get("/adminold/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, db: Session = Depends(get_db)):
    admin = _require_analytics(request, db)
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return templates.TemplateResponse("admin/analytics.html", {"request": request, "admin": admin})


@router.get("/api/v1/analytics/online-timeline")
async def api_online_timeline(
    request: Request, preset: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    server: Optional[str] = Query(None), db: Session = Depends(get_db),
):
    _require_analytics(request, db)
    since, until = _resolve_range(preset, start, end)
    if _is_default_range(preset, start, end) and not server:
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["online_timeline"])
    return JSONResponse(await run_in_threadpool(sa.compute_online_timeline, db, since=since, until=until, server=server))


@router.get("/api/v1/analytics/role-popularity")
async def api_role_popularity(request: Request, db: Session = Depends(get_db)):
    _require_analytics(request, db)
    cached = _cached_snapshot()
    if cached:
        return JSONResponse(cached["role_popularity"])
    return JSONResponse(await run_in_threadpool(sa.compute_role_popularity, db))


@router.get("/api/v1/analytics/submarine-popularity")
async def api_submarine_popularity(
    request: Request, preset: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_analytics(request, db)
    since, until = _resolve_range(preset, start, end)
    if _is_default_range(preset, start, end):
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["submarine_popularity"])
    return JSONResponse(await run_in_threadpool(sa.compute_submarine_popularity, db, since=since, until=until))


@router.get("/api/v1/analytics/peak-hours")
async def api_peak_hours(
    request: Request, preset: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    server: Optional[str] = Query(None), db: Session = Depends(get_db),
):
    _require_analytics(request, db)
    since, until = _resolve_range(preset, start, end)
    if _is_default_range(preset, start, end) and not server:
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["peak_hours"])
    return JSONResponse(await run_in_threadpool(sa.compute_peak_hours, db, since=since, until=until, server=server))


@router.get("/api/v1/analytics/session-duration")
async def api_session_duration(
    request: Request, preset: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_analytics(request, db)
    since, until = _resolve_range(preset, start, end)
    if _is_default_range(preset, start, end):
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["session_duration"])
    return JSONResponse(await run_in_threadpool(sa.compute_session_duration, db, since=since, until=until))


@router.get("/api/v1/analytics/server-comparison")
async def api_server_comparison(
    request: Request, preset: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_analytics(request, db)
    since, until = _resolve_range(preset, start, end)
    if _is_default_range(preset, start, end):
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["server_comparison"])
    return JSONResponse(await run_in_threadpool(sa.compute_server_comparison, db, since=since, until=until))


@router.get("/api/v1/analytics/player-retention")
async def api_player_retention(
    request: Request, preset: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_analytics(request, db)
    since, until = _resolve_range(preset, start, end)
    if _is_default_range(preset, start, end):
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["player_retention"])
    return JSONResponse(await run_in_threadpool(sa.compute_player_retention, db, since=since, until=until))


@router.get("/api/v1/analytics/top-players")
async def api_top_players(
    request: Request, metric: str = Query("playtime"),
    preset: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    limit: int = Query(15), db: Session = Depends(get_db),
):
    _require_analytics(request, db)
    since, until = _resolve_range(preset, start, end)
    if metric == "playtime" and limit == 15 and _is_default_range(preset, start, end):
        cached = _cached_snapshot()
        if cached:
            return JSONResponse(cached["top_players"])
    return JSONResponse(await run_in_threadpool(sa.compute_top_players, db, since=since, until=until, metric=metric, limit=limit))
