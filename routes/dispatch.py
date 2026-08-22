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
from dataclasses import dataclass, field
from contextlib import contextmanager
from queue import Full, Queue
import secrets
import logging
import os
import re
import threading
import time
import httpx

try:
    import fcntl
except ImportError:  # pragma: no cover - production runs on Linux
    fcntl = None

from database import GameSessionLocal, get_db, get_game_db
from security import verify_external_api_key_dependency, verify_client_api_key_dependency, check_permission
from config import get_settings
from models import (
    DispatchMessage, RoundEvent, AdminUser, AdminSession, AICreatureAlert,
    ServerAction, PlayerPunishment, PlayerData, WarehouseItem, AppSetting, WorldRound,
)
from schemas import DispatchMessageIn, RoundStartIn, RoundEndIn
from services_creature_alerts import is_configured_large_species

settings = get_settings()
logger = logging.getLogger(__name__)

_DISPATCHER_PREFIX = "[Диспетчер] "
_BAROTRAUMA_RADIO_MAX_CHARS = 200
_AI_REPLY_MAX_CHARS = _BAROTRAUMA_RADIO_MAX_CHARS - len(_DISPATCHER_PREFIX)
_AI_REPLY_MIN_DELAY_SECONDS = 4.0
_AI_REPLY_MAX_DELAY_SECONDS = 12.0
_AI_ACTION_ACK_POLL_SECONDS = 1.0
_AI_CHANNEL_WAIT_TIMEOUT_SECONDS = 90.0
_AI_ACTION_ACK_TIMEOUT_SECONDS = 90.0
_AI_DISPATCH_QUEUE_MAXSIZE = 100
_AI_DISPATCH_JOB_TTL_SECONDS = 180.0
_AI_BRIEFING_MIN_DELAY_SECONDS = 15
_AI_BRIEFING_MAX_DELAY_SECONDS = 35
_AI_BRIEFING_RETRY_DELAY_SECONDS = 8.0
_AI_BRIEFING_MAX_ATTEMPTS = 10
_AI_CREATURE_ALERT_RETRY_DELAY_SECONDS = 5.0
_AI_CREATURE_ALERT_MAX_ATTEMPTS = 3
_AI_BRIEFING_LIFECYCLE_MATCH_SECONDS = 180.0
_AI_MAX_REPLY_PARTS = 8
_AI_PROCESS_LOCK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))


@dataclass(frozen=True)
class _AIDispatchJob:
    url: str
    api_key: str
    payload: dict
    server_name: str
    initial_delay_seconds: float = 0.0
    retry_delay_seconds: float = 0.0
    max_attempts: int = 1
    creature_alert_ids: tuple[int, ...] = ()
    enqueued_at: float = field(default_factory=time.monotonic)


# A dedicated FIFO per game server keeps concurrent radio calls in their
# arrival order while allowing ASPER1 and ASPER2 to work independently.  The
# workers are created lazily and never run on FastAPI's request thread.
_ai_dispatch_job_queues: dict[str, Queue] = {}
_ai_dispatch_job_queues_lock = threading.Lock()
_dispatch_action_insert_lock = threading.RLock()

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
    server_name = (body.server_name or "").strip().upper() or None
    event = RoundEvent(
        event_type="start",
        server_name=server_name,
        submarine_name=body.submarine_name,
        mission=body.mission,
        location=body.location,
        players=body.players,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info(f"Round start: {server_name} | {body.submarine_name} | {len(body.players)} players")
    _queue_round_status_refresh(db, server_name)
    _queue_ai_round_briefing(event, db)
    return {"status": "ok", "round_id": event.id}


@api_router.post("/round/end")
def post_round_end(body: RoundEndIn, db: Session = Depends(get_game_db)):
    """Game server reports round end."""
    server_name = (body.server_name or "").strip().upper() or None
    # Link back to the start event that opened this round (the most recent
    # unpaired "start" for this server) so past rounds can be listed in one query.
    paired_start = (
        db.query(RoundEvent)
        .filter(
            RoundEvent.server_name == server_name,
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
        server_name=server_name,
        mission_completed=1 if body.mission_completed else 0,
        reached_station=1 if body.reached_station else 0,
        players=body.players,
        paired_start_id=paired_start.id if paired_start else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info(f"Round end: {body.server_name} | mission={body.mission_completed} | station={body.reached_station}")
    _queue_round_status_refresh(db, server_name)
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

    round_id = _resolve_ai_conversation_round_id(db, server_name)
    payload = {
        "event_id": f"nexus-dispatch-{msg.id}",
        "round_id": round_id,
        "server_name": server_name,
        "text": msg.text,
        "sender_name": msg.player_name,
    }
    _enqueue_ai_dispatch_job(
        _AIDispatchJob(
            url=url,
            api_key=api_key,
            payload=payload,
            server_name=server_name,
        )
    )


def _random_ai_briefing_delay_seconds() -> float:
    """Choose a human-looking delay within the configured inclusive window."""

    span = _AI_BRIEFING_MAX_DELAY_SECONDS - _AI_BRIEFING_MIN_DELAY_SECONDS + 1
    return float(_AI_BRIEFING_MIN_DELAY_SECONDS + secrets.randbelow(span))


def _world_round_matches_lifecycle_event(
    world_round: WorldRound,
    event: RoundEvent,
) -> bool:
    if (world_round.server_name or "").strip().upper() != (
        event.server_name or ""
    ).strip().upper():
        return False
    world_started_at = world_round.created_at or world_round.last_update_at
    if world_started_at is None or event.created_at is None:
        return False
    return (
        abs((world_started_at - event.created_at).total_seconds())
        <= _AI_BRIEFING_LIFECYCLE_MATCH_SECONDS
    )


def _claim_world_briefing(db: Session, world_round: WorldRound) -> bool:
    """Atomically claim one world round across all web workers."""

    if world_round.id is None:
        return False
    claimed = (
        db.query(WorldRound)
        .filter(
            WorldRound.id == world_round.id,
            WorldRound.ai_briefing_queued_at.is_(None),
        )
        .update(
            {WorldRound.ai_briefing_queued_at: datetime.utcnow()},
            synchronize_session=False,
        )
    )
    db.commit()
    if claimed:
        db.refresh(world_round)
    return claimed == 1


def _latest_world_round(db: Session, server_name: str) -> Optional[WorldRound]:
    return (
        db.query(WorldRound)
        .filter(WorldRound.server_name == server_name)
        .order_by(WorldRound.last_update_at.desc(), WorldRound.id.desc())
        .first()
    )


def _queue_ai_round_briefing(
    event: RoundEvent,
    db: Optional[Session] = None,
) -> None:
    """Поставить первичный доклад после начала выхода в ту же FIFO, что и ответы."""

    url = (settings.ai_dispatcher_url or "").strip()
    api_key = (settings.ai_dispatcher_api_key or "").strip()
    server_name = (event.server_name or "").strip().upper()
    if not url or not api_key:
        logger.warning(
            "AI round briefing skipped: dispatcher bridge is not configured; "
            "round_event_id=%s server=%r",
            event.id,
            event.server_name,
        )
        return
    if server_name not in {"ASPER1", "ASPER2"}:
        logger.warning(
            "AI round briefing skipped: unsupported server_name=%r round_event_id=%s",
            event.server_name,
            event.id,
        )
        return

    if db is not None:
        world_round = _latest_world_round(db, server_name)
        if world_round is not None and _world_round_matches_lifecycle_event(
            world_round,
            event,
        ):
            if not _claim_world_briefing(db, world_round):
                logger.info(
                    "AI round briefing already covered by world telemetry: "
                    "round_event_id=%s world_round_id=%s server=%s",
                    event.id,
                    world_round.round_id,
                    server_name,
                )
                return

    round_id = f"dispatch-{server_name}-round-{event.id}"
    _enqueue_ai_dispatch_job(
        _AIDispatchJob(
            url=url,
            api_key=api_key,
            payload={
                "event_id": f"nexus-round-briefing-{event.id}",
                "round_id": round_id,
                "server_name": server_name,
                "text": "Первичный доклад по сектору.",
                "sender_name": None,
                "message_type": "round_briefing",
                "briefing_context": {
                    "submarine_name": event.submarine_name,
                    "mission": event.mission,
                    "location": event.location,
                },
            },
            server_name=server_name,
            initial_delay_seconds=_random_ai_briefing_delay_seconds(),
            retry_delay_seconds=_AI_BRIEFING_RETRY_DELAY_SECONDS,
            max_attempts=_AI_BRIEFING_MAX_ATTEMPTS,
        )
    )


def _queue_ai_world_briefing(world_round: WorldRound, db: Session) -> None:
    """Queue one briefing from first world telemetry when /round/start is absent."""

    url = (settings.ai_dispatcher_url or "").strip()
    api_key = (settings.ai_dispatcher_api_key or "").strip()
    server_name = (world_round.server_name or "").strip().upper()
    if not url or not api_key or server_name not in {"ASPER1", "ASPER2"}:
        return
    if world_round.ai_briefing_queued_at is not None:
        return

    latest_event = (
        db.query(RoundEvent)
        .filter(RoundEvent.server_name == server_name)
        .order_by(RoundEvent.created_at.desc(), RoundEvent.id.desc())
        .first()
    )
    if (
        latest_event is not None
        and latest_event.event_type == "start"
        and _world_round_matches_lifecycle_event(world_round, latest_event)
    ):
        # The lifecycle path has already queued its job.  Persist the claim so
        # every later snapshot and every other worker remains silent.
        _claim_world_briefing(db, world_round)
        return

    if not _claim_world_briefing(db, world_round):
        return

    _enqueue_ai_dispatch_job(
        _AIDispatchJob(
            url=url,
            api_key=api_key,
            payload={
                "event_id": f"nexus-world-briefing-{world_round.id}",
                "round_id": world_round.round_id,
                "server_name": server_name,
                "text": "Первичный доклад по сектору.",
                "sender_name": None,
                "message_type": "round_briefing",
                "briefing_context": {
                    "submarine_name": world_round.sub_name,
                    "mission": None,
                    "location": world_round.level_name,
                },
            },
            server_name=server_name,
            initial_delay_seconds=_random_ai_briefing_delay_seconds(),
            retry_delay_seconds=_AI_BRIEFING_RETRY_DELAY_SECONDS,
            max_attempts=_AI_BRIEFING_MAX_ATTEMPTS,
        )
    )
    logger.info(
        "AI world briefing queued from telemetry: event_id=%s round_id=%s "
        "server=%s delay_range=%s-%s",
        f"nexus-world-briefing-{world_round.id}",
        world_round.round_id,
        server_name,
        _AI_BRIEFING_MIN_DELAY_SECONDS,
        _AI_BRIEFING_MAX_DELAY_SECONDS,
    )


def _queue_ai_large_creature_alert(
    world_round: WorldRound,
    alerts: List[AICreatureAlert],
    db: Session,
) -> bool:
    """Queue RP warnings for new large contacts and Yasa perimeter entries."""

    if not alerts:
        return False
    url = (settings.ai_dispatcher_url or "").strip()
    api_key = (settings.ai_dispatcher_api_key or "").strip()
    server_name = (world_round.server_name or "").strip().upper()
    if not url or not api_key or server_name not in {"ASPER1", "ASPER2"}:
        logger.warning(
            "AI large-creature alert not queued: round_id=%s server=%r "
            "url_configured=%s key_configured=%s",
            world_round.round_id,
            server_name,
            bool(url),
            bool(api_key),
        )
        _finish_ai_creature_alert_delivery(
            tuple(int(alert.id) for alert in alerts if alert.id is not None),
            delivered=False,
        )
        return False

    conversation_round_id = _resolve_ai_conversation_round_id(db, server_name)
    grouped_alerts: dict[tuple[str, bool], List[AICreatureAlert]] = {}
    for alert in alerts:
        contact_class = (
            "large"
            if is_configured_large_species(
                alert.species,
                settings.ai_large_creature_species_list,
            )
            else "unclassified"
        )
        yasa_perimeter = str(alert.character_id).startswith("yasa:")
        grouped_alerts.setdefault((contact_class, yasa_perimeter), []).append(alert)

    all_queued = True
    for (contact_class, yasa_perimeter), group in grouped_alerts.items():
        first_alert = min(group, key=lambda alert: int(alert.id or 0))
        nearest_distance = min(
            int(alert.distance_from_yasa_m_approx) for alert in group
        )
        alert_ids = tuple(int(alert.id) for alert in group if alert.id is not None)
        event_id = f"nexus-creature-alert-{first_alert.id}"
        queued = _enqueue_ai_dispatch_job(
            _AIDispatchJob(
                url=url,
                api_key=api_key,
                payload={
                    "event_id": event_id,
                    "round_id": conversation_round_id,
                    "server_name": server_name,
                    "text": "Оперативное предупреждение о новой сонарной отметке.",
                    "sender_name": None,
                    "message_type": "threat_alert",
                    "threat_context": {
                        "contact_scope": "group" if len(group) > 1 else "single",
                        "contact_class": contact_class,
                        "yasa_perimeter": yasa_perimeter,
                        "distance_from_yasa_m_approx": nearest_distance,
                    },
                },
                server_name=server_name,
                retry_delay_seconds=_AI_CREATURE_ALERT_RETRY_DELAY_SECONDS,
                max_attempts=_AI_CREATURE_ALERT_MAX_ATTEMPTS,
                creature_alert_ids=alert_ids,
            )
        )
        if not queued:
            _finish_ai_creature_alert_delivery(alert_ids, delivered=False)
            all_queued = False
            continue
        logger.info(
            "AI creature alert queued: event_id=%s round_id=%s server=%s "
            "contact_scope=%s contact_class=%s yasa_perimeter=%s "
            "distance_from_yasa_m_approx=%s",
            event_id,
            conversation_round_id,
            server_name,
            "group" if len(group) > 1 else "single",
            contact_class,
            yasa_perimeter,
            nearest_distance,
        )
    return all_queued


def _enqueue_ai_dispatch_job(job: _AIDispatchJob) -> bool:
    """Put an AI request into the source server's non-blocking FIFO."""

    with _ai_dispatch_job_queues_lock:
        work_queue = _ai_dispatch_job_queues.get(job.server_name)
        if work_queue is None:
            work_queue = Queue(maxsize=_AI_DISPATCH_QUEUE_MAXSIZE)
            _ai_dispatch_job_queues[job.server_name] = work_queue
            worker = threading.Thread(
                target=_ai_dispatch_worker,
                args=(job.server_name, work_queue),
                daemon=True,
                name=f"AIDispatch-{job.server_name}",
            )
            worker.start()
        try:
            work_queue.put_nowait(job)
        except Full:
            logger.error(
                "AI dispatch queue full; dropping request: event_id=%s server=%s maxsize=%s",
                job.payload.get("event_id"),
                job.server_name,
                _AI_DISPATCH_QUEUE_MAXSIZE,
            )
            return False
        queue_size = work_queue.qsize()

    logger.info(
        "AI dispatch queued: event_id=%s server=%s queue_size=%s",
        job.payload.get("event_id"),
        job.server_name,
        queue_size,
    )
    return True


def _ai_dispatch_worker(server_name: str, work_queue: Queue) -> None:
    """Drain one server FIFO forever without occupying a web worker."""

    while True:
        job = work_queue.get()
        delivered = False
        try:
            logger.info(
                "AI dispatch started: event_id=%s server=%s queued_after=%s",
                job.payload.get("event_id"),
                server_name,
                work_queue.qsize(),
            )
            # Each Uvicorn process owns its own in-memory FIFO.  The file lock
            # extends the one-server-at-a-time guarantee across those workers
            # on the same Nexus host; the kernel releases it if a worker dies.
            with _ai_dispatch_process_lock(server_name):
                if job.initial_delay_seconds > 0:
                    time.sleep(job.initial_delay_seconds)
                if not _ai_dispatch_context_is_current(
                    server_name,
                    job.payload.get("round_id"),
                    job.enqueued_at,
                ):
                    logger.warning(
                        "AI dispatch dropped stale job before model call: event_id=%s "
                        "server=%s round_id=%s age_seconds=%.1f",
                        job.payload.get("event_id"),
                        server_name,
                        job.payload.get("round_id"),
                        time.monotonic() - job.enqueued_at,
                    )
                    continue
                for attempt in range(1, max(job.max_attempts, 1) + 1):
                    logger.info(
                        "AI dispatch attempt: event_id=%s server=%s attempt=%s/%s",
                        job.payload.get("event_id"),
                        server_name,
                        attempt,
                        job.max_attempts,
                    )
                    delivered = _post_ai_dispatcher_sync(
                        job.url,
                        job.api_key,
                        job.payload,
                        job.server_name,
                        enqueued_at=job.enqueued_at,
                    )
                    if delivered or attempt >= job.max_attempts:
                        if not delivered:
                            logger.error(
                                "AI dispatch exhausted retries: event_id=%s server=%s "
                                "attempts=%s age_seconds=%.1f",
                                job.payload.get("event_id"),
                                server_name,
                                job.max_attempts,
                                time.monotonic() - job.enqueued_at,
                            )
                        break
                    logger.info(
                        "AI dispatch retry scheduled: event_id=%s server=%s "
                        "attempt=%s/%s delay_seconds=%.1f",
                        job.payload.get("event_id"),
                        server_name,
                        attempt + 1,
                        job.max_attempts,
                        job.retry_delay_seconds,
                    )
                    if job.retry_delay_seconds > 0:
                        time.sleep(job.retry_delay_seconds)
                    if not _ai_dispatch_context_is_current(
                        server_name,
                        job.payload.get("round_id"),
                        job.enqueued_at,
                    ):
                        break
        except Exception:
            # _post_ai_dispatcher_sync is already defensive, but an unexpected
            # failure must not kill the per-server FIFO and strand later calls.
            logger.exception(
                "AI dispatch worker failed: event_id=%s server=%s",
                job.payload.get("event_id"),
                server_name,
            )
        finally:
            if job.creature_alert_ids:
                _finish_ai_creature_alert_delivery(
                    job.creature_alert_ids,
                    delivered=delivered,
                )
            work_queue.task_done()


def _finish_ai_creature_alert_delivery(
    alert_ids: tuple[int, ...],
    *,
    delivered: bool,
) -> None:
    """Complete or release an automatic contact-alert delivery lease."""

    if not alert_ids:
        return
    callback_db = GameSessionLocal()
    try:
        values = (
            {
                AICreatureAlert.delivered_at: datetime.utcnow(),
                AICreatureAlert.queued_at: None,
            }
            if delivered
            else {AICreatureAlert.queued_at: None}
        )
        updated = (
            callback_db.query(AICreatureAlert)
            .filter(
                AICreatureAlert.id.in_(alert_ids),
                AICreatureAlert.delivered_at.is_(None),
            )
            .update(values, synchronize_session=False)
        )
        callback_db.commit()
        logger.info(
            "AI large-creature alert delivery state: alert_ids=%s delivered=%s updated=%s",
            ",".join(str(alert_id) for alert_id in alert_ids),
            delivered,
            updated,
        )
    except Exception:
        callback_db.rollback()
        logger.exception(
            "AI large-creature alert delivery state update failed: alert_ids=%s "
            "delivered=%s",
            ",".join(str(alert_id) for alert_id in alert_ids),
            delivered,
        )
    finally:
        callback_db.close()


@contextmanager
def _ai_dispatch_process_lock(server_name: str):
    """Serialize one server's AI delivery across local Uvicorn processes."""

    with _dispatch_process_locks((server_name,), blocking=True, lock_scope="ai"):
        yield


@contextmanager
def _dispatch_process_locks(server_names, *, blocking: bool, lock_scope: str = "ai"):
    """Lock dispatch channels in stable order across local web processes."""

    if fcntl is None:
        logger.warning(
            "Cross-process dispatch lock unavailable on this platform; servers=%s",
            ",".join(server_names),
        )
        yield
        return

    os.makedirs(_AI_PROCESS_LOCK_DIR, exist_ok=True)
    lock_files = []
    operation = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        for server_name in sorted(set(server_names)):
            lock_path = os.path.join(
                _AI_PROCESS_LOCK_DIR,
                f".{lock_scope}-dispatch-{server_name}.lock",
            )
            lock_file = open(lock_path, "a+b")
            try:
                fcntl.flock(lock_file.fileno(), operation)
            except Exception:
                lock_file.close()
                raise
            lock_files.append(lock_file)
        yield
    finally:
        for lock_file in reversed(lock_files):
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()


def _resolve_ai_conversation_round_id(db: Session, server_name: str) -> str:
    """Вернуть стабильный ID беседы на время текущего раунда сервера."""

    latest_event = (
        db.query(RoundEvent)
        .filter(RoundEvent.server_name == server_name)
        .order_by(RoundEvent.created_at.desc(), RoundEvent.id.desc())
        .first()
    )
    latest_world_round = _latest_world_round(db, server_name)

    # Some servers stream world telemetry but do not post lifecycle events.
    # Prefer a clearly newer world round over an old start/end row so both the
    # automatic briefing and later terminal messages share the real round ID.
    if latest_world_round is not None:
        world_started_at = (
            latest_world_round.created_at or latest_world_round.last_update_at
        )
        event_created_at = latest_event.created_at if latest_event is not None else None
        world_is_unrepresented = latest_event is None
        if world_started_at is not None and event_created_at is not None:
            if latest_event.event_type == "end":
                world_is_unrepresented = world_started_at > event_created_at
            elif latest_event.event_type == "start":
                world_is_unrepresented = (
                    world_started_at
                    > event_created_at
                    + timedelta(seconds=_AI_BRIEFING_LIFECYCLE_MATCH_SECONDS)
                )
        if world_is_unrepresented:
            return str(latest_world_round.round_id)

    if latest_event is not None:
        if latest_event.event_type == "start":
            return f"dispatch-{server_name}-round-{latest_event.id}"
        return f"dispatch-{server_name}-idle-{latest_event.id}"

    if latest_world_round is not None:
        return str(latest_world_round.round_id)
    return f"dispatch-{server_name}-idle-0"


def _ai_dispatch_context_is_current(
    server_name: str,
    expected_round_id: object,
    enqueued_at: Optional[float],
) -> bool:
    """Reject queued work after its TTL or after the game round changed."""

    db = GameSessionLocal()
    try:
        return _ai_dispatch_context_matches_db(
            db,
            server_name,
            expected_round_id,
            enqueued_at,
        )
    finally:
        db.close()


def _ai_dispatch_context_matches_db(
    db: Session,
    server_name: str,
    expected_round_id: object,
    enqueued_at: Optional[float],
) -> bool:
    """Check job age and round identity in one database snapshot."""

    if enqueued_at is not None and time.monotonic() - enqueued_at > _AI_DISPATCH_JOB_TTL_SECONDS:
        return False
    if not isinstance(expected_round_id, str) or not expected_round_id:
        return False
    return _resolve_ai_conversation_round_id(db, server_name) == expected_round_id


def _post_ai_dispatcher_sync(
    url: str,
    api_key: str,
    payload: dict,
    server_name: str,
    enqueued_at: Optional[float] = None,
) -> bool:
    try:
        with httpx.Client(timeout=settings.ai_dispatcher_timeout_seconds) as client:
            response = client.post(
                url,
                headers={"X-API-Key": api_key},
                json=payload,
            )
        response.raise_for_status()
        result = response.json()
        result_status = result.get("status")
        if result_status != "answered":
            system_message = payload.get("message_type") in {
                "round_briefing",
                "threat_alert",
            }
            logger.warning(
                "AI dispatcher returned without answer: event_id=%s server=%s "
                "message_type=%r status=%r retry=%s",
                payload.get("event_id"),
                server_name,
                payload.get("message_type"),
                result_status,
                system_message,
            )
            return not system_message
        replies = _extract_ai_dispatcher_replies(result)
        if not replies:
            logger.warning("AI dispatcher returned an empty reply for %s", payload["event_id"])
            return False

        delivered = _deliver_ai_dispatcher_replies(
            replies,
            server_name=server_name,
            event_id=payload.get("event_id", "unknown"),
            round_id=payload.get("round_id"),
            enqueued_at=enqueued_at,
        )
        if delivered is False:
            return False
        logger.info(
            "AI dispatcher answer accepted: event_id=%s server=%s reply_parts=%s",
            payload.get("event_id"),
            server_name,
            len(replies),
        )
        return True
    except httpx.HTTPStatusError as error:
        response = error.response
        response_text = " ".join((response.text or "").split())[:500]
        logger.warning(
            "AI dispatcher HTTP error: event_id=%s server=%s status=%s response=%r",
            payload.get("event_id"),
            server_name,
            response.status_code,
            response_text,
        )
        return False
    except Exception as error:
        logger.warning(
            "AI dispatcher request failed: event_id=%s server=%s error_type=%s error=%s",
            payload.get("event_id"),
            server_name,
            type(error).__name__,
            error,
        )
        return False


def _ai_reply_delay_seconds(reply: str) -> float:
    """Return a linear 4–12 second radio delay based on visible text length."""

    text_length = min(len((reply or "").strip()), _AI_REPLY_MAX_CHARS)
    length_ratio = text_length / _AI_REPLY_MAX_CHARS
    delay_range = _AI_REPLY_MAX_DELAY_SECONDS - _AI_REPLY_MIN_DELAY_SECONDS
    return _AI_REPLY_MIN_DELAY_SECONDS + delay_range * length_ratio


def _deliver_ai_dispatcher_replies(
    replies: List[str],
    *,
    server_name: str,
    event_id: str,
    round_id: Optional[str] = None,
    enqueued_at: Optional[float] = None,
) -> bool:
    """Deliver one AI answer serially, waiting for every game-server ACK.

    Merely inserting rows several seconds apart is insufficient because
    ``GET /server/actions`` returns every pending row in one poll.  Waiting for
    the target server's ACK before delaying and inserting the next part keeps
    at most one AI radiogram in flight.
    """

    for part_number, reply in enumerate(replies, start=1):
        if round_id is not None and not _ai_dispatch_context_is_current(
            server_name,
            round_id,
            enqueued_at,
        ):
            logger.warning(
                "AI dispatch dropped stale reply before delay: event_id=%s server=%s "
                "round_id=%s part=%s/%s",
                event_id,
                server_name,
                round_id,
                part_number,
                len(replies),
            )
            return False
        delay_seconds = _ai_reply_delay_seconds(reply)
        logger.info(
            "AI dispatch delay: event_id=%s server=%s part=%s/%s chars=%s delay_seconds=%.2f",
            event_id,
            server_name,
            part_number,
            len(replies),
            len(reply),
            delay_seconds,
        )
        time.sleep(delay_seconds)

        queue_kwargs = {}
        if round_id is not None:
            queue_kwargs = {
                "expected_round_id": round_id,
                "enqueued_at": enqueued_at,
            }
        action_id = _queue_ai_radio_action(
            reply,
            server_name,
            event_id,
            part_number,
            len(replies),
            **queue_kwargs,
        )
        if action_id is None:
            logger.error(
                "AI dispatch aborted before action queue: event_id=%s server=%s "
                "part=%s/%s",
                event_id,
                server_name,
                part_number,
                len(replies),
            )
            return False
        logger.info(
            "AI dispatch sent to action queue: event_id=%s server=%s part=%s/%s action_id=%s",
            event_id,
            server_name,
            part_number,
            len(replies),
            action_id,
        )
        ack_kwargs = {}
        if round_id is not None:
            ack_kwargs = {
                "expected_round_id": round_id,
                "enqueued_at": enqueued_at,
            }
        ack_status = _wait_for_ai_dispatch_action_ack(
            action_id,
            server_name,
            event_id,
            **ack_kwargs,
        )
        if ack_status != 1:
            logger.error(
                "AI dispatch aborted after missing or failed ACK: event_id=%s server=%s "
                "part=%s/%s action_id=%s status=%s",
                event_id,
                server_name,
                part_number,
                len(replies),
                action_id,
                ack_status,
            )
            return False
    return True


def _pending_dispatch_action_exists(db: Session, server_name: str) -> bool:
    status_column = (
        ServerAction.status_asper1
        if server_name == "ASPER1"
        else ServerAction.status_asper2
    )
    return (
        db.query(ServerAction.id)
        .filter(
            ServerAction.action_type == "dispatch",
            status_column == 0,
        )
        .first()
        is not None
    )


def _queue_ai_radio_action(
    reply: str,
    server_name: str,
    event_id: str,
    part_number: int,
    part_count: int,
    *,
    expected_round_id: Optional[str] = None,
    enqueued_at: Optional[float] = None,
) -> Optional[int]:
    """Atomically wait for an idle channel, then create one dispatch action."""

    logged_wait = False
    deadline = time.monotonic() + _AI_CHANNEL_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with _dispatch_process_locks(
            (server_name,),
            blocking=True,
            lock_scope="channel",
        ):
            callback_db = GameSessionLocal()
            try:
                with _dispatch_action_insert_lock:
                    if (
                        expected_round_id is not None
                        and not _ai_dispatch_context_matches_db(
                            callback_db,
                            server_name,
                            expected_round_id,
                            enqueued_at,
                        )
                    ):
                        logger.warning(
                            "AI dispatch dropped stale reply before action queue: "
                            "event_id=%s server=%s round_id=%s part=%s/%s",
                            event_id,
                            server_name,
                            expected_round_id,
                            part_number,
                            part_count,
                        )
                        return None
                    if not _pending_dispatch_action_exists(callback_db, server_name):
                        _, action = _send_dispatcher_message_locked(
                            callback_db,
                            reply,
                            server_name,
                            admin_id=None,
                            sender_label="ИИ-диспетчер",
                        )
                        return int(action.id)
            finally:
                callback_db.close()

        if not logged_wait:
            logger.info(
                "AI dispatch waiting for idle channel: event_id=%s server=%s part=%s/%s",
                event_id,
                server_name,
                part_number,
                part_count,
            )
            logged_wait = True
        time.sleep(_AI_ACTION_ACK_POLL_SECONDS)

    logger.error(
        "AI dispatch channel wait timed out: event_id=%s server=%s part=%s/%s "
        "timeout_seconds=%s",
        event_id,
        server_name,
        part_number,
        part_count,
        _AI_CHANNEL_WAIT_TIMEOUT_SECONDS,
    )
    return None


def _wait_for_ai_dispatch_action_ack(
    action_id: int,
    server_name: str,
    event_id: str,
    *,
    expected_round_id: Optional[str] = None,
    enqueued_at: Optional[float] = None,
) -> Optional[int]:
    """Wait off-request until the target game server ACKs an AI action."""

    status_attr = "status_asper1" if server_name == "ASPER1" else "status_asper2"
    deadline = time.monotonic() + _AI_ACTION_ACK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if expected_round_id is not None and not _ai_dispatch_context_is_current(
            server_name,
            expected_round_id,
            enqueued_at,
        ):
            _cancel_ai_dispatch_action(
                action_id,
                server_name,
                event_id,
                reason="Раунд завершился до подтверждения радиограммы",
            )
            return 2
        callback_db = GameSessionLocal()
        try:
            action = (
                callback_db.query(ServerAction)
                .filter(ServerAction.id == action_id)
                .first()
            )
            if action is None:
                logger.warning(
                    "AI dispatch action disappeared before ACK: event_id=%s server=%s action_id=%s",
                    event_id,
                    server_name,
                    action_id,
                )
                return None
            status = getattr(action, status_attr)
            if status == 1:
                logger.info(
                    "AI dispatch ACK received: event_id=%s server=%s action_id=%s status=%s",
                    event_id,
                    server_name,
                    action_id,
                    status,
                )
                return 1
            if status == 2:
                logger.warning(
                    "AI dispatch ACK reported failure: event_id=%s server=%s "
                    "action_id=%s",
                    event_id,
                    server_name,
                    action_id,
                )
                return 2
        finally:
            callback_db.close()

        time.sleep(_AI_ACTION_ACK_POLL_SECONDS)

    logger.error(
        "AI dispatch ACK wait timed out: event_id=%s server=%s action_id=%s "
        "timeout_seconds=%s",
        event_id,
        server_name,
        action_id,
        _AI_ACTION_ACK_TIMEOUT_SECONDS,
    )
    _cancel_ai_dispatch_action(
        action_id,
        server_name,
        event_id,
        reason="Истекло время ожидания подтверждения радиограммы",
    )
    return None


def _cancel_ai_dispatch_action(
    action_id: int,
    server_name: str,
    event_id: str,
    *,
    reason: str,
) -> bool:
    """Atomically fail an unacknowledged AI action so it cannot resurface."""

    status_column = (
        ServerAction.status_asper1
        if server_name == "ASPER1"
        else ServerAction.status_asper2
    )
    error_column = (
        ServerAction.error_asper1
        if server_name == "ASPER1"
        else ServerAction.error_asper2
    )
    executed_column = (
        ServerAction.executed_at_asper1
        if server_name == "ASPER1"
        else ServerAction.executed_at_asper2
    )
    callback_db = GameSessionLocal()
    try:
        updated = (
            callback_db.query(ServerAction)
            .filter(
                ServerAction.id == action_id,
                status_column == 0,
            )
            .update(
                {
                    status_column: 2,
                    error_column: reason,
                    executed_column: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        callback_db.commit()
    finally:
        callback_db.close()
    if updated:
        logger.warning(
            "AI dispatch action cancelled: event_id=%s server=%s action_id=%s reason=%s",
            event_id,
            server_name,
            action_id,
            reason,
        )
    return bool(updated)


def _extract_ai_dispatcher_replies(result: dict) -> List[str]:
    """Read the new multi-reply contract, falling back to the legacy reply."""

    raw_replies = result.get("replies")
    if isinstance(raw_replies, list):
        candidates = [reply for reply in raw_replies if isinstance(reply, str) and reply.strip()]
    else:
        candidates = []

    if not candidates:
        legacy_reply = result.get("reply")
        if isinstance(legacy_reply, str) and legacy_reply.strip():
            candidates = [legacy_reply]

    replies = []
    for candidate in candidates:
        replies.extend(_split_ai_radio_reply(candidate, max_chars=_AI_REPLY_MAX_CHARS))
    if len(replies) > _AI_MAX_REPLY_PARTS:
        logger.warning(
            "AI dispatcher exceeded reply part limit: parts=%s max_parts=%s",
            len(replies),
            _AI_MAX_REPLY_PARTS,
        )
    return replies[:_AI_MAX_REPLY_PARTS]


def _split_ai_radio_reply(reply: str, max_chars: int) -> List[str]:
    """Defensively fit old or malformed AI replies into Barotrauma messages."""

    compact = " ".join(reply.split())
    if not compact:
        return []

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?…])\s+", compact)
        if sentence.strip()
    ]
    parts = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                parts.append(current)
                current = ""
            parts.extend(_split_ai_radio_sentence(sentence, max_chars))
            continue

        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            parts.append(current)
            current = sentence

    if current:
        parts.append(current)
    return parts


def _split_ai_radio_sentence(sentence: str, max_chars: int) -> List[str]:
    """Create standalone clause messages; never cut an unfinished word sequence."""

    parts = []
    remainder = sentence
    while len(remainder) > max_chars:
        window = remainder[:max_chars + 1]
        clause_breaks = [
            match.end()
            for match in re.finditer(r"[,;:—–-](?=\s)", window[:max_chars])
        ]
        if not clause_breaks:
            raise ValueError("AI dispatcher returned an unsplittable oversized sentence")
        cut_at = clause_breaks[-1]
        part = _finish_ai_radio_clause(remainder[:cut_at], max_chars)
        remainder = remainder[cut_at:].lstrip()
        if part:
            parts.append(part)
    if remainder:
        parts.append(_finish_ai_radio_clause(remainder, max_chars))
    return parts


def _finish_ai_radio_clause(value: str, max_chars: int) -> str:
    clause = value.strip().rstrip(" ,;:—–-")
    if not clause:
        return ""
    clause = f"{clause[0].upper()}{clause[1:]}"
    if not clause.endswith((".", "!", "?", "…")):
        if len(clause) >= max_chars:
            raise ValueError("AI dispatcher returned an oversized terminal clause")
        clause = f"{clause}."
    return clause


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
    the admin-panel frontend blocks further sends (see /dispatch/pending). The
    server-side lock and pending check also reject racing admin/client sends, so
    they cannot overlap an AI transmission even with multiple Uvicorn workers.
    """
    # The request session may already hold a MySQL REPEATABLE READ snapshot
    # from authentication. A fresh session opened *after* the process lock is
    # required, otherwise another worker's just-committed pending action can be
    # invisible and two radiograms can be inserted.
    del db
    target_servers = _dispatch_target_servers(server_name)
    normalized_server_name = target_servers[0] if server_name is not None else None
    with _dispatch_process_locks(
        target_servers,
        blocking=True,
        lock_scope="channel",
    ):
        channel_db = GameSessionLocal()
        try:
            with _dispatch_action_insert_lock:
                if any(
                    _pending_dispatch_action_exists(channel_db, target_server)
                    for target_server in target_servers
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Канал диспетчерской занят",
                    )
                return _send_dispatcher_message_locked(
                    channel_db,
                    text,
                    normalized_server_name,
                    admin_id,
                    sender_label,
                )
        finally:
            channel_db.close()


def _dispatch_target_servers(server_name: Optional[str]) -> tuple[str, ...]:
    if server_name is None:
        return ("ASPER1", "ASPER2")
    normalized = str(server_name).strip().upper()
    if normalized not in {"ASPER1", "ASPER2"}:
        raise HTTPException(status_code=400, detail="server_name must be ASPER1 or ASPER2")
    return (normalized,)


def _send_dispatcher_message_locked(
    db: Session,
    text: str,
    server_name: Optional[str],
    admin_id: Optional[int],
    sender_label: str,
) -> tuple:
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    full_text = f"{_DISPATCHER_PREFIX}{text}"

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
    if not check_permission(current_admin, "dispatch", "send_message"):
        raise HTTPException(status_code=403)
    body = await request.json()
    server_name = body.get("server_name") or None  # optional: target specific server
    msg, action = _send_dispatcher_message(db, body.get("text", ""), server_name, current_admin.id, current_admin.discord_username)
    return JSONResponse({"status": "ok", "id": msg.id, "action_id": action.id})


@panel_router.get("/api/v1/admin/dispatch/pending")
def dispatch_check_pending(
    server: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(_get_current_admin),
):
    """Check if there's a pending dispatch action (not yet ACKed by server).
    Frontend uses this to block/unblock the send button.
    """
    if not check_permission(current_admin, "dispatch", "view"):
        raise HTTPException(status_code=403)
    query = db.query(ServerAction).filter(ServerAction.action_type == "dispatch")
    if server:
        normalized_server = _dispatch_target_servers(server)[0]
        status_column = (
            ServerAction.status_asper1
            if normalized_server == "ASPER1"
            else ServerAction.status_asper2
        )
        query = query.filter(status_column == 0)
    else:
        query = query.filter(
            (ServerAction.status_asper1 == 0) | (ServerAction.status_asper2 == 0)
        )
    pending = query.first()
    return {"pending": pending is not None, "action_id": pending.id if pending else None}


@panel_router.get("/api/v1/admin/dispatch/webhook-config")
def get_dispatch_webhook_config(db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Текущие адреса исходящих вебхуков внешней интеграции (по серверам).

    Раньше было жёстко «только суперпользователь». Теперь это отдельное право
    dispatch:manage_webhooks, которое НЕ выдаётся автоматически держателям
    dispatch:edit (см. PERMISSION_ACTION_SPLITS): адрес вебхука — точка утечки
    данных наружу, и она не должна открываться заодно с правом слать грузы.
    Суперпользователь проходит любую проверку прав, поэтому доступ у него
    сохраняется без отдельной ветки."""
    if not check_permission(current_admin, "dispatch", "manage_webhooks"):
        raise HTTPException(status_code=403, detail="Нет права на настройку вебхуков диспетчерской")
    return {
        "ASPER1": _get_app_setting(db, "dispatch_webhook_url_asper1"),
        "ASPER2": _get_app_setting(db, "dispatch_webhook_url_asper2"),
    }


@panel_router.patch("/api/v1/admin/dispatch/webhook-config")
async def set_dispatch_webhook_config(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    """Задать или очистить адрес исходящего вебхука для ASPER1/ASPER2.
    Тело: {"ASPER1": "https://..."|null, "ASPER2": "https://..."|null} —
    ключи необязательны, обновляются только переданные."""
    if not check_permission(current_admin, "dispatch", "manage_webhooks"):
        raise HTTPException(status_code=403, detail="Нет права на настройку вебхуков диспетчерской")
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
    if not check_permission(current_admin, "dispatch", "shuttle"):
        raise HTTPException(status_code=403)
    body = await request.json()
    command = body.get("command")
    server_name = (body.get("server_name") or "").strip() or None
    action = _queue_shuttle_action(db, server_name, command, current_admin.id)
    return JSONResponse({"status": "ok", "action_id": action.id})


@panel_router.post("/api/v1/admin/dispatch/actions/torpedo")
async def dispatch_torpedo_action(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    if not check_permission(current_admin, "dispatch", "torpedo"):
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
    logger.info(
        "dispatch.cargo.queued action_id=%s server=%s items=%r",
        action.id,
        server_name,
        action.payload.get("items", []) if isinstance(action.payload, dict) else [],
    )
    return action


@panel_router.post("/api/v1/admin/dispatch/actions/cargo")
async def dispatch_cargo_action(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(_get_current_admin)):
    if not check_permission(current_admin, "dispatch", "cargo"):
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
