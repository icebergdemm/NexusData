"""
Analytics routes — dashboard with online snapshots, role/submarine popularity, etc.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, text, desc
from typing import Optional
from datetime import datetime, timedelta, timezone
import secrets
import logging

from database import get_db
from security import check_permission
from models import (
    AdminUser, AdminSession, OnlineSnapshot, PlayerSession,
    PlayerRoleStat, RoundEvent, PlayerData, PlayerStats,
)

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)

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
    now = datetime.utcnow()
    delta = {"6h": timedelta(hours=6), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}.get(period, timedelta(hours=24))
    since = now - delta

    q = db.query(OnlineSnapshot).filter(OnlineSnapshot.recorded_at >= since)
    if server:
        q = q.filter(OnlineSnapshot.server_name == server.strip().upper())
    snapshots = q.order_by(OnlineSnapshot.recorded_at).all()

    slot_mins = {"6h": 5, "24h": 15, "7d": 60, "30d": 240}.get(period, 15)
    slots = {}
    for s in snapshots:
        ts = s.recorded_at
        slot_key = ts.replace(minute=(ts.minute // slot_mins) * slot_mins, second=0, microsecond=0)
        slots.setdefault(slot_key, []).append(s.player_count)

    labels, values_avg, values_peak = [], [], []
    for ts in sorted(slots.keys()):
        counts = slots[ts]
        # recorded_at is already MSK — no conversion needed
        if period in ("6h", "24h"):
            labels.append(ts.strftime("%H:%M"))
        elif period == "7d":
            labels.append(ts.strftime("%d.%m %H:%M"))
        else:
            labels.append(ts.strftime("%d.%m"))
        values_avg.append(round(sum(counts) / len(counts), 1))
        values_peak.append(max(counts))

    return JSONResponse({"labels": labels, "avg": values_avg, "peak": values_peak, "total_snapshots": len(snapshots)})


@router.get("/api/v1/analytics/role-popularity")
async def api_role_popularity(request: Request, db: Session = Depends(get_db)):
    _require_analytics(request, db)
    rows = db.query(PlayerRoleStat.role, func.sum(PlayerRoleStat.rounds_played).label("total")).group_by(PlayerRoleStat.role).order_by(desc("total")).all()
    return JSONResponse({"roles": [r[0] for r in rows], "counts": [int(r[1] or 0) for r in rows]})


@router.get("/api/v1/analytics/submarine-popularity")
async def api_submarine_popularity(request: Request, period: str = Query("30d"), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    delta = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90), "all": timedelta(days=36500)}.get(period, timedelta(days=30))
    since = datetime.utcnow() - delta
    rows = (
        db.query(RoundEvent.submarine_name, func.count().label("cnt"))
        .filter(RoundEvent.event_type == "start", RoundEvent.created_at >= since, RoundEvent.submarine_name.isnot(None))
        .group_by(RoundEvent.submarine_name).order_by(desc("cnt")).limit(20).all()
    )
    return JSONResponse({"submarines": [r[0] for r in rows], "counts": [int(r[1]) for r in rows]})


@router.get("/api/v1/analytics/peak-hours")
async def api_peak_hours(request: Request, server: Optional[str] = Query(None), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    since = datetime.utcnow() - timedelta(days=14)
    q = db.query(OnlineSnapshot).filter(OnlineSnapshot.recorded_at >= since)
    if server:
        q = q.filter(OnlineSnapshot.server_name == server.strip().upper())
    snapshots = q.all()

    hourly = {}
    for s in snapshots:
        # recorded_at is already MSK — use hour directly
        h = s.recorded_at.hour
        hourly.setdefault(h, []).append(s.player_count)

    labels = [f"{h:02d}:00" for h in range(24)]
    avg_v = [round(sum(hourly.get(h, [0])) / max(len(hourly.get(h, [0])), 1), 1) for h in range(24)]
    peak_v = [max(hourly.get(h, [0])) for h in range(24)]
    return JSONResponse({"labels": labels, "avg": avg_v, "peak": peak_v})


@router.get("/api/v1/analytics/session-duration")
async def api_session_duration(request: Request, period: str = Query("30d"), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    delta = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}.get(period, timedelta(days=30))
    since = datetime.utcnow() - delta
    sessions = db.query(PlayerSession.session_duration).filter(PlayerSession.login_time >= since, PlayerSession.session_duration.isnot(None)).all()

    buckets = [0] * 8
    bucket_labels = ["<5 мин", "5-15 мин", "15-30 мин", "30м-1ч", "1-2ч", "2-4ч", "4-8ч", "8ч+"]
    for (dur,) in sessions:
        if dur is None:
            continue
        m = dur / 60
        idx = 0 if m < 5 else 1 if m < 15 else 2 if m < 30 else 3 if m < 60 else 4 if m < 120 else 5 if m < 240 else 6 if m < 480 else 7
        buckets[idx] += 1
    return JSONResponse({"labels": bucket_labels, "counts": buckets, "total_sessions": len(sessions)})


@router.get("/api/v1/analytics/server-comparison")
async def api_server_comparison(request: Request, db: Session = Depends(get_db)):
    _require_analytics(request, db)
    since = datetime.utcnow() - timedelta(days=7)
    result = {}
    for sn in ["ASPER1", "ASPER2"]:
        row = db.query(func.avg(OnlineSnapshot.player_count), func.max(OnlineSnapshot.player_count)).filter(OnlineSnapshot.server_name == sn, OnlineSnapshot.recorded_at >= since).first()
        rounds = db.query(func.count()).filter(RoundEvent.event_type == "start", RoundEvent.server_name == sn, RoundEvent.created_at >= since).scalar() or 0
        result[sn] = {"avg_online": round(float(row[0] or 0), 1), "peak_online": int(row[1] or 0), "rounds_week": int(rounds)}
    return JSONResponse(result)


@router.get("/api/v1/analytics/player-retention")
async def api_player_retention(request: Request, db: Session = Depends(get_db)):
    _require_analytics(request, db)
    now = datetime.utcnow()
    month_ago = now - timedelta(days=30)
    week_ago = now - timedelta(days=7)

    total_new = db.query(func.count(func.distinct(PlayerSession.player_id))).filter(PlayerSession.login_time >= month_ago).scalar() or 0
    returning = db.execute(text("SELECT COUNT(*) FROM (SELECT player_id FROM player_sessions WHERE login_time >= :since GROUP BY player_id HAVING COUNT(*) > 1) t"), {"since": month_ago}).scalar() or 0
    active_week = db.execute(text("SELECT COUNT(DISTINCT player_id) FROM player_sessions WHERE login_time >= :week AND player_id IN (SELECT DISTINCT player_id FROM player_sessions WHERE login_time >= :month)"), {"week": week_ago, "month": month_ago}).scalar() or 0

    return JSONResponse({"total_new_30d": total_new, "returning_30d": returning, "retention_pct": round(returning / max(total_new, 1) * 100, 1), "active_this_week": active_week})


@router.get("/api/v1/analytics/top-players")
async def api_top_players(request: Request, metric: str = Query("playtime"), period: str = Query("7d"), limit: int = Query(15), db: Session = Depends(get_db)):
    _require_analytics(request, db)
    delta = {"7d": timedelta(days=7), "30d": timedelta(days=30), "all": timedelta(days=36500)}.get(period, timedelta(days=7))
    since = datetime.utcnow() - delta

    if metric == "playtime":
        rows = db.execute(text("SELECT ps.player_id, pd.player_last_time_name, SUM(ps.session_duration) as total FROM player_sessions ps LEFT JOIN players_data pd ON pd.player_id = ps.player_id WHERE ps.login_time >= :since AND ps.session_duration IS NOT NULL GROUP BY ps.player_id ORDER BY total DESC LIMIT :lim"), {"since": since, "lim": limit}).fetchall()
        return JSONResponse({"players": [{"name": r[1] or f"#{r[0]}", "value": int(r[2] or 0) // 3600, "unit": "ч"} for r in rows]})
    elif metric == "sessions":
        rows = db.execute(text("SELECT ps.player_id, pd.player_last_time_name, COUNT(*) as cnt FROM player_sessions ps LEFT JOIN players_data pd ON pd.player_id = ps.player_id WHERE ps.login_time >= :since GROUP BY ps.player_id ORDER BY cnt DESC LIMIT :lim"), {"since": since, "lim": limit}).fetchall()
        return JSONResponse({"players": [{"name": r[1] or f"#{r[0]}", "value": int(r[2]), "unit": ""} for r in rows]})
    else:
        rows = db.query(PlayerData.player_last_time_name, PlayerStats.killed_monsters).join(PlayerStats, PlayerStats.player_id == PlayerData.player_id).order_by(desc(PlayerStats.killed_monsters)).limit(limit).all()
        return JSONResponse({"players": [{"name": r[0] or "?", "value": int(r[1] or 0), "unit": ""} for r in rows]})
