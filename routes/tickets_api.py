"""
API endpoints for ticket system (bot + web panel).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
import asyncio
import hashlib
import mimetypes
import secrets
import json
import os
import re

import aiofiles

from database import get_db
from security import check_permission
from config import get_settings
from models import (
    Ticket, TicketMessage, PlayerData, PlayerSession, AdminUser, AdminSession, DiscordMember,
)
from services_ticket_management import (
    TicketStateError,
    apply_ticket_status_filter,
    restore_ticket,
    search_ticket_members,
    soft_delete_ticket,
)
from services_ticket_media import (
    TicketMediaQuotaExceeded,
    delete_ticket_media_file,
    find_archived_attachment,
    iter_file_range,
    new_storage_key,
    reserve_ticket_media,
    serialize_attachment,
    storage_path,
)
from services_request_limits import MultipartLimitExceeded, parse_bounded_multipart

settings = get_settings()

# Admin panel router (protected by session)
panel_router = APIRouter()

templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL: TICKETS
# ══════════════════════════════════════════════════════════════════════════════

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


def _ticket_query(db: Session, status: str):
    return apply_ticket_status_filter(db.query(Ticket), status)


@panel_router.get("/adminold/tickets", response_class=HTMLResponse)
async def admin_tickets_list(request: Request, status: str = Query("open"), db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    q = _ticket_query(db, status)

    tickets = q.order_by(desc(Ticket.created_at)).limit(100).all()

    # Load ticket config for type names
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'tickets.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            ticket_config = json.load(f)
    except Exception:
        ticket_config = {"types": {}}

    return templates.TemplateResponse("admin/tickets.html", {
        "request": request,
        "admin": admin,
        "tickets": tickets,
        "status_filter": status,
        "ticket_config": ticket_config,
    })


@panel_router.get("/adminold/tickets/{ticket_id}", response_class=HTMLResponse)
async def admin_ticket_detail(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    messages = db.query(TicketMessage).filter(
        TicketMessage.ticket_id == ticket_id
    ).order_by(TicketMessage.created_at).all()

    # Find player if linked
    player = None
    if ticket.player_id:
        player = db.query(PlayerData).filter(PlayerData.player_id == ticket.player_id).first()

    return templates.TemplateResponse("admin/ticket_detail.html", {
        "request": request,
        "admin": admin,
        "ticket": ticket,
        "messages": messages,
        # Keep ORM rows for the legacy date filter, but route every attachment
        # and forward/reply snapshot through the same protected serializer as
        # the React workspace. Raw storage metadata must never become an href.
        "serialized_messages": {
            message.id: _serialize_message(message) for message in messages
        },
        "player": player,
    })


@panel_router.post("/adminold/tickets/{ticket_id}/send")
async def admin_ticket_send_message(
    request: Request,
    ticket_id: int,
    content: str = Form(""),
    db: Session = Depends(get_db),
):
    """Send a message to a ticket from the web panel (via bot)."""
    admin = _get_admin(request, db)
    if not (
        check_permission(admin, "tickets", "view")
        and check_permission(admin, "tickets", "reply")
    ):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет нельзя изменять")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Закрытый тикет доступен только для чтения")
    if getattr(ticket, "archive_state", None) == "incomplete":
        raise HTTPException(
            status_code=409,
            detail="Discord-канал удалён; тикет доступен только для проверки неполного архива",
        )
    if not ticket.discord_channel_id:
        raise HTTPException(status_code=409, detail="Discord-канал тикета недоступен")

    content = content.strip()
    if not content:
        return RedirectResponse(url=f"/adminold/tickets/{ticket_id}", status_code=302)
    if len(content) > 2000:
        raise HTTPException(status_code=400, detail="Сообщение Discord не может быть длиннее 2000 символов")

    channel_id = ticket.discord_channel_id
    admin_id = int(admin.id)
    msg = TicketMessage(
        ticket_id=ticket_id,
        author_discord_id=str(admin.discord_id or f"admin:{admin_id}"),
        author_username=admin.discord_username,
        author_display_name=admin.discord_username,
        author_avatar_url=_admin_avatar(admin),
        content=content,
        is_bot=1,
        delivery_status="queued",
    )
    db.add(msg)
    ticket.last_message_at = datetime.utcnow()
    ticket.awaiting_admin = 0
    db.commit()
    db.refresh(msg)
    message_id = int(msg.id)
    db.rollback()

    from discord_bot import bot as discord_bot
    try:
        success, error, _action_id = await discord_bot.dispatch_ticket_bot_action(
            "ticket_send_message",
            {
                "ticket_id": ticket_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "content": content,
                "author_name": admin.discord_username,
            },
            timeout=60,
            queued_by_admin_id=admin_id,
            dedupe_key=f"ticket-send:{message_id}",
        )
    except Exception as exc:
        success, error = False, str(exc) or exc.__class__.__name__

    msg = db.query(TicketMessage).filter(
        TicketMessage.id == message_id,
        TicketMessage.ticket_id == ticket_id,
    ).first()
    if msg and msg.delivery_status != "sent":
        if success is True:
            msg.delivery_status = "sent"
            msg.delivery_error = None
        elif success is False:
            msg.delivery_status = "failed"
            msg.delivery_error = str(error or "Discord не подтвердил отправку")[:1000]
        db.commit()
    if success is False:
        raise HTTPException(
            status_code=502,
            detail=f"Сообщение сохранено, но не отправлено в Discord: {error}",
        )

    return RedirectResponse(url=f"/adminold/tickets/{ticket_id}", status_code=302)


def _load_ticket_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'tickets.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"types": {}}


def _admin_avatar(admin: AdminUser | None) -> str | None:
    if not admin or not admin.discord_avatar:
        return None
    value = str(admin.discord_avatar)
    if value.startswith(("http://", "https://")):
        return value
    if admin.discord_id:
        extension = "gif" if value.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{admin.discord_id}/{value}.{extension}?size=128"
    return None


def _serialize_admin(admin: AdminUser | None) -> dict | None:
    if not admin:
        return None
    return {
        "id": admin.id,
        "discord_id": admin.discord_id,
        "display_name": admin.discord_username,
        "role": admin.role,
        "avatar_url": _admin_avatar(admin),
    }


def _serialize_ticket(
    t: Ticket,
    assigned_admin: AdminUser | None = None,
    *,
    can_view_player: bool = True,
) -> dict:
    return {
        "id": t.id,
        "ticket_type": t.ticket_type,
        "title": t.title,
        "status": t.status,
        "archive_state": getattr(t, "archive_state", None),
        "discord_channel_id": t.discord_channel_id,
        "creator_discord_id": t.creator_discord_id,
        "creator_username": t.creator_username,
        "creator_display_name": t.creator_display_name,
        "player_id": t.player_id if can_view_player else None,
        "assigned_admin_id": getattr(t, "assigned_admin_id", None),
        "assigned_admin": _serialize_admin(assigned_admin),
        "closed_by": t.closed_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "deleted_at": t.deleted_at.isoformat() if t.deleted_at else None,
        "deleted_by": t.deleted_by,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "awaiting_admin": bool(t.awaiting_admin),
    }


def _reference_payloads(snapshot: dict) -> list[dict]:
    """Return message-shaped payloads from normalized and Discord snapshots."""
    wrapped = snapshot.get("message_snapshots") or []
    payloads = [
        item.get("message")
        for item in wrapped
        if isinstance(item, dict) and isinstance(item.get("message"), dict)
    ]
    if payloads:
        return payloads
    if isinstance(snapshot.get("message"), dict):
        return [snapshot["message"]]
    return [snapshot]


def _serialize_embed(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    image = raw.get("image") if isinstance(raw.get("image"), dict) else {}
    thumbnail = raw.get("thumbnail") if isinstance(raw.get("thumbnail"), dict) else {}
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    footer = raw.get("footer") if isinstance(raw.get("footer"), dict) else {}
    return {
        "title": raw.get("title"),
        "description": raw.get("description"),
        "url": raw.get("url"),
        "color": raw.get("color"),
        "author_name": raw.get("author_name") or author.get("name"),
        "footer_text": raw.get("footer_text") or footer.get("text"),
        "image_url": raw.get("image_url") or image.get("url"),
        "thumbnail_url": raw.get("thumbnail_url") or thumbnail.get("url"),
    }


def _serialize_reference(ticket_id: int, message_id: int, snapshot: dict | None) -> dict | None:
    """Normalize replies/forwards for the SPA without losing the raw archive.

    discord.py 2.5+ exposes forwarded messages in a nested
    ``message_snapshots[].message`` envelope, while historical rows and reply
    records are flatter.  Keeping the normalization here lets old and new
    tickets share one stable frontend contract.
    """
    if not isinstance(snapshot, dict):
        return None

    payloads = _reference_payloads(snapshot)
    source = dict(snapshot.get("source") or {}) if isinstance(snapshot.get("source"), dict) else snapshot.get("source")
    author = snapshot.get("author")
    if not author and isinstance(source, dict):
        author = source.get("author")
    if not author:
        author = next((payload.get("author") for payload in payloads if payload.get("author")), None)

    if isinstance(source, dict) and not source.get("jump_url"):
        guild_id, channel_id, source_message_id = (
            source.get("guild_id"), source.get("channel_id"), source.get("message_id")
        )
        if guild_id and channel_id and source_message_id:
            source["jump_url"] = f"https://discord.com/channels/{guild_id}/{channel_id}/{source_message_id}"

    attachments = []
    embeds = []
    contents = []
    created_at = snapshot.get("created_at")
    for payload in payloads:
        if payload.get("content"):
            contents.append(str(payload["content"]))
        created_at = created_at or payload.get("created_at")
        attachments.extend(
            serialize_attachment(ticket_id, message_id, raw)
            for raw in (payload.get("attachments") or [])
            if isinstance(raw, (dict, str))
        )
        embeds.extend(
            serialized
            for serialized in (_serialize_embed(raw) for raw in (payload.get("embeds") or []))
            if any(value is not None for value in serialized.values())
        )

    return {
        "kind": snapshot.get("kind") or snapshot.get("type") or "reply",
        "source": source,
        "author": author,
        "content": snapshot.get("content") or "\n\n".join(contents) or None,
        "created_at": created_at,
        "attachments": attachments,
        "embeds": embeds,
    }


def _serialize_message(message: TicketMessage) -> dict:
    raw_snapshot = getattr(message, "reference_snapshot", None)
    message_embeds = []
    if isinstance(raw_snapshot, dict):
        for raw in raw_snapshot.get("outer_embeds") or []:
            serialized = _serialize_embed(raw)
            if any(value is not None for value in serialized.values()):
                message_embeds.append(serialized)
        for sticker in raw_snapshot.get("outer_stickers") or []:
            if not isinstance(sticker, dict) or not sticker.get("url"):
                continue
            message_embeds.append({
                "title": sticker.get("name") or "Sticker",
                "description": None,
                "url": None,
                "color": None,
                "author_name": None,
                "footer_text": None,
                "image_url": sticker.get("url"),
                "thumbnail_url": None,
            })
    return {
        "id": message.id,
        "discord_message_id": message.discord_message_id,
        "author_discord_id": message.author_discord_id,
        "author_username": message.author_username,
        "author_display_name": message.author_display_name,
        "author_avatar_url": message.author_avatar_url,
        "content": message.content,
        "attachments": [
            serialize_attachment(message.ticket_id, message.id, raw)
            for raw in (message.attachments or [])
            if isinstance(raw, (dict, str))
        ],
        "reference_snapshot": (
            None
            if isinstance(raw_snapshot, dict) and raw_snapshot.get("kind") == "message_content"
            else _serialize_reference(message.ticket_id, message.id, raw_snapshot)
        ),
        "embeds": message_embeds,
        "is_bot": bool(message.is_bot),
        "delivery_status": getattr(message, "delivery_status", None) or "received",
        "delivery_error": getattr(message, "delivery_error", None),
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _raw_message_attachments(message: TicketMessage):
    for raw in message.attachments or []:
        if isinstance(raw, dict):
            yield raw
        elif isinstance(raw, str):
            yield {"url": raw}
    snapshot = getattr(message, "reference_snapshot", None)
    if isinstance(snapshot, dict):
        for payload in _reference_payloads(snapshot):
            for raw in payload.get("attachments") or []:
                if isinstance(raw, dict):
                    yield raw
                elif isinstance(raw, str):
                    yield {"url": raw}


def _archive_summary(messages: list[TicketMessage]) -> dict:
    count = total_bytes = stored = failed = legacy = pending = 0
    for message in messages:
        for raw in _raw_message_attachments(message):
            count += 1
            try:
                total_bytes += max(0, int(raw.get("file_size") or raw.get("size") or 0))
            except (TypeError, ValueError):
                pass
            state = str(raw.get("archive_status") or "").lower()
            if raw.get("storage_key") and state in ("", "stored"):
                stored += 1
            elif state in ("pending", "archiving"):
                pending += 1
            elif state in ("failed", "too_large", "quota_exceeded"):
                failed += 1
            else:
                legacy += 1
    if pending:
        state = "pending"
    elif failed:
        state = "partial" if stored or legacy else "failed"
    elif legacy:
        state = "partial" if stored else "legacy"
    else:
        state = "complete"
    return {
        "state": state,
        "attachment_count": count,
        "stored_count": stored,
        "total_bytes": total_bytes,
        "failed_count": failed,
        "legacy_count": legacy,
    }


def _can_view_player(admin: AdminUser, player: PlayerData) -> bool:
    return not bool(player.is_secret) or bool(admin.is_superuser) or check_permission(
        admin, "players_special", "view"
    )


def _serialize_player(admin: AdminUser, player: PlayerData | None, online: bool = False) -> dict | None:
    if not player or not _can_view_player(admin, player):
        return None
    return {
        "player_id": player.player_id,
        "player_last_time_name": player.player_last_time_name,
        "forced_display_name": player.forced_display_name,
        "player_steamid": player.player_steamid,
        "avatar_url": player.steam_avatar_url,
        "last_login_time": player.last_login_time.isoformat() if player.last_login_time else None,
        "online": online,
        "profile_url": f"/players/{player.player_id}",
    }


def _ticket_identity_links(
    db: Session,
    discord_ids: list[str],
) -> tuple[dict[str, PlayerData], dict[str, AdminUser]]:
    """Resolve Discord IDs both directly and through NexusData accounts.

    A platform player may be linked to an ``AdminUser`` while its historical
    ``PlayerData.discord_id`` is empty.  Ticket mentions must still lead to
    that player's platform profile.
    """
    admins = db.query(AdminUser).filter(
        AdminUser.discord_id.in_(discord_ids),
        AdminUser.is_deleted == 0,
    ).all()
    admin_by_discord = {str(row.discord_id): row for row in admins if row.discord_id}

    direct_players = db.query(PlayerData).filter(PlayerData.discord_id.in_(discord_ids)).all()
    player_by_discord = {
        str(row.discord_id): row for row in direct_players if row.discord_id
    }
    admin_ids = [row.id for row in admins]
    if admin_ids:
        linked_players = db.query(PlayerData).filter(
            PlayerData.linked_admin_id.in_(admin_ids)
        ).all()
        discord_by_admin = {
            row.id: discord_id for discord_id, row in admin_by_discord.items()
        }
        for player in linked_players:
            discord_id = discord_by_admin.get(player.linked_admin_id)
            if discord_id:
                player_by_discord.setdefault(discord_id, player)

    return player_by_discord, admin_by_discord


def _ticket_participants(
    db: Session, admin: AdminUser, ticket: Ticket, messages: list[TicketMessage]
) -> list[dict]:
    ordered_ids: list[str] = []
    identity: dict[str, dict] = {}

    def remember(discord_id, username=None, display_name=None, avatar_url=None):
        key = str(discord_id or "").strip()
        if not key or key == "system":
            return
        if key not in identity:
            ordered_ids.append(key)
            identity[key] = {"username": username, "display_name": display_name, "avatar_url": avatar_url, "count": 0, "last_at": None}
        row = identity[key]
        row["username"] = username or row["username"]
        row["display_name"] = display_name or row["display_name"]
        row["avatar_url"] = avatar_url or row["avatar_url"]

    remember(ticket.creator_discord_id, ticket.creator_username, ticket.creator_display_name)
    for message in messages:
        remember(message.author_discord_id, message.author_username, message.author_display_name, message.author_avatar_url)
        key = str(message.author_discord_id or "")
        if key in identity:
            identity[key]["count"] += 1
            identity[key]["last_at"] = message.created_at

    if not ordered_ids:
        return []
    player_by_id, admin_by_id = _ticket_identity_links(db, ordered_ids)
    discord_members = db.query(DiscordMember).filter(DiscordMember.discord_id.in_(ordered_ids)).all()
    member_by_id = {str(row.discord_id): row for row in discord_members if row.discord_id}
    player_ids = list({row.player_id for row in player_by_id.values()})
    online_ids = set()
    if player_ids:
        online_ids = {
            row[0]
            for row in db.query(PlayerSession.player_id).filter(
                PlayerSession.player_id.in_(player_ids), PlayerSession.logout_time.is_(None)
            ).all()
        }

    result = []
    for discord_id in ordered_ids:
        data = identity[discord_id]
        linked_player = player_by_id.get(discord_id)
        linked_admin = admin_by_id.get(discord_id)
        discord_member = member_by_id.get(discord_id)
        player_visible = not linked_player or _can_view_player(admin, linked_player)
        role = "creator" if discord_id == str(ticket.creator_discord_id) else ("staff" if linked_admin else "participant")
        result.append({
            "discord_id": discord_id,
            "username": data["username"] or (discord_member.username if discord_member else None),
            "display_name": data["display_name"] or (
                discord_member.display_name if discord_member else None
            ) or data["username"] or (
                discord_member.username if discord_member else None
            ) or discord_id,
            "avatar_url": data["avatar_url"] or (
                discord_member.avatar if discord_member else None
            ) or _admin_avatar(linked_admin) or (
                linked_player.steam_avatar_url if linked_player and player_visible else None
            ),
            "role": role,
            "message_count": data["count"],
            "last_message_at": data["last_at"].isoformat() if data["last_at"] else None,
            "is_assigned": bool(linked_admin and linked_admin.id == getattr(ticket, "assigned_admin_id", None)),
            "admin": _serialize_admin(linked_admin),
            "player": _serialize_player(admin, linked_player, bool(linked_player and linked_player.player_id in online_ids)),
        })
    return result


_DISCORD_USER_MENTION_RE = re.compile(r"<@!?(\d{5,25})>")


def _ticket_mention_profiles(
    db: Session,
    admin: AdminUser,
    messages: list[TicketMessage],
) -> dict[str, dict]:
    """Resolve Discord mention tokens for safe, useful rendering in the UI."""
    ordered_ids: list[str] = []
    seen: set[str] = set()

    def collect(value) -> None:
        if isinstance(value, str):
            for discord_id in _DISCORD_USER_MENTION_RE.findall(value):
                if discord_id not in seen:
                    seen.add(discord_id)
                    ordered_ids.append(discord_id)
        elif isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    for message in messages:
        collect(message.content)
        collect(getattr(message, "reference_snapshot", None))

    if not ordered_ids:
        return {}

    members = db.query(DiscordMember).filter(DiscordMember.discord_id.in_(ordered_ids)).all()
    player_by_id, admin_by_id = _ticket_identity_links(db, ordered_ids)
    member_by_id = {str(row.discord_id): row for row in members if row.discord_id}

    profiles: dict[str, dict] = {}
    for discord_id in ordered_ids:
        member = member_by_id.get(discord_id)
        player = player_by_id.get(discord_id)
        linked_admin = admin_by_id.get(discord_id)
        player_visible = bool(player and _can_view_player(admin, player))
        display_name = (
            (member.display_name if member else None)
            or (linked_admin.discord_username if linked_admin else None)
            or (member.username if member else None)
            or (player.forced_display_name if player and player_visible else None)
            or (player.player_last_time_name if player and player_visible else None)
            or discord_id
        )
        profiles[discord_id] = {
            "discord_id": discord_id,
            "username": member.username if member else None,
            "display_name": display_name,
            "platform_display_name": (
                (player.forced_display_name or player.player_last_time_name)
                if player and player_visible else None
            ),
            "avatar_url": (
                (member.avatar if member else None)
                or _admin_avatar(linked_admin)
                or (player.steam_avatar_url if player and player_visible else None)
            ),
            "player_id": player.player_id if player and player_visible else None,
            "profile_url": f"/players/{player.player_id}" if player and player_visible else None,
            "is_staff": bool(linked_admin),
        }
    return profiles


@panel_router.get("/api/v1/admin/tickets")
def api_tickets_list(request: Request, status: str = Query("open"), db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    q = _ticket_query(db, status)
    tickets = q.order_by(desc(Ticket.last_message_at), desc(Ticket.created_at)).limit(100).all()
    assigned_ids = {t.assigned_admin_id for t in tickets if getattr(t, "assigned_admin_id", None)}
    assigned = {
        row.id: row for row in db.query(AdminUser).filter(AdminUser.id.in_(assigned_ids)).all()
    } if assigned_ids else {}
    linked_player_ids = {ticket.player_id for ticket in tickets if ticket.player_id}
    linked_players = {
        row.player_id: row
        for row in db.query(PlayerData).filter(PlayerData.player_id.in_(linked_player_ids)).all()
    } if linked_player_ids else {}

    ticket_ids = [ticket.id for ticket in tickets]
    latest_by_ticket: dict[int, tuple] = {}
    participant_counts: dict[int, int] = {}
    if ticket_ids:
        ranked = db.query(
            TicketMessage.ticket_id.label("ticket_id"),
            TicketMessage.content.label("content"),
            TicketMessage.reference_snapshot.label("reference_snapshot"),
            func.row_number().over(
                partition_by=TicketMessage.ticket_id,
                order_by=(TicketMessage.created_at.desc(), TicketMessage.id.desc()),
            ).label("row_number"),
        ).filter(TicketMessage.ticket_id.in_(ticket_ids)).subquery()
        latest_by_ticket = {
            row.ticket_id: (row.content, row.reference_snapshot)
            for row in db.query(ranked).filter(ranked.c.row_number == 1).all()
        }
        participant_counts = {
            ticket_id: int(count or 0)
            for ticket_id, count in db.query(
                TicketMessage.ticket_id,
                func.count(func.distinct(TicketMessage.author_discord_id)),
            ).filter(
                TicketMessage.ticket_id.in_(ticket_ids),
                TicketMessage.author_discord_id != "system",
            ).group_by(TicketMessage.ticket_id).all()
        }

    serialized_tickets = []
    for ticket in tickets:
        linked_player = linked_players.get(ticket.player_id)
        row = _serialize_ticket(
            ticket,
            assigned.get(ticket.assigned_admin_id),
            can_view_player=not linked_player or _can_view_player(admin, linked_player),
        )
        content, reference = latest_by_ticket.get(ticket.id, (None, None))
        if not content and isinstance(reference, dict):
            content = reference.get("content")
            if not content:
                payloads = _reference_payloads(reference)
                content = next((payload.get("content") for payload in payloads if payload.get("content")), None)
        row["last_message_preview"] = " ".join(str(content or "").split())[:180] or None
        row["participant_count"] = max(1, participant_counts.get(ticket.id, 0))
        serialized_tickets.append(row)

    return {
        "tickets": serialized_tickets,
        "status_filter": status,
        "ticket_config": _load_ticket_config(),
        "can_edit": check_permission(admin, "tickets", "edit"),
        "can_assign": check_permission(admin, "tickets", "assign"),
        "can_delete": check_permission(admin, "tickets", "delete"),
    }


@panel_router.get("/api/v1/admin/tickets/members")
async def api_ticket_members(
    request: Request,
    search: str = Query(""),
    limit: int = Query(50),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    """Search the cached server members used by the ticket picker."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "assign"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для добавления участников")

    total, members = search_ticket_members(db, search, limit=limit, offset=offset)
    return {
        "total": total,
        "members": [
            {
                "discord_id": member.discord_id,
                "username": member.username,
                "display_name": member.display_name,
                "avatar_url": member.avatar,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            }
            for member in members
        ],
    }


@panel_router.get("/api/v1/admin/tickets/{ticket_id}")
def api_ticket_detail(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    messages = db.query(TicketMessage).filter(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at, TicketMessage.id).all()
    player = db.query(PlayerData).filter(PlayerData.player_id == ticket.player_id).first() if ticket.player_id else None
    assigned_admin = db.query(AdminUser).filter(AdminUser.id == ticket.assigned_admin_id).first() if getattr(ticket, "assigned_admin_id", None) else None
    online = bool(player and db.query(PlayerSession.id).filter(PlayerSession.player_id == player.player_id, PlayerSession.logout_time.is_(None)).first())
    can_reply = check_permission(admin, "tickets", "reply")
    can_close = check_permission(admin, "tickets", "close")
    can_assign = check_permission(admin, "tickets", "assign")
    can_delete = check_permission(admin, "tickets", "delete")

    return {
        "discord_guild_id": str(settings.discord_guild_id or "") or None,
        "ticket": _serialize_ticket(
            ticket,
            assigned_admin,
            can_view_player=not player or _can_view_player(admin, player),
        ),
        "player": _serialize_player(admin, player, online),
        "messages": [_serialize_message(message) for message in messages],
        "participants": _ticket_participants(db, admin, ticket, messages),
        "mention_profiles": _ticket_mention_profiles(db, admin, messages),
        "archive": _archive_summary(messages),
        "permissions": {
            "can_reply": can_reply,
            "can_close": can_close,
            "can_assign": can_assign,
            "can_delete": can_delete,
        },
        "ticket_config": _load_ticket_config(),
        "can_edit": check_permission(admin, "tickets", "edit"),
        "can_assign": can_assign,
        "can_delete": can_delete,
    }


@panel_router.get("/api/v1/admin/tickets/{ticket_id}/members")
async def api_ticket_assigned_members(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "assign"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для управления участниками")

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет недоступен для изменения")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Закрытый тикет недоступен для изменения")
    if not ticket.discord_channel_id:
        raise HTTPException(status_code=409, detail="У тикета нет Discord-канала")

    from discord_bot import bot as discord_bot
    from routes.discord_viewer_api import _call_bot

    result = await _call_bot(discord_bot.list_ticket_members(ticket.discord_channel_id))
    result_status = result.get("status") if isinstance(result, dict) else None
    if result_status != "ok":
        code = result.get("code") if isinstance(result, dict) else None
        status_code = 404 if code == "channel_not_found" else 502
        raise HTTPException(status_code=status_code, detail=result.get("detail", "Не удалось получить участников тикета из Discord"))

    return {"members": result.get("members", [])}


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/members")
async def api_ticket_add_member(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "assign"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для добавления участников")

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет недоступен для изменения")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Закрытый тикет недоступен для добавления участников")
    if not ticket.discord_channel_id:
        raise HTTPException(status_code=409, detail="У тикета нет Discord-канала")

    body = await request.json()
    discord_id = str(body.get("discord_id") or "").strip()
    if not discord_id.isdigit():
        raise HTTPException(status_code=400, detail="Выберите участника из списка Discord")

    member = db.query(DiscordMember).filter(DiscordMember.discord_id == discord_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Участник отсутствует в кеше Discord — обновите список")

    from discord_bot import bot as discord_bot
    from routes.discord_viewer_api import _call_bot

    result = await _call_bot(discord_bot.add_ticket_member(
        channel_id=ticket.discord_channel_id,
        member_id=discord_id,
        added_by=admin.discord_username,
        ticket_id=ticket.id,
    ))
    result_status = result.get("status") if isinstance(result, dict) else None
    if result_status == "already_present":
        return {"status": "already_present"}
    if result_status == "partial":
        raise HTTPException(status_code=502, detail=result.get("detail", "Доступ мог быть выдан, но сообщение не отправлено"))
    if result_status != "ok":
        code = result.get("code") if isinstance(result, dict) else None
        status_code = 404 if code in {"member_not_found", "channel_not_found"} else 502
        raise HTTPException(status_code=status_code, detail=result.get("detail", "Не удалось изменить доступ в Discord"))

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "ticket_member_add", {
        "ticket_id": ticket.id,
        "discord_id": discord_id,
        "channel_id": ticket.discord_channel_id,
        "discord_message_id": result.get("message_id"),
    })
    return {"status": "ok", "member": {"discord_id": discord_id, "display_name": member.display_name}}


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/members/{discord_id}/remove")
async def api_ticket_remove_member(request: Request, ticket_id: int, discord_id: str, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "assign"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для управления участниками")

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет недоступен для изменения")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Закрытый тикет недоступен для изменения")
    if not ticket.discord_channel_id:
        raise HTTPException(status_code=409, detail="У тикета нет Discord-канала")

    discord_id = str(discord_id).strip()
    if not discord_id.isdigit():
        raise HTTPException(status_code=400, detail="Выберите участника из списка Discord")

    from discord_bot import bot as discord_bot
    from routes.discord_viewer_api import _call_bot

    result = await _call_bot(discord_bot.remove_ticket_member(
        channel_id=ticket.discord_channel_id,
        member_id=discord_id,
        removed_by=admin.discord_username,
        ticket_id=ticket.id,
    ))
    result_status = result.get("status") if isinstance(result, dict) else None
    if result_status == "partial":
        raise HTTPException(status_code=502, detail=result.get("detail", "Доступ убран, но сообщение не отправлено"))
    if result_status not in {"ok", "already_absent"}:
        code = result.get("code") if isinstance(result, dict) else None
        status_code = 409 if code == "member_unremovable" else 404 if code in {"member_not_found", "channel_not_found"} else 502
        raise HTTPException(status_code=status_code, detail=result.get("detail", "Не удалось убрать доступ в Discord"))

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "ticket_member_remove", {
        "ticket_id": ticket.id,
        "discord_id": discord_id,
        "channel_id": ticket.discord_channel_id,
    })
    return {"status": result_status, "member": {"discord_id": discord_id}}


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/delete")
async def api_ticket_delete(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "delete"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для удаления тикетов")

    try:
        ticket = soft_delete_ticket(db, ticket_id, admin.discord_username)
        db.commit()
    except TicketStateError as exc:
        db.rollback()
        detail = str(exc)
        status_code = 409 if detail != "Тикет не найден" else 404
        raise HTTPException(status_code=status_code, detail=detail)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "ticket_delete", {
        "ticket_id": ticket.id,
        "title": ticket.title,
        "status": ticket.status,
    })
    return {"status": "deleted", "deleted_at": ticket.deleted_at.isoformat()}


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/restore")
async def api_ticket_restore(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "delete"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для восстановления тикетов")

    try:
        ticket = restore_ticket(db, ticket_id)
        db.commit()
    except TicketStateError as exc:
        db.rollback()
        detail = str(exc)
        status_code = 409 if detail != "Тикет не найден" else 404
        raise HTTPException(status_code=status_code, detail=detail)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "ticket_restore", {
        "ticket_id": ticket.id,
        "channel_id": ticket.discord_channel_id,
    })
    return {"status": "restored", "ticket_id": ticket.id}


@panel_router.get("/api/v1/admin/tickets/{ticket_id}/messages")
def api_ticket_message_updates(
    request: Request,
    ticket_id: int,
    after_id: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Return a cheap incremental message page for the messenger poller.

    Full ticket detail includes participant/profile joins and an archive audit;
    doing that every four seconds for every open moderator tab was needlessly
    expensive. The UI refreshes full detail only when this endpoint reports a
    real change.
    """
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    page_size = 200
    rows = db.query(TicketMessage).filter(
        TicketMessage.ticket_id == ticket_id,
        TicketMessage.id > after_id,
    ).order_by(TicketMessage.id).limit(page_size + 1).all()
    delivery_rows = db.query(
        TicketMessage.id,
        TicketMessage.delivery_status,
        TicketMessage.delivery_error,
    ).filter(TicketMessage.ticket_id == ticket_id).order_by(
        TicketMessage.id.desc()
    ).limit(20).all()
    return {
        "messages": [_serialize_message(message) for message in rows[:page_size]],
        "delivery_updates": [
            {
                "id": row.id,
                "delivery_status": row.delivery_status or "received",
                "delivery_error": row.delivery_error,
            }
            for row in delivery_rows
        ],
        "ticket_update": {
            "status": ticket.status,
            "archive_state": getattr(ticket, "archive_state", None),
            "closed_by": ticket.closed_by,
            "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
            "last_message_at": (
                ticket.last_message_at.isoformat() if ticket.last_message_at else None
            ),
            "awaiting_admin": bool(ticket.awaiting_admin),
        },
        "has_more": len(rows) > page_size,
    }


async def _close_uploads(uploads: list) -> None:
    """Release every Starlette spooled file, including rejected form fields."""
    for upload in uploads:
        close = getattr(upload, "close", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:
            # The request is already complete and archived files are independent
            # copies. A temporary-file cleanup failure must not duplicate a send.
            pass


async def _read_send_form(request: Request) -> tuple[str, list]:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        # Bound the stream while Starlette parses it. Checking UploadFile.size
        # afterwards is too late: an authenticated user could already have
        # filled the temporary filesystem with a chunked oversized request.
        file_budget = min(
            int(settings.ticket_attachment_message_max_bytes),
            int(settings.ticket_web_upload_max_bytes)
            * int(settings.ticket_web_upload_max_files),
        )
        body_budget = file_budget + 1024 * 1024  # boundaries, names and text
        try:
            form = await parse_bounded_multipart(
                request.headers,
                request.stream(),
                max_files=int(settings.ticket_web_upload_max_files),
                max_fields=10,
                max_file_bytes=int(settings.ticket_web_upload_max_bytes),
                max_body_bytes=body_budget,
            )
        except MultipartLimitExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        content = str(form.get("content") or "").strip()
        all_uploads = [
            value for _key, value in form.multi_items()
            if hasattr(value, "read") and hasattr(value, "close")
        ]
        unexpected_uploads = [
            value for key, value in form.multi_items()
            if key != "files" and hasattr(value, "read") and hasattr(value, "close")
        ]
        if unexpected_uploads:
            await _close_uploads(all_uploads)
            raise HTTPException(
                status_code=400,
                detail="Файлы разрешены только в поле files",
            )
        files = [
            value for key, value in form.multi_items()
            if key == "files" and hasattr(value, "read")
        ]
        return content, files
    json_body_limit = 32 * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный Content-Length") from exc
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="Некорректный Content-Length")
        if declared_length > json_body_limit:
            raise HTTPException(status_code=413, detail="Тело сообщения слишком большое")

    raw_body = bytearray()
    try:
        async for chunk in request.stream():
            raw_body.extend(chunk)
            if len(raw_body) > json_body_limit:
                raise HTTPException(status_code=413, detail="Тело сообщения слишком большое")
        body = json.loads(bytes(raw_body))
    except HTTPException:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Некорректное тело запроса") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Тело запроса должно быть объектом")
    return str(body.get("content") or "").strip(), []


async def _store_web_upload(ticket_id: int, namespace: str, upload) -> tuple[dict, Path]:
    filename = Path(str(getattr(upload, "filename", "") or "file")).name or "file"
    storage_key = new_storage_key(filename)
    target = storage_path(ticket_id, namespace, storage_key)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    size = 0
    committed = False
    quota_reservation = None
    try:
        declared_size = max(0, int(getattr(upload, "size", 0) or 0))
        if declared_size > settings.ticket_web_upload_max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Файл {filename} превышает лимит "
                    f"{settings.ticket_web_upload_max_bytes // (1024 * 1024)} МБ"
                ),
            )
        # Initial quota accounting may scan the pre-existing archive tree.
        quota_reservation = await asyncio.to_thread(
            reserve_ticket_media,
            ticket_id,
            declared_size,
        )
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Re-resolve after mkdir to reject a pre-existing symlink introduced
        # below the configured media root.
        target = storage_path(ticket_id, namespace, storage_key)
        partial = target.with_name(target.name + ".part")
        async with aiofiles.open(partial, "wb") as output:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.ticket_web_upload_max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл {filename} превышает лимит {settings.ticket_web_upload_max_bytes // (1024 * 1024)} МБ",
                    )
                quota_reservation.grow_to(size)
                digest.update(chunk)
                await output.write(chunk)
        await asyncio.to_thread(os.chmod, partial, 0o600)
        await asyncio.to_thread(os.replace, partial, target)
        quota_reservation.commit(size)
        committed = True
    except TicketMediaQuotaExceeded as exc:
        try:
            partial.unlink(missing_ok=True)
            if committed:
                delete_ticket_media_file(ticket_id, target)
            else:
                target.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        label = "тикета" if exc.scope == "ticket" else "архива тикетов"
        raise HTTPException(
            status_code=413,
            detail=(
                f"Недостаточно места в квоте {label}: "
                f"использовано {exc.used_bytes} из {exc.limit_bytes} байт"
            ),
        ) from exc
    except BaseException:
        try:
            partial.unlink(missing_ok=True)
            if committed:
                delete_ticket_media_file(ticket_id, target)
            else:
                target.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        raise
    finally:
        if quota_reservation is not None:
            quota_reservation.release()
    now = datetime.utcnow().isoformat()
    return ({
        "id": storage_key,
        "filename": filename,
        "content_type": getattr(upload, "content_type", None) or "application/octet-stream",
        "file_size": size,
        "source_url": None,
        "storage_key": storage_key,
        "storage_namespace": namespace,
        "archive_status": "stored",
        "archive_error": None,
        "sha256": digest.hexdigest(),
        "archived_at": now,
    }, target)


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/send")
async def api_ticket_send_message(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not (
        check_permission(admin, "tickets", "view")
        and check_permission(admin, "tickets", "reply")
    ):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет нельзя изменять")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Закрытый тикет доступен только для чтения")
    if getattr(ticket, "archive_state", None) == "incomplete":
        raise HTTPException(
            status_code=409,
            detail="Discord-канал удалён; тикет доступен только для проверки неполного архива",
        )
    if not ticket.discord_channel_id:
        raise HTTPException(status_code=409, detail="Discord-канал тикета недоступен")
    channel_id = ticket.discord_channel_id
    admin_id = int(admin.id)
    admin_username = admin.discord_username
    admin_discord_id = admin.discord_id
    admin_avatar_url = _admin_avatar(admin)

    content, uploads = await _read_send_form(request)
    namespace = f"web-{secrets.token_hex(12)}"
    attachment_rows: list[dict] = []
    attachment_paths: list[dict] = []
    aggregate_size = 0
    try:
        if not content and not uploads:
            raise HTTPException(status_code=400, detail="Добавьте текст или вложение")
        if len(content) > 2000:
            raise HTTPException(status_code=400, detail="Сообщение Discord не может быть длиннее 2000 символов")
        if len(uploads) > settings.ticket_web_upload_max_files:
            raise HTTPException(status_code=413, detail=f"Можно прикрепить не более {settings.ticket_web_upload_max_files} файлов")

        for upload in uploads:
            row, path = await _store_web_upload(ticket_id, namespace, upload)
            aggregate_size += int(row["file_size"])
            attachment_rows.append(row)
            attachment_paths.append({
                "path": str(path),
                "filename": row["filename"],
                "file_size": int(row["file_size"]),
            })
            if aggregate_size > settings.ticket_attachment_message_max_bytes:
                raise HTTPException(status_code=413, detail="Суммарный размер вложений сообщения превышает лимит")
    except BaseException:
        for item in attachment_paths:
            try:
                path = Path(item["path"])
                delete_ticket_media_file(ticket_id, path)
            except (OSError, ValueError):
                pass
        raise
    finally:
        await _close_uploads(uploads)

    now = datetime.utcnow()
    msg = TicketMessage(
        ticket_id=ticket_id,
        author_discord_id=str(admin_discord_id or f"admin:{admin.id}"),
        author_username=admin_username,
        author_display_name=admin_username,
        author_avatar_url=admin_avatar_url,
        content=content or None,
        attachments=attachment_rows,
        is_bot=1,
        delivery_status="queued",
        created_at=now,
    )
    db.add(msg)
    ticket.last_message_at = now
    ticket.awaiting_admin = 0
    try:
        db.commit()
        db.refresh(msg)
        message_id = msg.id
        # Do not hold a MySQL pool connection while waiting on Discord. This
        # path may include uploading several large files and used to amplify
        # pool exhaustion during concurrent moderator replies.
        db.rollback()
    except Exception:
        db.rollback()
        for item in attachment_paths:
            try:
                path = Path(item["path"])
                delete_ticket_media_file(ticket_id, path)
            except (OSError, ValueError):
                pass
        raise

    from discord_bot import bot as discord_bot
    try:
        success, error, action_id = await discord_bot.dispatch_ticket_bot_action(
            "ticket_send_message",
            {
                "ticket_id": ticket_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "content": content,
                "author_name": admin_username,
                "attachment_paths": attachment_paths,
            },
            timeout=60,
            queued_by_admin_id=admin_id,
            dedupe_key=f"ticket-send:{message_id}",
        )
    except Exception as exc:
        success, error, action_id = False, str(exc) or exc.__class__.__name__, None
    msg = db.query(TicketMessage).filter(
        TicketMessage.id == message_id, TicketMessage.ticket_id == ticket_id
    ).first()
    if not msg:
        raise HTTPException(status_code=500, detail="Сообщение сохранено, но его статус доставки потерян")
    if msg.delivery_status == "sent":
        success = True
    elif success is True:
        msg.delivery_status = "sent"
        msg.delivery_error = None
        db.commit()
    elif success is False:
        msg.delivery_status = "failed"
        msg.delivery_error = str(error or "Discord не подтвердил отправку")[:1000]
        db.commit()
    if success is False:
        raise HTTPException(status_code=502, detail=f"Сообщение сохранено, но не отправлено в Discord: {msg.delivery_error}")
    if success is None:
        return JSONResponse(
            status_code=202,
            content={
                "status": "queued",
                "action_id": action_id,
                "detail": error,
                "message": _serialize_message(msg),
            },
        )
    return {"status": "ok", "message": _serialize_message(msg)}


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/assign")
async def api_ticket_assign(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not (
        check_permission(admin, "tickets", "view")
        and check_permission(admin, "tickets", "assign")
    ):
        raise HTTPException(status_code=403)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет нельзя изменять")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Закрытый тикет нельзя переназначить")
    ticket.assigned_admin_id = admin.id
    db.commit()
    return {"status": "ok", "assigned_admin": _serialize_admin(admin)}


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/close")
async def api_ticket_close(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not (
        check_permission(admin, "tickets", "view")
        and check_permission(admin, "tickets", "close")
    ):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет нельзя изменять")
    if ticket.status == "closed":
        return {"status": "ok"}
    if getattr(ticket, "archive_state", None) == "incomplete":
        raise HTTPException(
            status_code=409,
            detail="Канал Discord удалён до завершения архивации; тикет оставлен открытым для проверки",
        )
    channel_id = ticket.discord_channel_id
    admin_id = int(admin.id)
    closed_by = admin.discord_username
    if channel_id:
        # Release the request's DB transaction while Discord history and
        # potentially hundreds of media files are reconciled.
        db.rollback()
        from discord_bot import bot as discord_bot
        try:
            success, error, action_id = await discord_bot.dispatch_ticket_bot_action(
                "ticket_close_channel",
                {
                    "ticket_id": ticket_id,
                    "channel_id": channel_id,
                    "closed_by": closed_by,
                },
                timeout=15,
                queued_by_admin_id=admin_id,
                dedupe_key=f"ticket-close:{ticket_id}",
            )
        except Exception as exc:
            success, error, action_id = False, str(exc) or exc.__class__.__name__, None
        if success is False:
            raise HTTPException(
                status_code=503,
                detail=f"Канал оставлен открытым: архивирование не завершено ({error or 'неизвестная ошибка'})",
            )
        if success is None:
            return JSONResponse(
                status_code=202,
                content={"status": "queued", "action_id": action_id, "detail": error},
            )
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if not ticket:
            raise HTTPException(status_code=404)
        if ticket.status == "closed":
            return {"status": "ok"}
        raise HTTPException(
            status_code=503,
            detail="Discord подтвердил закрытие, но состояние тикета ещё не сохранено",
        )
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    ticket.status = "closed"
    ticket.closed_by = closed_by
    ticket.closed_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


_SAFE_INLINE_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
    "video/mp4", "video/webm", "video/ogg", "video/quicktime",
    "audio/aac", "audio/flac", "audio/mpeg", "audio/ogg", "audio/wav",
    "audio/webm", "audio/mp4",
}

_MEDIA_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
    "audio/x-m4a": "audio/mp4",
    "video/x-m4v": "video/mp4",
}


def _ticket_media_content_type(path: Path, filename: str, declared: object) -> str:
    """Return a browser-safe media type for an already archived file.

    Older Discord rows may have no MIME type (or ``application/octet-stream``),
    which makes the authenticated media endpoint download an otherwise valid
    image/video because it also sends ``nosniff``.  Prefer a known declared
    type, then the original filename, and finally a small set of unambiguous
    file signatures.  Unknown data remains a forced download.
    """
    normalized = str(declared or "").split(";", 1)[0].strip().lower()
    normalized = _MEDIA_TYPE_ALIASES.get(normalized, normalized)
    if normalized in _SAFE_INLINE_TYPES:
        return normalized

    guessed, _encoding = mimetypes.guess_type(filename, strict=False)
    guessed = _MEDIA_TYPE_ALIASES.get(str(guessed or "").lower(), str(guessed or "").lower())
    if guessed in _SAFE_INLINE_TYPES:
        return guessed

    try:
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError:
        return "application/octet-stream"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif"
        return "video/mp4"
    return "application/octet-stream"


def _content_disposition(filename: str, download: bool) -> str:
    disposition = "attachment" if download else "inline"
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in '\"\\' else "_" for ch in filename) or "file"
    return f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


@panel_router.get("/api/v1/admin/tickets/{ticket_id}/messages/{message_id}/media/{storage_key}")
def api_ticket_media(
    request: Request,
    ticket_id: int,
    message_id: int,
    storage_key: str,
    download: bool = Query(False),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)
    message = db.query(TicketMessage).filter(
        TicketMessage.id == message_id, TicketMessage.ticket_id == ticket_id
    ).first()
    if not message:
        raise HTTPException(status_code=404)
    raw = find_archived_attachment(message, storage_key)
    if not raw or not raw.get("storage_namespace"):
        raise HTTPException(status_code=404, detail="Вложение не найдено в архиве")
    try:
        path = storage_path(ticket_id, raw["storage_namespace"], storage_key)
    except ValueError:
        raise HTTPException(status_code=404)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Архивный файл отсутствует")

    size = path.stat().st_size
    filename = str(raw.get("filename") or storage_key or "file")
    content_type = _ticket_media_content_type(path, filename, raw.get("content_type"))
    force_download = download or content_type not in _SAFE_INLINE_TYPES
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": _content_disposition(filename, force_download),
    }
    range_header = request.headers.get("range")
    if not range_header:
        headers["Content-Length"] = str(size)
        return FileResponse(path, media_type=content_type, headers=headers)

    try:
        unit, value = range_header.strip().split("=", 1)
        if unit.lower() != "bytes" or "," in value:
            raise ValueError
        start_text, end_text = value.split("-", 1)
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                raise ValueError
            start, end = max(0, size - suffix), size - 1
        else:
            start = int(start_text)
            end = min(size - 1, int(end_text)) if end_text else size - 1
        if start < 0 or start >= size or end < start:
            raise ValueError
    except (TypeError, ValueError):
        return StreamingResponse(
            iter(()), status_code=416,
            headers={**headers, "Content-Range": f"bytes */{size}"},
            media_type=content_type,
        )
    length = end - start + 1
    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
    return StreamingResponse(
        iter_file_range(path, start, end), status_code=206, headers=headers, media_type=content_type
    )


@panel_router.get("/adminold/tickets/{ticket_id}/messages-json")
async def admin_ticket_messages_json(
    request: Request,
    ticket_id: int,
    after_id: int = Query(0),
    db: Session = Depends(get_db),
):
    """Get new messages after a given ID (for live polling)."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    query = db.query(TicketMessage).filter(TicketMessage.ticket_id == ticket_id)
    if after_id > 0:
        query = query.filter(TicketMessage.id > after_id)
    messages = query.order_by(TicketMessage.created_at).all()

    from fastapi.responses import JSONResponse
    serialized = []
    for message in messages:
        row = _serialize_message(message)
        row["created_at"] = (
            message.created_at.strftime("%d.%m %H:%M")
            if message.created_at else ""
        )
        serialized.append(row)
    return JSONResponse(serialized)


@panel_router.post("/adminold/tickets/{ticket_id}/close")
async def admin_ticket_close(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    """Close a ticket from the web panel."""
    admin = _get_admin(request, db)
    if not (
        check_permission(admin, "tickets", "view")
        and check_permission(admin, "tickets", "close")
    ):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    if ticket.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Удалённый тикет нельзя изменять")

    if ticket.status == "closed":
        return RedirectResponse(url="/adminold/tickets", status_code=302)
    if getattr(ticket, "archive_state", None) == "incomplete":
        raise HTTPException(
            status_code=409,
            detail="Канал Discord удалён до завершения архивации; тикет оставлен открытым для проверки",
        )

    channel_id = ticket.discord_channel_id
    admin_id = int(admin.id)
    closed_by = admin.discord_username
    if channel_id:
        # Match the modern API's evidence-first close semantics.  The legacy
        # form must not report success or mark the row closed until Discord
        # history and media have been durably reconciled and deletion is
        # confirmed.
        db.rollback()
        from discord_bot import bot as discord_bot
        try:
            success, error, _action_id = await discord_bot.dispatch_ticket_bot_action(
                "ticket_close_channel",
                {
                    "ticket_id": ticket_id,
                    "channel_id": channel_id,
                    "closed_by": closed_by,
                },
                timeout=15,
                queued_by_admin_id=admin_id,
                dedupe_key=f"ticket-close:{ticket_id}",
            )
        except Exception as exc:
            success, error = False, str(exc) or exc.__class__.__name__
        if success is False:
            raise HTTPException(
                status_code=503,
                detail=f"Канал оставлен открытым: архивирование не завершено ({error or 'неизвестная ошибка'})",
            )
        if success is None:
            return RedirectResponse(url="/adminold/tickets", status_code=302)
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if not ticket:
            raise HTTPException(status_code=404)
        if ticket.status == "closed":
            return RedirectResponse(url="/adminold/tickets", status_code=302)
        raise HTTPException(
            status_code=503,
            detail="Discord подтвердил закрытие, но состояние тикета ещё не сохранено",
        )

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    ticket.status = "closed"
    ticket.closed_by = closed_by
    ticket.closed_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/adminold/tickets", status_code=302)
