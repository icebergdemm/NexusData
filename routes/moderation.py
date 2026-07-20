"""
Server Moderation module.

API routes (for the game server):
  POST /api/v1/server/players          – replace online players snapshot
  POST /api/v1/server/anticheat        – report anticheat event
  GET  /api/v1/server/actions          – get pending action queue
  POST /api/v1/server/actions/{id}/ack – acknowledge executed action

Admin panel routes:
  GET  /admin/moderation               – main page with 4 tabs
  POST /admin/moderation/action        – enqueue kick / ban / message
  POST /admin/moderation/anticheat/{id}/review – mark AC event reviewed
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional, List
import secrets
from datetime import datetime, timedelta

from database import get_db
from security import check_permission
from config import get_settings
from security import verify_external_api_key_dependency
from services import ServerModerationService, PlayerService
from schemas import (
    ServerPlayersReport, ServerPlayersReportResult,
    AnticheatEventIn, AnticheatEventOut,
    ServerActionOut, ServerActionAck,
    QueueActionIn, QueueActionResult,
)
from models import AdminUser, AdminSession
import logging

settings = get_settings()
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)

# ── Reusable helpers (copied from admin.py pattern) ──────────────────────────

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
                request.session["username"] = admin.discord_username
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


async def ensure_csrf(request: Request, db: Session):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    session_csrf = request.session.get("csrf_token")
    if csrf_token and session_csrf and secrets.compare_digest(str(csrf_token), str(session_csrf)):
        return
    token = request.cookies.get("admin_token")
    if token:
        now = datetime.utcnow()
        s = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
        if s and (s.expires_at is None or s.expires_at > now):
            logger.warning("CSRF mismatch accepted via valid admin session for %s", request.url.path)
            return
    raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _fmt_playtime(seconds: Optional[int]) -> str:
    if not seconds:
        return "0ч 0м"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}ч {m}м"


# ════════════════════════════════════════════════════════════════════════════
# API ROUTER  (for the game server, protected by external API key)
# ════════════════════════════════════════════════════════════════════════════

api_router = APIRouter(
    prefix="/api/v1/server",
    tags=["server-moderation"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)


@api_router.post("/players", response_model=ServerPlayersReportResult)
def report_online_players(
    report: ServerPlayersReport,
    server_name: Optional[str] = Query(None, description="Имя сервера-отправителя"),
    db: Session = Depends(get_db),
):
    """Replace the full snapshot of currently online players for a server.

    Uses per-player TTL tracking — players age out individually, so one
    server's report cannot wipe another server's online players.
    """
    svc = ServerModerationService(db)
    count = svc.replace_online_players(report.players, server_name=server_name)
    _update_server_live_state(db, server_name, report.submarine_health, report.supply_shuttle_eta)
    return ServerPlayersReportResult(status="ok", count=count)


def _update_server_live_state(
    db: Session,
    server_name: Optional[str],
    submarine_health: Optional[int],
    supply_shuttle_eta: Optional[int],
):
    """Upsert the live per-server state (submarine health, supply shuttle ETA).

    Stored as an absolute UTC arrival timestamp (not the raw seconds-remaining)
    so consumers (the Discord round-status embed) can compute an accurate
    countdown regardless of when they last refreshed, rather than a value that
    goes stale between the game server's periodic reports.
    """
    if submarine_health is None and supply_shuttle_eta is None:
        return
    from models import ServerLiveState

    sn = (server_name or "UNKNOWN").strip()
    row = db.query(ServerLiveState).filter(ServerLiveState.server_name == sn).first()
    if not row:
        row = ServerLiveState(server_name=sn)
        db.add(row)

    if submarine_health is not None:
        row.submarine_health = submarine_health
    if supply_shuttle_eta is not None:
        row.supply_shuttle_eta_seconds = supply_shuttle_eta
        row.shuttle_arrival_at = datetime.utcnow() + timedelta(seconds=supply_shuttle_eta)
    else:
        row.supply_shuttle_eta_seconds = None
        row.shuttle_arrival_at = None

    db.commit()


@api_router.post("/anticheat", response_model=AnticheatEventOut)
def report_anticheat_event(
    event: AnticheatEventIn,
    db: Session = Depends(get_db),
):
    """Record a single anticheat trigger from the game server."""
    svc = ServerModerationService(db)
    ev = svc.add_anticheat_event(event)
    return ev


@api_router.get("/actions")
def get_pending_actions(
    server_name: Optional[str] = Query(None, description="Имя сервера — получить только свои задачи"),
    db: Session = Depends(get_db),
):
    """Return pending actions for the specified server."""
    svc = ServerModerationService(db)
    rows = svc.get_pending_actions(server_name=server_name)
    return [ServerActionOut.from_v2(r) for r in rows]


@api_router.post("/actions/{action_id}/ack")
def acknowledge_action(
    action_id: int,
    ack: ServerActionAck,
    db: Session = Depends(get_db),
):
    """Acknowledge that the game server executed (or failed) a queued action.
    Include server_name in the body so NexusData can track per-server completion for broadcast actions."""
    svc = ServerModerationService(db)
    ok = svc.acknowledge_action(action_id, ack.success, ack.error_message, server_name=ack.server_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Action not found")
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL ROUTER  (HTML pages, protected by admin session)
# ════════════════════════════════════════════════════════════════════════════

panel_router = APIRouter()


def _moderation_action_permission(action_type: Optional[str]) -> str:
    """Maps a kick/ban/unban/message action to its own registered permission
    (permissions_registry.py) instead of the generic "edit"."""
    return {"kick": "kick", "ban": "ban", "unban": "ban", "message": "message"}.get(action_type, "edit")


@panel_router.get("/adminold/moderation", response_class=HTMLResponse)
async def moderation_page(
    request: Request,
    tab: str = Query("status", pattern=r"^(status|banhammer|logs|commands|anticheat|sauron)$"),
    server: Optional[str] = Query(None, description="Фильтр по серверу: ASPER1 или ASPER2"),
    search: Optional[str] = Query(None, min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Main server moderation page with five tabs."""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = ServerModerationService(db)

    online_players = svc.get_online_players(server_name=server)
    online_count = len(online_players)
    online_servers = svc.get_online_servers()
    selected_server = server

    # Round state: last start event per server
    from routes.dispatch import get_round_state
    round_states = {}
    for sn in (["ASPER1", "ASPER2"] if not server else [server]):
        round_states[sn] = get_round_state(db, sn)

    # Banhammer: online + recently offline + search
    recent_offline = svc.get_recent_offline_players(limit=50)

    # Enrich recent_offline with last session logout time
    if recent_offline:
        from models import PlayerSession
        pid_list = [p.player_id for p in recent_offline]
        last_sessions = (
            db.query(PlayerSession)
            .filter(
                PlayerSession.player_id.in_(pid_list),
                PlayerSession.logout_time.isnot(None),
            )
            .order_by(PlayerSession.logout_time.desc())
            .all()
        )
        last_logout_map: dict = {}
        for s in last_sessions:
            if s.player_id not in last_logout_map:
                last_logout_map[s.player_id] = s.logout_time
        
        # Convert times to Moscow timezone and attach
        from datetime import timezone, timedelta
        MSK = timezone(timedelta(hours=3))
        for p in recent_offline:
            logout_dt = last_logout_map.get(p.player_id)
            if logout_dt and logout_dt.tzinfo is None:
                # Assume UTC if naive, convert to MSK
                logout_dt = logout_dt.replace(tzinfo=timezone.utc).astimezone(MSK)
            p.last_session_logout = logout_dt
            
            # Also convert last_login_time to MSK
            if p.last_login_time and p.last_login_time.tzinfo is None:
                p.last_login_time = p.last_login_time.replace(tzinfo=timezone.utc).astimezone(MSK)

    # Search across all players (for Banhammer)
    search_results = []
    if search:
        player_svc = PlayerService(db)
        search_results = player_svc.search_players(search, limit=50)

    # Anticheat
    ac_events = svc.get_anticheat_events(limit=200, reviewed=None)
    # Enrich AC events with player_id for profile link
    ac_enriched = []
    for ev in ac_events:
        ac_enriched.append({
            "id": ev.id,
            "player_steamid": ev.player_steamid,
            "player_name": ev.player_name,
            "player_id": ev.player_id,
            "event_type": ev.event_type,
            "details": ev.details,
            "occurred_at": ev.occurred_at,
            "reviewed": ev.reviewed,
        })

    return templates.TemplateResponse("admin/moderation.html", {
        "request": request,
        "admin": current_admin,
        "active_tab": tab,
        "online_players": online_players,
        "online_count": online_count,
        "online_servers": online_servers,
        "selected_server": selected_server,
        "round_states": round_states,
        "recent_offline": recent_offline,
        "search_results": search_results,
        "search_query": search,
        "ac_events": ac_enriched,
        "fmt_playtime": _fmt_playtime,
        "csrf_token": request.session.get("csrf_token"),
    })


@panel_router.post("/adminold/moderation/action")
async def enqueue_moderation_action(
    request: Request,
    action_type: str = Form(...),
    target_steamid: str = Form(...),
    reason: Optional[str] = Form(None),
    duration_minutes: Optional[int] = Form(None),
    message_text: Optional[str] = Form(None),
    server_name: Optional[str] = Form(None),
    redirect_tab: str = Form("status"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Enqueue a kick / ban / unban / message action from the admin panel."""
    if not check_permission(current_admin, "moderation", _moderation_action_permission(action_type)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    await ensure_csrf(request, db)

    if action_type not in ("kick", "ban", "unban", "message"):
        raise HTTPException(status_code=400, detail="Invalid action_type")

    from schemas import QueueActionIn
    action_in = QueueActionIn(
        action_type=action_type,
        target_steamid=target_steamid,
        reason=reason or None,
        duration_minutes=duration_minutes or None,
        message_text=message_text or None,
        server_name=server_name.strip().upper() if server_name and server_name.strip() else None,
    )

    svc = ServerModerationService(db)
    action = svc.enqueue_action(action_in, admin_id=current_admin.id)
    logger.info(
        "Admin %s queued %s for %s on server=%s (action_id=%s)",
        current_admin.discord_username, action_type, target_steamid,
        server_name or "all", action.id,
    )

    # For bans — also create a punishment record
    if action_type == "ban":
        from models import PlayerData, PlayerPunishment
        from services import _now_msk
        player = db.query(PlayerData).filter(PlayerData.player_steamid == target_steamid).first()
        punishment = PlayerPunishment(
            player_id=player.player_id if player else 0,
            player_steamid=target_steamid,
            player_ip=player.last_ip if player else None,
            type="ban",
            reason=reason or None,
            issued_by_admin_id=current_admin.id,
            issued_by_name=f"ND. {current_admin.discord_username}",
            issued_by_was_superuser=current_admin.is_superuser,
            issued_at=_now_msk(),
            expires_at=None,
            is_active=1,
        )
        db.add(punishment)
        db.commit()
        # Queue Discord notification for the ban
        from routes.game_api import _queue_punishment_discord_notification
        _queue_punishment_discord_notification(db, punishment)

    from routes.admin import log_admin_action, push_flash
    log_admin_action(db, current_admin.id, f"moderation.{action_type}", {
        "target_steamid": target_steamid,
        "reason": reason,
        "server_name": server_name,
        "action_id": action.id,
    })
    srv_label = f" → {server_name}" if server_name else " → все серверы"
    push_flash(request, "success", f"Действие «{action_type}» поставлено в очередь для {target_steamid}{srv_label}")
    return RedirectResponse(url=f"/adminold/moderation?tab={redirect_tab}", status_code=302)


@panel_router.post("/adminold/moderation/anticheat/{event_id}/review")
async def review_anticheat_event(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Mark an anticheat event as reviewed."""
    if not check_permission(current_admin, "moderation", "manage_anticheat"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf(request, db)
    svc = ServerModerationService(db)
    if not svc.mark_reviewed(event_id):
        raise HTTPException(status_code=404, detail="Event not found")

    from routes.admin import push_flash
    push_flash(request, "success", "Событие отмечено как проверенное")
    return RedirectResponse(url="/adminold/moderation?tab=anticheat", status_code=302)


# ════════════════════════════════════════════════════════════════════════════
# MODERATION JSON API (for the React SPA — mirrors moderation_page/enqueue_moderation_action)
# ════════════════════════════════════════════════════════════════════════════

def _serialize_online_player(info: dict) -> dict:
    return {
        "player_steamid": info.get("player_steamid"),
        "player_name": info.get("player_name"),
        "character_name": info.get("character_name"),
        "job_id": info.get("job_id"),
        "ping": info.get("ping"),
        "player_id": info.get("player_id"),
        "server_name": info.get("server_name"),
        "join_time": info["join_time"].isoformat() if info.get("join_time") else None,
        "last_seen": info["last_seen"].isoformat() if info.get("last_seen") else None,
        "total_play_time": info.get("total_play_time"),
        "is_new_player": info.get("is_new_player"),
    }


def _serialize_offline_player(p) -> dict:
    return {
        "player_id": p.player_id,
        "player_steamid": p.player_steamid,
        "player_last_time_name": p.player_last_time_name,
        "last_login_time": p.last_login_time.isoformat() if p.last_login_time else None,
        "last_session_logout": p.last_session_logout.isoformat() if getattr(p, "last_session_logout", None) else None,
        "play_time_hours": p.play_time_hours,
    }


def _serialize_round_state_json(rs: dict) -> dict:
    last_end = rs.get("last_end")
    return {
        "active": rs.get("active", False),
        "location": rs.get("location"),
        "submarine_name": rs.get("submarine_name"),
        "mission": rs.get("mission"),
        "started_at": rs["started_at"].isoformat() if rs.get("started_at") else None,
        "players": rs.get("players") or [],
        "last_end_at": last_end.created_at.isoformat() if last_end and last_end.created_at else None,
        "last_end_mission_completed": bool(last_end.mission_completed) if last_end else None,
    }


@panel_router.get("/api/v1/admin/moderation/overview")
async def api_moderation_overview(
    server: Optional[str] = Query(None),
    search: Optional[str] = Query(None, min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Combined status/banhammer/anticheat data — mirrors moderation_page's eager loads."""
    from routes.dispatch import get_round_state

    svc = ServerModerationService(db)
    online_players = svc.get_online_players(server_name=server)
    online_servers = svc.get_online_servers()

    round_states = {}
    for sn in (["ASPER1", "ASPER2"] if not server else [server]):
        round_states[sn] = _serialize_round_state_json(get_round_state(db, sn))

    recent_offline = svc.get_recent_offline_players(limit=50)
    if recent_offline:
        from models import PlayerSession
        pid_list = [p.player_id for p in recent_offline]
        last_sessions = (
            db.query(PlayerSession)
            .filter(PlayerSession.player_id.in_(pid_list), PlayerSession.logout_time.isnot(None))
            .order_by(PlayerSession.logout_time.desc())
            .all()
        )
        last_logout_map = {}
        for s in last_sessions:
            if s.player_id not in last_logout_map:
                last_logout_map[s.player_id] = s.logout_time
        from datetime import timezone
        MSK = timezone(timedelta(hours=3))
        for p in recent_offline:
            logout_dt = last_logout_map.get(p.player_id)
            if logout_dt and logout_dt.tzinfo is None:
                logout_dt = logout_dt.replace(tzinfo=timezone.utc).astimezone(MSK)
            p.last_session_logout = logout_dt
            if p.last_login_time and p.last_login_time.tzinfo is None:
                p.last_login_time = p.last_login_time.replace(tzinfo=timezone.utc).astimezone(MSK)

    search_results = []
    if search:
        from schemas import PlayerDataOut
        raw_results = PlayerService(db).search_players(search, limit=50)
        search_results = [PlayerDataOut.from_orm_with_mappings(p, settings.ranks_mapping, settings.kits_mapping) for p in raw_results]

    ac_events = svc.get_anticheat_events(limit=200, reviewed=None)
    ac_enriched = [
        {
            "id": ev.id, "player_steamid": ev.player_steamid, "player_name": ev.player_name,
            "player_id": ev.player_id, "event_type": ev.event_type, "details": ev.details,
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None, "reviewed": bool(ev.reviewed),
        }
        for ev in ac_events
    ]

    return {
        "online_players": [_serialize_online_player(p) for p in online_players],
        "online_count": len(online_players),
        "online_servers": online_servers,
        "selected_server": server,
        "round_states": round_states,
        "recent_offline": [_serialize_offline_player(p) for p in recent_offline],
        "search_results": search_results,
        "search_query": search,
        "ac_events": ac_enriched,
        "can_edit": check_permission(current_admin, "moderation", "edit"),
        "can_delete": check_permission(current_admin, "moderation", "delete"),
    }


@panel_router.post("/api/v1/admin/moderation/action")
async def api_enqueue_moderation_action(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """JSON variant of enqueue_moderation_action — same side effects, JSON in/out."""
    body = await request.json()
    action_type = body.get("action_type")

    if not check_permission(current_admin, "moderation", _moderation_action_permission(action_type)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    target_steamid = body.get("target_steamid")
    reason = body.get("reason")
    duration_minutes = body.get("duration_minutes")
    message_text = body.get("message_text")
    server_name = body.get("server_name")

    if action_type not in ("kick", "ban", "unban", "message"):
        raise HTTPException(status_code=400, detail="Invalid action_type")
    if not target_steamid:
        raise HTTPException(status_code=400, detail="target_steamid required")

    from schemas import QueueActionIn
    action_in = QueueActionIn(
        action_type=action_type,
        target_steamid=target_steamid,
        reason=reason or None,
        duration_minutes=duration_minutes or None,
        message_text=message_text or None,
        server_name=server_name.strip().upper() if server_name and server_name.strip() else None,
    )

    svc = ServerModerationService(db)
    action = svc.enqueue_action(action_in, admin_id=current_admin.id)
    logger.info(
        "Admin %s queued %s for %s on server=%s (action_id=%s)",
        current_admin.discord_username, action_type, target_steamid, server_name or "all", action.id,
    )

    if action_type == "ban":
        from models import PlayerData, PlayerPunishment
        from services import _now_msk
        player = db.query(PlayerData).filter(PlayerData.player_steamid == target_steamid).first()
        punishment = PlayerPunishment(
            player_id=player.player_id if player else 0,
            player_steamid=target_steamid,
            player_ip=player.last_ip if player else None,
            type="ban",
            reason=reason or None,
            issued_by_admin_id=current_admin.id,
            issued_by_name=f"ND. {current_admin.discord_username}",
            issued_by_was_superuser=current_admin.is_superuser,
            issued_at=_now_msk(),
            expires_at=None,
            is_active=1,
        )
        db.add(punishment)
        db.commit()
        from routes.game_api import _queue_punishment_discord_notification
        _queue_punishment_discord_notification(db, punishment)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, f"moderation.{action_type}", {
        "target_steamid": target_steamid, "reason": reason, "server_name": server_name, "action_id": action.id,
    })
    return {"status": "ok", "action_id": action.id}


@panel_router.post("/api/v1/admin/moderation/anticheat/{event_id}/review")
async def api_review_anticheat_event(
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "moderation", "manage_anticheat"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    svc = ServerModerationService(db)
    if not svc.mark_reviewed(event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════════════════
# SAURON'S EYE — API endpoints (loaded lazily via AJAX)
# ════════════════════════════════════════════════════════════════════════════

@panel_router.get("/api/v1/sauron/twinks")
async def sauron_twinks(
    request: Request,
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse

    try:
        from task_scheduler import scheduler, Priority
        from workers import task_sauron_twinks
        task_id = "sauron_twinks"
        result = scheduler.get_result(task_id)
        if result and result.status == "done" and result.finished_at:
            from datetime import datetime
            age = (datetime.utcnow() - result.finished_at).total_seconds()
            if age < 600:
                return JSONResponse(result.data.get("results", []))
        scheduler.submit(task_sauron_twinks, priority=Priority.NORMAL, task_id=task_id, timeout=90.0)
        return JSONResponse({"status": "running", "message": "Анализ запущен, обновите через несколько секунд"}, status_code=202)
    except ImportError:
        import services_sauron
        results = services_sauron.detect_all_twink_pairs(db, days=days)
        return JSONResponse(results)


@panel_router.get("/api/v1/sauron/deviant")
async def sauron_deviant(
    request: Request,
    days: int = Query(30, ge=7, le=180),
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse

    try:
        from task_scheduler import scheduler, Priority
        from workers import task_sauron_deviant
        task_id = "sauron_deviant"
        result = scheduler.get_result(task_id)
        if result and result.status == "done" and result.finished_at:
            from datetime import datetime
            age = (datetime.utcnow() - result.finished_at).total_seconds()
            if age < 600:
                return JSONResponse(result.data.get("results", []))
        scheduler.submit(task_sauron_deviant, priority=Priority.NORMAL, task_id=task_id, timeout=90.0)
        return JSONResponse({"status": "running", "message": "Анализ запущен, обновите через несколько секунд"}, status_code=202)
    except ImportError:
        import services_sauron
        results = services_sauron.detect_deviant_behavior(db, days=days, min_incidents=2)
        return JSONResponse(results)


@panel_router.get("/api/v1/sauron/summary")
async def sauron_summary(
    request: Request,
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse

    try:
        from task_scheduler import scheduler, Priority
        from workers import task_sauron_summary
        task_id = "sauron_summary"
        result = scheduler.get_result(task_id)
        if result and result.status == "done" and result.data:
            from datetime import datetime
            age = (datetime.utcnow() - result.finished_at).total_seconds() if result.finished_at else 9999
            if age < 120:
                return JSONResponse(result.data)
        scheduler.submit(task_sauron_summary, priority=Priority.NORMAL, task_id=task_id, timeout=30.0)
        return JSONResponse({"twink_pairs_total": "…", "deviant_total": "…", "twink_high_risk": "…", "deviant_high_risk": "…"})
    except ImportError:
        import services_sauron
        return JSONResponse(services_sauron.get_sauron_summary(db))
