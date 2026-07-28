"""
Dispatch (radio terminal) and Round lifecycle API routes.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List
from datetime import datetime, timedelta
import secrets
import logging
import threading
import httpx

from database import GameSessionLocal, get_db, get_game_db
from security import verify_external_api_key_dependency, verify_client_api_key_dependency, check_permission
from config import get_settings
from models import (
    DispatchMessage, RoundEvent, AdminUser, AdminSession,
    ServerAction, PlayerPunishment, PlayerData, WarehouseItem, AppSetting, WorldRound,
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

# ── External dispatch client API (protected by the SEPARATE client_api_key —
# same tier as world_state.py's client_router, never the game-server ingest
# key or an admin session) — lets a third-party system send cargo/shuttle/
# dispatcher-message requests without a logged-in admin. ────────────────────
client_router = APIRouter(
    prefix="/api/v1/client/dispatch",
    tags=["client-api"],
    dependencies=[Depends(verify_client_api_key_dependency)],
)


# ══════════════════════════════════════════════════════════════════════════════
# DISPATCH API (game server → NexusData)
# ══════════════════════════════════════════════════════════════════════════════

def get_round_state(db: Session, server_name: str) -> dict:
    """Return the current round state for a server, derived from the last
    start/end RoundEvent rows (mirrors the logic used by the moderation status page)."""
    last_start = (
        db.query(RoundEvent)
        .filter(RoundEvent.event_type == "start", RoundEvent.server_name == server_name)
        .order_by(desc(RoundEvent.created_at))
        .first()
    )
    last_end = (
        db.query(RoundEvent)
        .filter(RoundEvent.event_type == "end", RoundEvent.server_name == server_name)
        .order_by(desc(RoundEvent.created_at))
        .first()
    )
    if not last_start:
        return {
            "active": False, "location": None, "submarine_name": None, "mission": None,
            "started_at": None, "players": [], "last_end": last_end,
        }

    is_active = (last_end is None) or (last_start.created_at > last_end.created_at)
    return {
        "active": is_active,
        "location": last_start.location if is_active else None,
        "submarine_name": last_start.submarine_name if is_active else None,
        "mission": last_start.mission if is_active else None,
        "started_at": last_start.created_at if is_active else None,
        "players": last_start.players or [],
        "last_end": last_end,
    }


@api_router.post("/dispatch")
def post_dispatch_message(body: DispatchMessageIn, db: Session = Depends(get_game_db)):
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

    # Outgoing webhook — external dispatch system, if configured for this server
    _send_dispatch_webhook(db, body.server_name, "dispatch_message", {
        "steamid": body.steamid,
        "player_name": body.player_name,
        "text": body.text,
    })
    _queue_ai_dispatcher_request(db, msg)

    return {"status": "ok", "id": msg.id}


def _resolve_dispatch_channel_key(server_name: str) -> Optional[str]:
    """Map a server name to its Discord dispatch channel config key, or None if unresolvable."""
    sn_upper = (server_name or "").upper()
    if "1" in sn_upper or sn_upper == "ASPER1":
        return "dispatch_channel_asper1_id"
    if "2" in sn_upper or sn_upper == "ASPER2":
        return "dispatch_channel_asper2_id"
    return None


def _enqueue_dispatch_embed(db, channel_config_key: str, embed: dict):
    from discord_bot import bot as discord_bot
    coroutine = discord_bot.execute_bot_action("send_embed", {
        "channel_config_key": channel_config_key,
        "embed": embed,
    })
    try:
        discord_bot.schedule(coroutine)
    except Exception as error:
        coroutine.close()
        logger.warning(
            "Dispatch Discord notification skipped for %s: %s",
            channel_config_key,
            error,
        )


def _queue_dispatch_discord_notification(db, msg: DispatchMessage):
    """Send dispatch message to the server-specific Discord channel."""
    server_name = msg.server_name or ""
    channel_config_key = _resolve_dispatch_channel_key(server_name)

    color = 0x3894e0 if msg.sender_type == "dispatcher" else 0xe8e8e8

    if msg.sender_type == "dispatcher":
        sender_label = "📡 Региональный диспетчер"
    else:
        round_state = get_round_state(db, server_name.upper()) if channel_config_key else {"active": False}
        if round_state.get("active") and round_state.get("location"):
            sender_label = f"📻 {round_state['location']}"
        else:
            sender_label = "❓ Неизвестный сектор"

    embed = {
        "description": msg.text,
        "color": color,
        "author": {"name": sender_label},
        "footer": {"text": f"{server_name} · Диспетчерская"},
        "timestamp": msg.created_at.isoformat() if msg.created_at else None,
    }

    if channel_config_key:
        _enqueue_dispatch_embed(db, channel_config_key, embed)
    elif msg.sender_type == "dispatcher":
        # Broadcast dispatcher message (no specific target server) — post to both channels
        _enqueue_dispatch_embed(db, "dispatch_channel_asper1_id", embed)
        _enqueue_dispatch_embed(db, "dispatch_channel_asper2_id", embed)
    # else: unknown server, non-dispatcher message — skip notification


@api_router.get("/dispatch")
def get_dispatch_messages(
    limit: int = Query(50, ge=1, le=200),
    after_id: Optional[int] = Query(None, description="Get messages with id > after_id"),
    db: Session = Depends(get_game_db),
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


@api_router.post("/dispatch/shuttle-departed")
def post_shuttle_departed(body: dict, db: Session = Depends(get_game_db)):
    """Game server reports that the supply shuttle has actually departed
    (Harmony postfix on RespawnManager.DispatchShuttle in RoundManager_ND.cs —
    fires for every real dispatch, forced or vanilla). Relayed to the
    external dispatch webhook as 'shuttle_departed', if configured."""
    server_name = (body or {}).get("server_name")
    _send_dispatch_webhook(db, server_name, "shuttle_departed", {})
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# ROUND LIFECYCLE API
# ══════════════════════════════════════════════════════════════════════════════

@api_router.post("/round/start")
def post_round_start(body: RoundStartIn, db: Session = Depends(get_game_db)):
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
    _queue_round_status_refresh(db, body.server_name)
    return {"status": "ok", "round_id": event.id}


@api_router.post("/round/end")
def post_round_end(body: RoundEndIn, db: Session = Depends(get_game_db)):
    """Game server reports round end."""
    # Link back to the start event that opened this round (the most recent
    # unpaired "start" for this server) so past rounds can be listed in one query.
    paired_start = (
        db.query(RoundEvent)
        .filter(
            RoundEvent.server_name == body.server_name,
            RoundEvent.event_type == "start",
            ~RoundEvent.id.in_(
                db.query(RoundEvent.paired_start_id).filter(RoundEvent.paired_start_id.isnot(None))
            ),
        )
        .order_by(RoundEvent.created_at.desc())
        .first()
    )
    event = RoundEvent(
        event_type="end",
        server_name=body.server_name,
        mission_completed=1 if body.mission_completed else 0,
        reached_station=1 if body.reached_station else 0,
        players=body.players,
        paired_start_id=paired_start.id if paired_start else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info(f"Round end: {body.server_name} | mission={body.mission_completed} | station={body.reached_station}")
    _queue_round_status_refresh(db, body.server_name)
    return {"status": "ok", "round_id": event.id}


def _queue_round_status_refresh(db, server_name: Optional[str]):
    """Ask the bot to immediately refresh the round-status embed for this server
    (in addition to its regular 1-minute loop), so start/end feels instant."""
    if not server_name:
        return
    from discord_bot import bot as discord_bot
    discord_bot.schedule(discord_bot.execute_bot_action("refresh_round_status", {"server_name": server_name}))


# ══════════════════════════════════════════════════════════════════════════════
# EXTERNAL DISPATCH WEBHOOK (NexusData → third-party system)
#
# One outgoing webhook URL per server (ASPER1/ASPER2), superuser-configurable
# via PATCH /api/v1/admin/dispatch/webhook-config, stored in AppSetting (same
# key/value pattern as "rank_colors" in routes/admin.py). Fires for three
# events: a player's radio message reaching dispatch (dispatch_message), a
# supply-shuttle send being queued (shuttle_send_requested), and the game
# engine confirming the shuttle actually departed (shuttle_departed, posted
# by the plugin — see /server/dispatch/shuttle-departed below).
# ══════════════════════════════════════════════════════════════════════════════

def _dispatch_webhook_setting_key(server_name: Optional[str]) -> Optional[str]:
    sn = (server_name or "").strip().upper()
    if sn == "ASPER1":
        return "dispatch_webhook_url_asper1"
    if sn == "ASPER2":
        return "dispatch_webhook_url_asper2"
    return None


def _get_app_setting(db: Session, key: str) -> Optional[str]:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row and row.value else None


def _set_app_setting(db: Session, key: str, value: Optional[str]):
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def _post_webhook_sync(url: str, payload: dict):
    """Runs in its own daemon thread — never blocks the calling request, no
    retries (best-effort). A dedicated thread (rather than asyncio.create_task)
    is used because this is called from BOTH sync and async route handlers in
    this file, and create_task requires a running event loop that plain sync
    def handlers (run in FastAPI's worker thread pool) don't have."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning(f"Dispatch webhook {url} returned {resp.status_code}: {resp.text[:300]}")
    except Exception as ex:
        logger.warning(f"Dispatch webhook POST to {url} failed: {ex}")


def _send_dispatch_webhook(db: Session, server_name: Optional[str], event: str, payload: dict):
    """Best-effort outgoing notification — no-op if no URL is configured for
    this server. Fire-and-forget, never raises, never blocks the caller."""
    key = _dispatch_webhook_setting_key(server_name)
    if not key:
        return
    url = _get_app_setting(db, key)
    if not url:
        return
    body = {
        "event": event,
        "server_name": (server_name or "").strip().upper(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        **payload,
    }
    threading.Thread(target=_post_webhook_sync, args=(url, body), daemon=True).start()


def _queue_ai_dispatcher_request(db: Session, msg: DispatchMessage) -> None:
    """Forward a player's radio message to AsperAIDispatcher in the background.

    The game-server ingest request must remain fast and successful even when the
    AI service is unavailable. The AI performs its own callsign filtering; an
    ignored response produces no game action.
    """
    url = (settings.ai_dispatcher_url or "").strip()
    api_key = (settings.ai_dispatcher_api_key or "").strip()
    server_name = (msg.server_name or "").strip().upper()
    if not url or not api_key:
        return
    if server_name not in {"ASPER1", "ASPER2"}:
        logger.warning(
            "AI dispatcher skipped message %s: unsupported server_name=%r",
            msg.id,
            msg.server_name,
        )
        return

    threshold = datetime.utcnow() - timedelta(seconds=15)
    active_round = (
        db.query(WorldRound)
        .filter(
            WorldRound.server_name == server_name,
            WorldRound.last_update_at >= threshold,
        )
        .order_by(WorldRound.last_update_at.desc())
        .first()
    )
    round_id = active_round.round_id if active_round else f"dispatch-{server_name}-{msg.id}"
    payload = {
        "event_id": f"nexus-dispatch-{msg.id}",
        "round_id": round_id,
        "server_name": server_name,
        "text": msg.text,
        "sender_name": msg.player_name,
    }
    threading.Thread(
        target=_post_ai_dispatcher_sync,
        args=(url, api_key, payload, server_name),
        daemon=True,
        name=f"AIDispatch-{msg.id}",
    ).start()


def _post_ai_dispatcher_sync(
    url: str,
    api_key: str,
    payload: dict,
    server_name: str,
) -> None:
    try:
        with httpx.Client(timeout=settings.ai_dispatcher_timeout_seconds) as client:
            response = client.post(
                url,
                headers={"X-API-Key": api_key},
                json=payload,
            )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "answered":
            return
        reply = result.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            logger.warning("AI dispatcher returned an empty reply for %s", payload["event_id"])
            return

        callback_db = GameSessionLocal()
        try:
            _send_dispatcher_message(
                callback_db,
                reply,
                server_name,
                admin_id=None,
                sender_label="ИИ-диспетчер",
            )
        finally:
            callback_db.close()
    except Exception as error:
        logger.warning(
            "AI dispatcher request failed for %s: %s",
            payload.get("event_id"),
            error,
        )


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
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


@panel_router.get("/adminold/dispatch", response_class=HTMLResponse)
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


def _send_dispatcher_message(db: Session, text: str, server_name: Optional[str], admin_id: Optional[int], sender_label: str) -> tuple:
    """Shared by the admin panel endpoint and the external client endpoint.

    Creates a 'dispatch' ServerAction. The game server picks it up, sends it
    in-game, then ACKs. Until ACK is received, the action stays pending and
    the admin-panel frontend blocks further sends (see /dispatch/pending —
    this only checks action_type=="dispatch" regardless of who queued it).
    """
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    full_text = f"[Диспетчер] {text}"

    # Save to dispatch history
    msg = DispatchMessage(
        steamid=None,
        player_name=sender_label,
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
        queued_by_admin_id=admin_id,
    )
    # If target_server is set, mark the OTHER server as done (not applicable)
    if server_name and server_name.upper() == "ASPER1":
        action.status_asper2 = 1
    elif server_name and server_name.upper() == "ASPER2":
        action.status_asper1 = 1
    db.add(action)
    db.commit()
    db.refresh(action)
    db.refresh(msg)

    # Mirror the dispatcher message into the corresponding Discord dispatch channel(s)
    _queue_dispatch_discord_notification(db, msg)

    return msg, action


@panel_router.post("/api/v1/admin/dispatch/send")
async def dispatch_send(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Admin sends a dispatcher message via the action queue."""
    if not check_permission(current_admin, "dispatch", "edit"):
        raise HTTPException(status_code=403)
    body = await request.json()
    server_name = body.get("server_name") or None  # optional: target specific server
    msg, action = _send_dispatcher_message(db, body.get("text", ""), server_name, current_admin.id, current_admin.discord_username)
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


@panel_router.get("/api/v1/admin/dispatch/webhook-config")
def get_dispatch_webhook_config(db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Superuser-only: current outgoing webhook URLs (per server) for the
    external dispatch integration. Not gated by the 'dispatch' permission
    registry — this is a site-wide integration secret, superuser only."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")
    return {
        "ASPER1": _get_app_setting(db, "dispatch_webhook_url_asper1"),
        "ASPER2": _get_app_setting(db, "dispatch_webhook_url_asper2"),
    }


@panel_router.patch("/api/v1/admin/dispatch/webhook-config")
async def set_dispatch_webhook_config(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Superuser-only: set/clear the outgoing webhook URL for ASPER1/ASPER2.
    Body: {"ASPER1": "https://..."|null, "ASPER2": "https://..."|null} —
    keys are optional, only provided ones are updated."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")
    body = await request.json()

    if "ASPER1" in body:
        url = (body.get("ASPER1") or "").strip() or None
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")
        _set_app_setting(db, "dispatch_webhook_url_asper1", url)
    if "ASPER2" in body:
        url = (body.get("ASPER2") or "").strip() or None
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")
        _set_app_setting(db, "dispatch_webhook_url_asper2", url)

    db.commit()
    return {
        "ASPER1": _get_app_setting(db, "dispatch_webhook_url_asper1"),
        "ASPER2": _get_app_setting(db, "dispatch_webhook_url_asper2"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Dropdown actions ("Отправить/отозвать челнок", "Груз в очередь", "Торпедный
# удар") — all queue a ServerAction the game server's own poll loop already
# consumes (GET /api/v1/actions, see routes/moderation.py) the same way it
# already does for kick/ban/dispatch. This side only ever queues a correctly
# structured action + (for cargo) deducts warehouse stock — actually spawning
# the cargo/moving the shuttle/firing the torpedo is the game server's job.
# ══════════════════════════════════════════════════════════════════════════════

def _mark_other_server_done(action: ServerAction, server_name: Optional[str]):
    """Single-server-targeted actions don't need the OTHER server to do
    anything — same convention as dispatch_send above."""
    if server_name and server_name.upper() == "ASPER1":
        action.status_asper2 = 1
    elif server_name and server_name.upper() == "ASPER2":
        action.status_asper1 = 1


def _queue_shuttle_action(db: Session, server_name: Optional[str], command: str, admin_id: Optional[int]) -> ServerAction:
    """Shared by the admin panel endpoint and the external client endpoint.
    Queues supply_shuttle_send/recall and — for 'send' only — fires the
    'shuttle_send_requested' outgoing webhook (recall has no webhook event
    per the current spec)."""
    if command not in ("send", "recall"):
        raise HTTPException(status_code=400, detail="command должен быть send или recall")
    if not server_name:
        raise HTTPException(status_code=400, detail="Укажите сервер")

    action = ServerAction(
        action_type=f"supply_shuttle_{command}",
        target_steamid="system",
        target_server=server_name,
        queued_by_admin_id=admin_id,
    )
    _mark_other_server_done(action, server_name)
    db.add(action)
    db.commit()
    db.refresh(action)

    if command == "send":
        _send_dispatch_webhook(db, server_name, "shuttle_send_requested", {})

    return action


@panel_router.post("/api/v1/admin/dispatch/actions/shuttle")
async def dispatch_shuttle_action(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    if not check_permission(current_admin, "dispatch", "edit"):
        raise HTTPException(status_code=403)
    body = await request.json()
    command = body.get("command")
    server_name = (body.get("server_name") or "").strip() or None
    action = _queue_shuttle_action(db, server_name, command, current_admin.id)
    return JSONResponse({"status": "ok", "action_id": action.id})


@panel_router.post("/api/v1/admin/dispatch/actions/torpedo")
async def dispatch_torpedo_action(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    if not check_permission(current_admin, "dispatch", "edit"):
        raise HTTPException(status_code=403)
    body = await request.json()
    server_name = (body.get("server_name") or "").strip() or None
    x, y = body.get("x"), body.get("y")
    if not server_name:
        raise HTTPException(status_code=400, detail="Укажите сервер")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise HTTPException(status_code=400, detail="Укажите координаты цели (x, y)")

    action = ServerAction(
        action_type="torpedo_strike",
        target_steamid="system",
        target_server=server_name,
        payload={"x": x, "y": y},
        queued_by_admin_id=current_admin.id,
    )
    _mark_other_server_done(action, server_name)
    db.add(action)
    db.commit()
    db.refresh(action)
    return JSONResponse({"status": "ok", "action_id": action.id})


def _queue_cargo_action(db: Session, server_name: Optional[str], items: list, admin_id: Optional[int]) -> ServerAction:
    """Shared by the admin panel endpoint and the external client endpoint.
    Requested items are DEDUCTED from the virtual warehouse (routes/warehouse.py)
    atomically with queuing the action — if stock is insufficient for anything
    requested, the whole request is rejected and nothing is deducted or queued."""
    if not server_name:
        raise HTTPException(status_code=400, detail="Укажите сервер")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один предмет")

    to_deduct = []
    for entry in items:
        identifier = (entry.get("identifier") or "").strip() if isinstance(entry, dict) else ""
        qty = entry.get("quantity") if isinstance(entry, dict) else None
        if not identifier or not isinstance(qty, int) or qty <= 0:
            raise HTTPException(status_code=400, detail="Некорректный предмет в грузе")
        row = db.query(WarehouseItem).filter(WarehouseItem.identifier == identifier).first()
        if not row or row.quantity < qty:
            available = row.quantity if row else 0
            raise HTTPException(status_code=400, detail=f"На складе недостаточно «{identifier}» (нужно {qty}, есть {available})")
        to_deduct.append((row, qty))

    for row, qty in to_deduct:
        row.quantity -= qty
        row.updated_by_admin_id = admin_id

    action = ServerAction(
        action_type="cargo_spawn",
        target_steamid="system",
        target_server=server_name,
        payload={"items": [{"identifier": e["identifier"], "quantity": e["quantity"]} for e in items]},
        queued_by_admin_id=admin_id,
    )
    _mark_other_server_done(action, server_name)
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


@panel_router.post("/api/v1/admin/dispatch/actions/cargo")
async def dispatch_cargo_action(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    if not check_permission(current_admin, "dispatch", "edit"):
        raise HTTPException(status_code=403)
    body = await request.json()
    server_name = (body.get("server_name") or "").strip() or None
    items = body.get("items") or []
    action = _queue_cargo_action(db, server_name, items, current_admin.id)
    return JSONResponse({"status": "ok", "action_id": action.id})


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

    # Overlay forced_display_name (admin-assigned nickname) onto the stored
    # per-message sender name, same fallback convention used elsewhere
    # (forced_display_name or the real name) — see routes/moderation.py.
    steamids = {m.steamid for m in messages if m.steamid}
    forced_map = {}
    if steamids:
        rows = (
            db.query(PlayerData.player_steamid, PlayerData.forced_display_name)
            .filter(PlayerData.player_steamid.in_(steamids), PlayerData.forced_display_name.isnot(None))
            .all()
        )
        forced_map = {sid: name for sid, name in rows if name}

    return [
        {
            "id": m.id,
            "steamid": m.steamid,
            "player_name": (forced_map.get(m.steamid) or m.player_name) if m.steamid else m.player_name,
            "real_name": m.player_name if m.steamid and forced_map.get(m.steamid) else None,
            "text": m.text,
            "sender_type": m.sender_type,
            "server_name": m.server_name,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@panel_router.get("/api/v1/admin/round-status")
def admin_round_status(db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Round/location/ship/mission/player-count/shuttle-ETA for both servers — for the
    React SPA's 'Статус раунда' overview page. Auth via admin session, not the game-server API key."""
    from services import ServerModerationService
    from models import ServerLiveState

    svc = ServerModerationService(db)
    result = {}
    for sn in ["ASPER1", "ASPER2"]:
        round_state = get_round_state(db, sn)
        online_players = svc.get_online_players(server_name=sn)
        live = db.query(ServerLiveState).filter(ServerLiveState.server_name == sn).first()
        result[sn] = {
            "active": round_state["active"],
            "location": round_state["location"],
            "submarine_name": round_state["submarine_name"],
            "mission": round_state["mission"],
            "started_at": round_state["started_at"].isoformat() if round_state["started_at"] else None,
            "player_count": len(online_players),
            "submarine_health": live.submarine_health if live else None,
            "shuttle_arrival_at": live.shuttle_arrival_at.isoformat() + "Z" if live and live.shuttle_arrival_at else None,
        }
    return result


# ══════════════════════════════════════════════════════════════════════════════
# EXTERNAL DISPATCH CLIENT API (client_api_key — no admin session required)
#
# Mirrors the three admin-panel dispatch actions above (cargo/shuttle/message —
# torpedo is intentionally not exposed here), so a third-party system can
# drive dispatch the same way the web UI does. queued_by_admin_id is always
# None here (no admin is involved), same convention as game-server-initiated
# actions elsewhere (e.g. routes/game_api.py's traps/queue, unban).
# ══════════════════════════════════════════════════════════════════════════════

@client_router.post("/cargo")
async def client_dispatch_cargo(request: Request, db: Session = Depends(get_game_db)):
    body = await request.json()
    server_name = (body.get("server_name") or "").strip() or None
    items = body.get("items") or []
    action = _queue_cargo_action(db, server_name, items, admin_id=None)
    return {"status": "ok", "action_id": action.id}


@client_router.post("/shuttle")
async def client_dispatch_shuttle(request: Request, db: Session = Depends(get_game_db)):
    body = await request.json()
    server_name = (body.get("server_name") or "").strip() or None
    command = body.get("command")
    action = _queue_shuttle_action(db, server_name, command, admin_id=None)
    return {"status": "ok", "action_id": action.id}


@client_router.post("/message")
async def client_dispatch_message(request: Request, db: Session = Depends(get_game_db)):
    body = await request.json()
    server_name = (body.get("server_name") or "").strip() or None
    sender_label = (body.get("sender_name") or "").strip() or "Внешняя система"
    msg, action = _send_dispatcher_message(db, body.get("text", ""), server_name, admin_id=None, sender_label=sender_label)
    return {"status": "ok", "id": msg.id, "action_id": action.id}
