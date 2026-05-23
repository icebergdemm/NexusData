"""
API endpoints for the Discord bot.
Bot communicates with NexusData through these endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

from database import get_db
from security import verify_external_api_key_dependency, check_permission
from config import get_settings
from models import (
    DiscordMember, DiscordBotAction, DiscordBotConfig, DiscordAntiRaidLog,
    PlayerData, PlayerRating,
)

settings = get_settings()

router = APIRouter(
    prefix="/api/v1/bot",
    tags=["discord-bot"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# Separate router for admin panel pages (no prefix, no API key)
panel_router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class MemberInfo(BaseModel):
    discord_id: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    avatar: Optional[str] = None
    joined_at: Optional[str] = None


class MembersSyncRequest(BaseModel):
    members: List[MemberInfo]


class BotActionOut(BaseModel):
    id: int
    action_type: str
    payload: dict
    queued_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BotActionAck(BaseModel):
    success: bool
    error_message: Optional[str] = None


class AntiRaidEvent(BaseModel):
    event_type: str
    discord_id: Optional[str] = None
    username: Optional[str] = None
    details: Optional[dict] = None


class ConfigUpdate(BaseModel):
    key: str
    value: str


# ══════════════════════════════════════════════════════════════════════════════
# MEMBERS SYNC
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/members/sync")
def sync_members(body: MembersSyncRequest, db: Session = Depends(get_db)):
    """
    Bot sends full list of Discord server members every 10 minutes.
    NexusData stores them and attempts to auto-match discord_id to players.
    """
    now = datetime.utcnow()
    synced = 0

    for m in body.members:
        record = db.query(DiscordMember).filter(DiscordMember.discord_id == m.discord_id).first()
        if record:
            record.username = m.username
            record.display_name = m.display_name
            record.avatar = m.avatar
            record.last_synced_at = now
        else:
            record = DiscordMember(
                discord_id=m.discord_id,
                username=m.username,
                display_name=m.display_name,
                avatar=m.avatar,
                joined_at=datetime.fromisoformat(m.joined_at) if m.joined_at else None,
                last_synced_at=now,
            )
            db.add(record)
        synced += 1

    db.commit()

    # Auto-match: try to link discord_id to players by name similarity
    _auto_match_discord_ids(db)

    return {"status": "ok", "synced": synced}


def _auto_match_discord_ids(db: Session):
    """
    Try to auto-match Discord members to players by display_name similarity.
    Only updates players where discord_id is NULL or discord_id_confirmed=0.
    """
    members = db.query(DiscordMember).all()
    if not members:
        return

    # Get players without confirmed discord_id
    players = db.query(PlayerData).filter(
        (PlayerData.discord_id.is_(None)) | (PlayerData.discord_id_confirmed == 0)
    ).all()

    if not players:
        return

    # Build lookup: lowercase display_name -> discord_id
    name_to_discord = {}
    for m in members:
        if m.display_name:
            name_to_discord[m.display_name.lower().strip()] = m.discord_id
        if m.username:
            name_to_discord[m.username.lower().strip()] = m.discord_id

    # Match players
    for player in players:
        if not player.player_last_time_name:
            continue
        player_name_lower = player.player_last_time_name.lower().strip()
        # Try exact match
        matched_discord_id = name_to_discord.get(player_name_lower)
        if matched_discord_id:
            player.discord_id = matched_discord_id
            player.discord_id_confirmed = 0  # auto-matched, not confirmed

    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# BOT ACTIONS (queue)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/actions", response_model=List[BotActionOut])
def get_pending_bot_actions(db: Session = Depends(get_db)):
    """Get pending actions for the bot to execute."""
    actions = db.query(DiscordBotAction).filter(
        DiscordBotAction.executed == 0
    ).order_by(DiscordBotAction.queued_at).all()
    return actions


@router.post("/actions/{action_id}/ack")
def ack_bot_action(action_id: int, ack: BotActionAck, db: Session = Depends(get_db)):
    """Acknowledge bot action execution."""
    action = db.query(DiscordBotAction).filter(DiscordBotAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    action.executed = 1 if ack.success else 2
    action.executed_at = datetime.utcnow()
    action.error_message = ack.error_message
    db.commit()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# ANTI-RAID
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/antiraid/event")
def report_antiraid_event(event: AntiRaidEvent, db: Session = Depends(get_db)):
    """Bot reports a potential raid event."""
    log = DiscordAntiRaidLog(
        event_type=event.event_type,
        discord_id=event.discord_id,
        username=event.username,
        details=event.details,
    )
    db.add(log)
    db.commit()
    return {"status": "logged", "id": log.id}


@router.get("/antiraid/events")
def get_antiraid_events(
    limit: int = Query(50),
    unresolved_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Get anti-raid event log."""
    q = db.query(DiscordAntiRaidLog)
    if unresolved_only:
        q = q.filter(DiscordAntiRaidLog.resolved == 0)
    events = q.order_by(DiscordAntiRaidLog.occurred_at.desc()).limit(limit).all()
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "discord_id": e.discord_id,
            "username": e.username,
            "details": e.details,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "resolved": e.resolved,
        }
        for e in events
    ]


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/config")
def get_bot_config(db: Session = Depends(get_db)):
    """Get all bot configuration."""
    configs = db.query(DiscordBotConfig).all()
    return {c.key: c.value for c in configs}


@router.post("/config")
def set_bot_config(update: ConfigUpdate, db: Session = Depends(get_db)):
    """Set a bot configuration value."""
    record = db.query(DiscordBotConfig).filter(DiscordBotConfig.key == update.key).first()
    if record:
        record.value = update.value
    else:
        record = DiscordBotConfig(key=update.key, value=update.value)
        db.add(record)
    db.commit()
    return {"status": "ok", "key": update.key}


# ══════════════════════════════════════════════════════════════════════════════
# RATINGS (for bot to publish in Discord)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/ratings/leaderboard")
def get_ratings_for_bot(
    role: str = Query("captain"),
    limit: int = Query(10),
    db: Session = Depends(get_db),
):
    """Get rating leaderboard for bot to publish in Discord embeds."""
    from rating_constants import ROLES
    from sqlalchemy import desc

    if role not in ROLES:
        role = "captain"

    records = (
        db.query(PlayerRating)
        .filter(PlayerRating.role == role, PlayerRating.current_rating > 0)
        .order_by(desc(PlayerRating.current_rating))
        .limit(limit)
        .all()
    )

    # Enrich with player names and discord_ids
    steam_ids = [r.steam_id for r in records]
    players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steam_ids)).all() if steam_ids else []
    pmap = {p.player_steamid: p for p in players}

    result = []
    for i, r in enumerate(records, 1):
        p = pmap.get(r.steam_id)
        result.append({
            "rank": i,
            "steam_id": r.steam_id,
            "player_name": p.player_last_time_name if p else r.steam_id,
            "discord_id": p.discord_id if p else None,
            "rating": round(r.current_rating, 2),
            "cumulative_points": round(r.cumulative_points, 1),
        })

    return {"role": role, "leaderboard": result}


# ══════════════════════════════════════════════════════════════════════════════
# TWINK DETECTION (for bot)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/twink-suspects")
def get_twink_suspects(db: Session = Depends(get_db)):
    """
    Get potential Discord twink accounts.
    Logic: Discord members who joined recently AND have similar names to existing players
    with different discord_ids.
    """
    from datetime import timedelta

    recent_threshold = datetime.utcnow() - timedelta(days=7)
    recent_members = db.query(DiscordMember).filter(
        DiscordMember.joined_at >= recent_threshold
    ).all()

    if not recent_members:
        return {"suspects": []}

    # Get all players with confirmed discord_ids
    confirmed_players = db.query(PlayerData).filter(
        PlayerData.discord_id.isnot(None),
        PlayerData.discord_id_confirmed == 1,
    ).all()

    confirmed_discord_ids = {p.discord_id for p in confirmed_players}

    suspects = []
    for member in recent_members:
        # Skip if already a confirmed player
        if member.discord_id in confirmed_discord_ids:
            continue

        # Check if display_name matches any existing player name
        if member.display_name:
            similar = db.query(PlayerData).filter(
                PlayerData.player_last_time_name.ilike(f"%{member.display_name[:6]}%")
            ).first()
            if similar and similar.discord_id and similar.discord_id != member.discord_id:
                suspects.append({
                    "new_discord_id": member.discord_id,
                    "new_username": member.username,
                    "new_display_name": member.display_name,
                    "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                    "similar_player_name": similar.player_last_time_name,
                    "similar_player_discord_id": similar.discord_id,
                    "reason": "Name similarity with existing player who has different Discord ID",
                })

    return {"suspects": suspects}


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL PAGE
# ══════════════════════════════════════════════════════════════════════════════

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request, Form
import secrets
import json as _json

_templates = Jinja2Templates(directory="templates")
_templates.env.globals.update(check_permission=check_permission)

from models import AdminUser, AdminSession


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


@panel_router.get("/admin/discord-bot", response_class=HTMLResponse)
async def admin_discord_bot_page(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    # Stats
    members_count = db.query(DiscordMember).count()
    pending_actions = db.query(DiscordBotAction).filter(DiscordBotAction.executed == 0).count()

    # Last sync
    last_member = db.query(DiscordMember).order_by(DiscordMember.last_synced_at.desc()).first()
    last_sync = last_member.last_synced_at.strftime("%d.%m.%Y %H:%M") if last_member and last_member.last_synced_at else None

    # Config
    configs = db.query(DiscordBotConfig).all()
    config = {c.key: c.value for c in configs}

    # Anti-raid events
    antiraid_events = db.query(DiscordAntiRaidLog).order_by(DiscordAntiRaidLog.occurred_at.desc()).limit(30).all()
    ar_list = [
        {
            "event_type": e.event_type,
            "discord_id": e.discord_id,
            "username": e.username,
            "details": e.details,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "resolved": e.resolved,
        }
        for e in antiraid_events
    ]

    # Twink suspects (simple version)
    twink_suspects = []
    try:
        from datetime import timedelta
        recent_threshold = datetime.utcnow() - timedelta(days=7)
        recent_members = db.query(DiscordMember).filter(DiscordMember.joined_at >= recent_threshold).all()
        confirmed_ids = set(
            p.discord_id for p in db.query(PlayerData).filter(
                PlayerData.discord_id.isnot(None), PlayerData.discord_id_confirmed == 1
            ).all()
        )
        for member in recent_members:
            if member.discord_id in confirmed_ids:
                continue
            if member.display_name and len(member.display_name) >= 4:
                similar = db.query(PlayerData).filter(
                    PlayerData.player_last_time_name.ilike(f"%{member.display_name[:6]}%")
                ).first()
                if similar and similar.discord_id and similar.discord_id != member.discord_id:
                    twink_suspects.append({
                        "new_discord_id": member.discord_id,
                        "new_username": member.username,
                        "new_display_name": member.display_name,
                        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                        "similar_player_name": similar.player_last_time_name,
                    })
    except Exception:
        pass

    # Flash messages
    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)

    return _templates.TemplateResponse("admin/discord_bot.html", {
        "request": request,
        "admin": admin,
        "members_count": members_count,
        "pending_actions": pending_actions,
        "last_sync": last_sync,
        "config": config,
        "antiraid_events": ar_list,
        "twink_suspects": twink_suspects,
        "flashes": flashes,
    })


@panel_router.post("/admin/discord-bot/action")
async def admin_discord_bot_send_action(
    request: Request,
    action_type: str = Form(...),
    payload_json: str = Form("{}"),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "edit"):
        raise HTTPException(status_code=403)

    try:
        payload = _json.loads(payload_json)
    except _json.JSONDecodeError:
        payload = {}

    action = DiscordBotAction(
        action_type=action_type,
        payload=payload,
        queued_by_admin_id=admin.id,
    )
    db.add(action)
    db.commit()

    flashes = request.session.get("_flashes", [])
    flashes.append({"level": "success", "message": f"Действие «{action_type}» поставлено в очередь (id={action.id})"})
    request.session["_flashes"] = flashes

    return RedirectResponse(url="/admin/discord-bot", status_code=302)
