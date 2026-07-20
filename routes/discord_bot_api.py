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
    role_ids: List[str] = Field(default_factory=list)


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
    # Structured response for request/response-style actions (e.g.
    # "check_channel_category") — optional and unused by every other action
    # type, so this is fully backward-compatible.
    result: Optional[dict] = None


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
            record.role_ids = m.role_ids
            record.last_synced_at = now
        else:
            record = DiscordMember(
                discord_id=m.discord_id,
                username=m.username,
                display_name=m.display_name,
                avatar=m.avatar,
                role_ids=m.role_ids,
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
def get_pending_bot_actions(action_type: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get pending actions for the bot to execute. `action_type` lets a
    dedicated fast-polling loop (e.g. check_channel_category, polled every
    ~2s for a near-synchronous verify-channel round trip) pull only its own
    action type without also claiming/racing the main 30s loop's actions."""
    q = db.query(DiscordBotAction).filter(DiscordBotAction.executed == 0)
    if action_type:
        q = q.filter(DiscordBotAction.action_type == action_type)
    actions = q.order_by(DiscordBotAction.queued_at).all()
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
    action.result = ack.result
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
# DEPARTMENT CHAT (item 6) — bot mirrors messages from linked channels
# ══════════════════════════════════════════════════════════════════════════════

class DepartmentChatMessageIn(BaseModel):
    discord_channel_id: str
    discord_message_id: Optional[str] = None
    author_name: Optional[str] = None
    author_avatar_url: Optional[str] = None
    content: Optional[str] = None


@router.get("/department-chat/channels")
def list_department_chat_channel_ids(db: Session = Depends(get_db)):
    """Flat list of Discord channel IDs the bot should mirror into
    DepartmentChatMessage — refreshed periodically by sync_members_task."""
    from models import DepartmentChatChannel
    channel_ids = {row[0] for row in db.query(DepartmentChatChannel.discord_channel_id).all()}
    return {"channel_ids": list(channel_ids)}


@router.post("/department-chat/messages")
def ingest_department_chat_message(body: DepartmentChatMessageIn, db: Session = Depends(get_db)):
    from models import DepartmentChatChannel, DepartmentChatMessage

    channel = db.query(DepartmentChatChannel).filter(DepartmentChatChannel.discord_channel_id == body.discord_channel_id).first()
    if not channel:
        return {"status": "skipped"}

    db.add(DepartmentChatMessage(
        channel_id=channel.id,
        discord_message_id=body.discord_message_id,
        author_name=body.author_name,
        author_avatar_url=body.author_avatar_url,
        content=(body.content or "")[:4000],
    ))
    db.commit()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# ROUND STATUS (for the auto-updating Discord embed)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/round-status")
def get_round_status(server_name: str = Query(...), db: Session = Depends(get_db)):
    """Aggregate live round/location/ship/mission/player-count/shuttle-ETA for one server."""
    from routes.dispatch import get_round_state
    from services import ServerModerationService
    from models import ServerLiveState

    round_state = get_round_state(db, server_name)
    online_players = ServerModerationService(db).get_online_players(server_name=server_name)
    live = db.query(ServerLiveState).filter(ServerLiveState.server_name == server_name).first()

    return {
        "server_name": server_name,
        "active": round_state["active"],
        "location": round_state["location"],
        "submarine_name": round_state["submarine_name"],
        "mission": round_state["mission"],
        "started_at": round_state["started_at"].isoformat() if round_state["started_at"] else None,
        "player_count": len(online_players),
        "submarine_health": live.submarine_health if live else None,
        "shuttle_arrival_at": live.shuttle_arrival_at.isoformat() + "Z" if live and live.shuttle_arrival_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SUB-PROJECT MESSAGE ID CALLBACK
# ══════════════════════════════════════════════════════════════════════════════

from pydantic import BaseModel as _BM

class _SubProjectMsgId(_BM):
    message_id: str

@router.post("/sub-project/{project_id}/set-message-id")
def set_sub_project_msg_id(project_id: int, body: _SubProjectMsgId, db: Session = Depends(get_db)):
    """Bot reports the Discord message_id after sending a sub-project embed."""
    from models import SubProject
    project = db.query(SubProject).filter(SubProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.discord_message_id = body.message_id
    db.commit()
    return {"status": "ok", "project_id": project_id, "message_id": body.message_id}


class _DocumentMsgId(_BM):
    message_id: str
    channel_id: str


@router.post("/document/{document_id}/set-message-id")
def set_document_msg_id(document_id: int, body: _DocumentMsgId, db: Session = Depends(get_db)):
    """Bot reports the Discord message_id after sending a NEW document embed
    (routes/documents.py's api_publish_document_to_discord, "send_document_embed"
    action) — a later publish with this message_id still present edits it
    ("Синхронизировать") instead of posting a new message."""
    from models import Document
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.discord_message_id = body.message_id
    doc.discord_channel_id = body.channel_id
    db.commit()
    return {"status": "ok", "document_id": document_id, "message_id": body.message_id}


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
# DEPARTMENT RATINGS (for bot to publish, one embed per department — mirrors
# the per-role rating publish above)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/department-ratings/channels")
def list_department_rating_channels(db: Session = Depends(get_db)):
    """Every department with a rating_channel_id configured — the bot polls
    this hourly before publishing, same "reload from NexusData config"
    pattern already used for the site-wide rating channel."""
    from models import Department

    depts = db.query(Department).filter(
        Department.rating_channel_id.isnot(None), Department.is_archived.is_(False)
    ).all()
    return {"departments": [{"department_id": d.id, "name": d.name, "icon": d.icon, "rating_channel_id": d.rating_channel_id} for d in depts]}


@router.get("/department-ratings/leaderboard")
def get_department_rating_for_bot(department_id: int = Query(...), limit: int = Query(10), db: Session = Depends(get_db)):
    """Same aggregation as GET /api/v1/admin/departments/{id}/rating, just
    without the admin-session/module-permission gate — this is bot-to-server
    traffic, authenticated the same way every other /api/v1/bot/* route is."""
    from sqlalchemy import func as sa_func
    from sqlalchemy.orm import joinedload
    from models import Department, DepartmentMember, DepartmentRatingEntry

    dept = db.query(Department).filter(Department.id == department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    totals = (
        db.query(DepartmentRatingEntry.admin_id, sa_func.sum(DepartmentRatingEntry.delta).label("total"))
        .filter(DepartmentRatingEntry.department_id == dept.id)
        .group_by(DepartmentRatingEntry.admin_id)
        .all()
    )
    totals_by_admin = {row.admin_id: row.total for row in totals}
    members = (
        db.query(DepartmentMember)
        .options(joinedload(DepartmentMember.admin))
        .filter(DepartmentMember.department_id == dept.id)
        .all()
    )
    leaderboard = sorted(
        (
            {
                "admin_id": m.admin_id,
                "admin_name": m.admin.discord_username if m.admin else None,
                "discord_id": m.admin.discord_id if m.admin else None,
                "total": totals_by_admin.get(m.admin_id, 0),
            }
            for m in members
        ),
        key=lambda r: r["total"],
        reverse=True,
    )[:limit]
    return {"department_id": dept.id, "name": dept.name, "leaderboard": leaderboard}


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
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


@panel_router.get("/adminold/discord-bot", response_class=HTMLResponse)
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


@panel_router.get("/api/v1/admin/discord-bot")
async def api_admin_discord_bot(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)

    members_count = db.query(DiscordMember).count()
    pending_actions = db.query(DiscordBotAction).filter(DiscordBotAction.executed == 0).count()
    last_member = db.query(DiscordMember).order_by(DiscordMember.last_synced_at.desc()).first()
    last_sync = last_member.last_synced_at.isoformat() if last_member and last_member.last_synced_at else None

    configs = db.query(DiscordBotConfig).all()
    config = {c.key: c.value for c in configs}

    # Recent failures of the site -> Discord role push (routes/departments.py's
    # _queue_role_sync) — surfaced here since these used to be silently
    # reported as "success" by the bot even when the actual Discord API call
    # failed (permission/role-hierarchy issues), making broken auto-sync
    # invisible. See discord_bot/bot.py's sync_department_role handler.
    failed_role_syncs_rows = (
        db.query(DiscordBotAction)
        .filter(DiscordBotAction.action_type == "sync_department_role", DiscordBotAction.executed == 2)
        .order_by(DiscordBotAction.executed_at.desc())
        .limit(20)
        .all()
    )
    failed_role_syncs = [
        {
            "id": a.id,
            "discord_id": (a.payload or {}).get("discord_id"),
            "add_role_id": (a.payload or {}).get("add_role_id"),
            "remove_role_id": (a.payload or {}).get("remove_role_id"),
            "error_message": a.error_message,
            "executed_at": a.executed_at.isoformat() if a.executed_at else None,
        }
        for a in failed_role_syncs_rows
    ]

    antiraid_events = db.query(DiscordAntiRaidLog).order_by(DiscordAntiRaidLog.occurred_at.desc()).limit(30).all()
    ar_list = [
        {
            "event_type": e.event_type, "discord_id": e.discord_id, "username": e.username,
            "details": e.details, "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "resolved": bool(e.resolved),
        }
        for e in antiraid_events
    ]

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
                        "new_discord_id": member.discord_id, "new_username": member.username,
                        "new_display_name": member.display_name,
                        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                        "similar_player_name": similar.player_last_time_name,
                    })
    except Exception:
        pass

    return {
        "members_count": members_count, "pending_actions": pending_actions, "last_sync": last_sync,
        "config": config, "antiraid_events": ar_list, "twink_suspects": twink_suspects,
        "failed_role_syncs": failed_role_syncs,
        "can_edit": check_permission(admin, "discord_bot", "edit"),
        "can_manage_config": check_permission(admin, "discord_bot", "manage_config"),
    }


@panel_router.post("/api/v1/admin/discord-bot/config")
async def api_admin_discord_bot_set_config(request: Request, db: Session = Depends(get_db)):
    """Set a DiscordBotConfig key/value from the admin-session-authenticated web panel
    (previously only settable via Discord bang-commands using the bot's own API key)."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    body = await request.json()
    key = (body.get("key") or "").strip()
    value = body.get("value")
    if not key:
        raise HTTPException(status_code=400, detail="key required")

    record = db.query(DiscordBotConfig).filter(DiscordBotConfig.key == key).first()
    if record:
        record.value = value
    else:
        record = DiscordBotConfig(key=key, value=value)
        db.add(record)
    db.commit()
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_bot_config_set", {"key": key})
    return {"status": "ok", "key": key, "value": value}


@panel_router.get("/api/v1/admin/discord-bot/role-sync/preview")
async def api_discord_role_sync_preview(request: Request, db: Session = Depends(get_db)):
    """Read-only diff of "site says this member should hold these department
    roles" vs DiscordMember.role_ids (last-known snapshot) — the site is the
    source of truth, this just finds where Discord hasn't caught up yet, in
    EITHER direction (add missing, remove stale). Nothing is queued here;
    the admin picks which rows to actually sync via the /apply endpoint
    below. Compare with workers.task_reconcile_department_discord_roles,
    which only ever adds automatically — this manual tool is reviewed by a
    human, so it's safe to also surface (and let them choose to apply)
    removals."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    from sqlalchemy.orm import joinedload
    from models import DepartmentMember, Department, DiscordMember

    memberships = (
        db.query(DepartmentMember)
        .join(Department, Department.id == DepartmentMember.department_id)
        .filter(Department.is_archived.is_(False))
        .options(joinedload(DepartmentMember.admin), joinedload(DepartmentMember.department).joinedload(Department.access_levels))
        .all()
    )

    rows = []
    for m in memberships:
        member_admin = m.admin
        if not member_admin or not member_admin.discord_id:
            continue
        dept = m.department

        managed_roles = set()
        if dept.base_discord_role_id:
            managed_roles.add(dept.base_discord_role_id)
        for level in dept.access_levels:
            if level.discord_role_id:
                managed_roles.add(level.discord_role_id)
        if not managed_roles:
            continue

        expected_roles = set()
        if dept.base_discord_role_id:
            expected_roles.add(dept.base_discord_role_id)
        held = m.held_level_keys or []
        for level in dept.access_levels:
            if level.key in held and level.discord_role_id:
                expected_roles.add(level.discord_role_id)

        snapshot = db.query(DiscordMember).filter(DiscordMember.discord_id == member_admin.discord_id).first()
        current_roles = set(snapshot.role_ids or []) if snapshot else set()

        add_role_ids = sorted(expected_roles - current_roles)
        # Only ever proposes removing a role THIS department's module
        # actually manages (base role or one of its access levels') — never
        # a role from another department or one granted manually in Discord
        # for unrelated reasons.
        remove_role_ids = sorted((current_roles & managed_roles) - expected_roles)
        if not add_role_ids and not remove_role_ids:
            continue

        rows.append({
            "admin_id": member_admin.id,
            "admin_name": member_admin.discord_username,
            "department_id": dept.id,
            "department_name": dept.name,
            "add_role_ids": add_role_ids,
            "remove_role_ids": remove_role_ids,
        })

    return {"rows": rows}


@panel_router.post("/api/v1/admin/discord-bot/role-sync/apply")
async def api_discord_role_sync_apply(request: Request, db: Session = Depends(get_db)):
    """Queue sync_department_role bot actions for exactly the rows the admin
    selected from the preview above — never re-derives the diff itself, so
    what gets applied is exactly what was reviewed on screen."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    from models import AdminUser as _AdminUser

    body = await request.json()
    selections = body.get("selections") or []
    queued = 0
    for sel in selections:
        member_admin = db.query(_AdminUser).filter(_AdminUser.id == sel.get("admin_id")).first()
        if not member_admin or not member_admin.discord_id:
            continue
        for role_id in sel.get("add_role_ids") or []:
            db.add(DiscordBotAction(
                action_type="sync_department_role",
                payload={"discord_id": member_admin.discord_id, "add_role_id": role_id},
                queued_by_admin_id=admin.id,
            ))
            queued += 1
        for role_id in sel.get("remove_role_ids") or []:
            db.add(DiscordBotAction(
                action_type="sync_department_role",
                payload={"discord_id": member_admin.discord_id, "remove_role_id": role_id},
                queued_by_admin_id=admin.id,
            ))
            queued += 1

    db.commit()
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_role_sync_manual_apply", {"queued": queued, "selections": len(selections)})
    return {"status": "ok", "queued": queued}


@panel_router.post("/api/v1/admin/discord-bot/action")
async def api_admin_discord_bot_send_action(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "broadcast"):
        raise HTTPException(status_code=403)

    body = await request.json()
    action_type = body.get("action_type")
    payload = body.get("payload") or {}
    if not action_type:
        raise HTTPException(status_code=400, detail="action_type required")

    action = DiscordBotAction(action_type=action_type, payload=payload, queued_by_admin_id=admin.id)
    db.add(action)
    db.commit()
    return {"status": "ok", "action_id": action.id}


@panel_router.post("/adminold/discord-bot/action")
async def admin_discord_bot_send_action(
    request: Request,
    action_type: str = Form(...),
    payload_json: str = Form("{}"),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "broadcast"):
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

    return RedirectResponse(url="/adminold/discord-bot", status_code=302)
