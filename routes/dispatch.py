"""
Dispatch (radio terminal) and Round lifecycle API routes.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List
from datetime import datetime
import secrets
import logging

from database import get_db
from security import verify_external_api_key_dependency, check_permission
from config import get_settings
from models import (
    DispatchMessage, RoundEvent, AdminUser, AdminSession,
    ServerAction, PlayerPunishment, PlayerData, DiscordBotAction,
)
from schemas import DispatchMessageIn, RoundStartIn, RoundEndIn

settings = get_settings()
logger = logging.getLogger(__name__)

# ── Game server API (protected by API key) ───────────────────────────────────
api_router = APIRouter(
    prefix="/api/v1/server",
    tags=["dispatch-rounds"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# ── Admin panel router ───────────────────────────────────────────────────────
panel_router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)


# ══════════════════════════════════════════════════════════════════════════════
# DISPATCH API (game server → NexusData)
# ══════════════════════════════════════════════════════════════════════════════

@api_router.post("/dispatch")
def post_dispatch_message(body: DispatchMessageIn, db: Session = Depends(get_db)):
    """Game server posts a radio terminal message from a player."""
    msg = DispatchMessage(
        steamid=body.steamid,
        player_name=body.player_name,
        text=body.text,
        sender_type="player",
        server_name=body.server_name,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # Queue Discord notification for dispatch messages
    _queue_dispatch_discord_notification(db, msg)

    return {"status": "ok", "id": msg.id}


def _queue_dispatch_discord_notification(db, msg: DispatchMessage):
    """Send dispatch message to the server-specific Discord channel."""
    server_name = msg.server_name or ""
    sn_upper = server_name.upper()

    # Map server name to config key
    if "1" in sn_upper or sn_upper == "ASPER1":
        channel_config_key = "dispatch_channel_asper1_id"
    elif "2" in sn_upper or sn_upper == "ASPER2":
        channel_config_key = "dispatch_channel_asper2_id"
    else:
        # Unknown server — skip notification
        return

    color = 0x3894e0 if msg.sender_type == "dispatcher" else 0xe8e8e8
    sender_label = "🎙️ Диспетчер" if msg.sender_type == "dispatcher" else f"📻 {msg.player_name or 'Игрок'}"

    embed = {
        "description": msg.text,
        "color": color,
        "author": {"name": sender_label},
        "footer": {"text": f"{server_name} · Диспетчерская"},
        "timestamp": msg.created_at.isoformat() if msg.created_at else None,
    }

    action = DiscordBotAction(
        action_type="send_embed",
        payload={
            "channel_config_key": channel_config_key,
            "embed": embed,
        },
    )
    db.add(action)
    db.commit()


@api_router.get("/dispatch")
def get_dispatch_messages(
    limit: int = Query(50, ge=1, le=200),
    after_id: Optional[int] = Query(None, description="Get messages with id > after_id"),
    db: Session = Depends(get_db),
):
    """Get recent dispatch messages (for polling)."""
    q = db.query(DispatchMessage)
    if after_id:
        q = q.filter(DispatchMessage.id > after_id)
    messages = q.order_by(desc(DispatchMessage.created_at)).limit(limit).all()
    messages.reverse()  # Oldest first
    return [
        {
            "id": m.id,
            "steamid": m.steamid,
            "player_name": m.player_name,
            "text": m.text,
            "sender_type": m.sender_type,
            "server_name": m.server_name,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


# ══════════════════════════════════════════════════════════════════════════════
# ROUND LIFECYCLE API
# ══════════════════════════════════════════════════════════════════════════════

@api_router.post("/round/start")
def post_round_start(body: RoundStartIn, db: Session = Depends(get_db)):
    """Game server reports round start."""
    event = RoundEvent(
        event_type="start",
        server_name=body.server_name,
        submarine_name=body.submarine_name,
        mission=body.mission,
        location=body.location,
        players=body.players,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info(f"Round start: {body.server_name} | {body.submarine_name} | {len(body.players)} players")
    return {"status": "ok", "round_id": event.id}


@api_router.post("/round/end")
def post_round_end(body: RoundEndIn, db: Session = Depends(get_db)):
    """Game server reports round end."""
    event = RoundEvent(
        event_type="end",
        server_name=body.server_name,
        mission_completed=1 if body.mission_completed else 0,
        reached_station=1 if body.reached_station else 0,
        players=body.players,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info(f"Round end: {body.server_name} | mission={body.mission_completed} | station={body.reached_station}")
    return {"status": "ok", "round_id": event.id}


# ══════════════════════════════════════════════════════════════════════════════
# DISPATCH ADMIN PANEL (send dispatcher messages from web)
# ══════════════════════════════════════════════════════════════════════════════

def _get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
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


@panel_router.get("/admin/dispatch", response_class=HTMLResponse)
async def dispatch_page(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Dispatch chat page."""
    if not check_permission(current_admin, "dispatch", "view"):
        raise HTTPException(status_code=403)
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return templates.TemplateResponse("admin/dispatch.html", {
        "request": request,
        "admin": current_admin,
    })


@panel_router.post("/api/v1/admin/dispatch/send")
async def dispatch_send(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Admin sends a dispatcher message via the action queue.
    
    Creates a 'dispatch' action in ServerActionQueue. The game server picks it up,
    sends it in-game, then ACKs. Until ACK is received, the action stays pending
    and the frontend blocks further sends.
    """
    if not check_permission(current_admin, "dispatch", "edit"):
        raise HTTPException(status_code=403)
    body = await request.json()
    text = body.get("text", "").strip()
    server_name = body.get("server_name") or None  # optional: target specific server
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    full_text = f"[Диспетчер] {text}"

    # Save to dispatch history
    msg = DispatchMessage(
        steamid=None,
        player_name=current_admin.discord_username,
        text=full_text,
        sender_type="dispatcher",
        server_name=server_name,
    )
    db.add(msg)
    db.flush()

    # Queue as server action (game server will deliver in-game and ACK)
    action = ServerAction(
        action_type="dispatch",
        target_steamid="broadcast",
        message_text=full_text,
        target_server=server_name,  # Send to specific server only
        queued_by_admin_id=current_admin.id,
    )
    # If target_server is set, mark the OTHER server as done (not applicable)
    if server_name and server_name.upper() == "ASPER1":
        action.status_asper2 = 1
    elif server_name and server_name.upper() == "ASPER2":
        action.status_asper1 = 1
    db.add(action)
    db.commit()
    db.refresh(action)

    return JSONResponse({"status": "ok", "id": msg.id, "action_id": action.id})


@panel_router.get("/api/v1/admin/dispatch/pending")
def dispatch_check_pending(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(_get_current_admin),
):
    """Check if there's a pending dispatch action (not yet ACKed by server).
    Frontend uses this to block/unblock the send button.
    """
    if not check_permission(current_admin, "dispatch", "view"):
        raise HTTPException(status_code=403)
    pending = db.query(ServerAction).filter(
        ServerAction.action_type == "dispatch",
        (ServerAction.status_asper1 == 0) | (ServerAction.status_asper2 == 0),
    ).first()
    return {"pending": pending is not None, "action_id": pending.id if pending else None}


@panel_router.get("/api/v1/admin/dispatch/messages")
def dispatch_get_messages(
    after_id: Optional[int] = Query(None),
    server: Optional[str] = Query(None),
    limit: int = Query(50),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(_get_current_admin),
):
    """Get dispatch messages for the admin panel, filtered by server."""
    if not check_permission(current_admin, "dispatch", "view"):
        raise HTTPException(status_code=403)
    q = db.query(DispatchMessage)
    if after_id:
        q = q.filter(DispatchMessage.id > after_id)
    if server:
        from sqlalchemy import or_
        q = q.filter(or_(
            DispatchMessage.server_name == server,
            DispatchMessage.server_name.is_(None),  # dispatcher messages (from web) show in both
        ))
    messages = q.order_by(desc(DispatchMessage.created_at)).limit(limit).all()
    messages.reverse()
    return [
        {
            "id": m.id,
            "steamid": m.steamid,
            "player_name": m.player_name,
            "text": m.text,
            "sender_type": m.sender_type,
            "server_name": m.server_name,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]
