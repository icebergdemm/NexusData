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
from datetime import datetime

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
    db: Session = Depends(get_db),
):
    """Replace the full snapshot of currently online players."""
    svc = ServerModerationService(db)
    count = svc.replace_online_players(report.players)
    return ServerPlayersReportResult(status="ok", count=count)


@api_router.post("/anticheat", response_model=AnticheatEventOut)
def report_anticheat_event(
    event: AnticheatEventIn,
    db: Session = Depends(get_db),
):
    """Record a single anticheat trigger from the game server."""
    svc = ServerModerationService(db)
    ev = svc.add_anticheat_event(event)
    return ev


@api_router.get("/actions", response_model=List[ServerActionOut])
def get_pending_actions(db: Session = Depends(get_db)):
    """Return all pending moderation actions for the game server to execute."""
    svc = ServerModerationService(db)
    return svc.get_pending_actions()


@api_router.post("/actions/{action_id}/ack")
def acknowledge_action(
    action_id: int,
    ack: ServerActionAck,
    db: Session = Depends(get_db),
):
    """Acknowledge that the game server executed (or failed) a queued action."""
    svc = ServerModerationService(db)
    ok = svc.acknowledge_action(action_id, ack.success, ack.error_message)
    if not ok:
        raise HTTPException(status_code=404, detail="Action not found")
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL ROUTER  (HTML pages, protected by admin session)
# ════════════════════════════════════════════════════════════════════════════

panel_router = APIRouter()


@panel_router.get("/admin/moderation", response_class=HTMLResponse)
async def moderation_page(
    request: Request,
    tab: str = Query("status", pattern=r"^(status|banhammer|logs|anticheat)$"),
    search: Optional[str] = Query(None, min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Main server moderation page with four tabs."""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = ServerModerationService(db)

    online_players = svc.get_online_players()
    online_count = len(online_players)

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
        "recent_offline": recent_offline,
        "search_results": search_results,
        "search_query": search,
        "ac_events": ac_enriched,
        "fmt_playtime": _fmt_playtime,
        "csrf_token": request.session.get("csrf_token"),
    })


@panel_router.post("/admin/moderation/action")
async def enqueue_moderation_action(
    request: Request,
    action_type: str = Form(...),
    target_steamid: str = Form(...),
    reason: Optional[str] = Form(None),
    duration_minutes: Optional[int] = Form(None),
    message_text: Optional[str] = Form(None),
    redirect_tab: str = Form("status"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Enqueue a kick / ban / message action from the admin panel."""
    if not check_permission(current_admin, "moderation", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    await ensure_csrf(request, db)

    if action_type not in ("kick", "ban", "message"):
        raise HTTPException(status_code=400, detail="Invalid action_type")

    from schemas import QueueActionIn
    action_in = QueueActionIn(
        action_type=action_type,
        target_steamid=target_steamid,
        reason=reason or None,
        duration_minutes=duration_minutes or None,
        message_text=message_text or None,
    )

    svc = ServerModerationService(db)
    action = svc.enqueue_action(action_in, admin_id=current_admin.id)
    logger.info(
        "Admin %s queued %s for %s (action_id=%s)",
        current_admin.discord_username, action_type, target_steamid, action.id,
    )

    from routes.admin import log_admin_action, push_flash
    log_admin_action(db, current_admin.id, f"moderation.{action_type}", {
        "target_steamid": target_steamid,
        "reason": reason,
        "action_id": action.id,
    })
    push_flash(request, "success", f"Действие «{action_type}» поставлено в очередь для {target_steamid}")
    return RedirectResponse(url=f"/admin/moderation?tab={redirect_tab}", status_code=302)


@panel_router.post("/admin/moderation/anticheat/{event_id}/review")
async def review_anticheat_event(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Mark an anticheat event as reviewed."""
    await ensure_csrf(request, db)
    svc = ServerModerationService(db)
    if not svc.mark_reviewed(event_id):
        raise HTTPException(status_code=404, detail="Event not found")

    from routes.admin import push_flash
    push_flash(request, "success", "Событие отмечено как проверенное")
    return RedirectResponse(url="/admin/moderation?tab=anticheat", status_code=302)
