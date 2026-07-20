"""
API endpoints for ticket system (bot + web panel).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
import secrets
import json
import os

from database import get_db
from security import verify_external_api_key_dependency, check_permission
from config import get_settings
from models import (
    Ticket, TicketMessage, PlayerData, AdminUser, AdminSession,
    DiscordBotAction, DiscordMember,
)

settings = get_settings()

# Bot API router (protected by API key)
bot_router = APIRouter(
    prefix="/api/v1/tickets",
    tags=["tickets-bot"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# Admin panel router (protected by session)
panel_router = APIRouter()

templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class TicketCreate(BaseModel):
    ticket_type: str
    title: str
    creator_discord_id: str
    creator_username: Optional[str] = None
    creator_display_name: Optional[str] = None
    discord_channel_id: Optional[str] = None


class TicketMessageCreate(BaseModel):
    ticket_id: int
    author_discord_id: str
    author_username: Optional[str] = None
    author_display_name: Optional[str] = None
    author_avatar_url: Optional[str] = None
    content: Optional[str] = None
    attachments: Optional[list] = None
    discord_message_id: Optional[str] = None


class TicketClose(BaseModel):
    closed_by: str


# ══════════════════════════════════════════════════════════════════════════════
# BOT API: TICKETS
# ══════════════════════════════════════════════════════════════════════════════

@bot_router.post("/create")
def create_ticket(body: TicketCreate, db: Session = Depends(get_db)):
    """Bot creates a ticket when user clicks button."""
    # Try to find player by discord_id
    player = db.query(PlayerData).filter(PlayerData.discord_id == body.creator_discord_id).first()

    ticket = Ticket(
        ticket_type=body.ticket_type,
        title=body.title,
        status="open",
        discord_channel_id=body.discord_channel_id,
        creator_discord_id=body.creator_discord_id,
        creator_username=body.creator_username,
        creator_display_name=body.creator_display_name,
        player_id=player.player_id if player else None,
        awaiting_admin=1,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return {"status": "ok", "ticket_id": ticket.id}


@bot_router.post("/message")
def add_ticket_message(body: TicketMessageCreate, db: Session = Depends(get_db)):
    """Bot syncs a message from Discord to NexusData."""
    ticket = db.query(Ticket).filter(Ticket.id == body.ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    msg = TicketMessage(
        ticket_id=body.ticket_id,
        discord_message_id=body.discord_message_id,
        author_discord_id=body.author_discord_id,
        author_username=body.author_username,
        author_display_name=body.author_display_name,
        author_avatar_url=body.author_avatar_url,
        content=body.content,
        attachments=body.attachments or [],
        is_bot=0,
    )
    db.add(msg)

    # Update ticket last_message_at
    ticket.last_message_at = datetime.utcnow()

    # If message is from someone other than creator, mark as not awaiting admin
    if body.author_discord_id != ticket.creator_discord_id:
        ticket.awaiting_admin = 0

    db.commit()
    return {"status": "ok", "message_id": msg.id}


@bot_router.post("/{ticket_id}/close")
def close_ticket(ticket_id: int, body: TicketClose, db: Session = Depends(get_db)):
    """Close a ticket."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket.status = "closed"
    ticket.closed_by = body.closed_by
    ticket.closed_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


@bot_router.post("/{ticket_id}/set-channel")
def set_ticket_channel(ticket_id: int, channel_id: str = Query(...), db: Session = Depends(get_db)):
    """Set the Discord channel ID for a ticket."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.discord_channel_id = channel_id
    db.commit()
    return {"status": "ok"}


@bot_router.get("/open")
def get_open_tickets(db: Session = Depends(get_db)):
    """Get all open tickets (for status embed)."""
    tickets = db.query(Ticket).filter(Ticket.status != "closed").order_by(desc(Ticket.created_at)).all()
    return [
        {
            "id": t.id,
            "type": t.ticket_type,
            "title": t.title,
            "status": t.status,
            "creator_username": t.creator_username,
            "creator_display_name": t.creator_display_name,
            "discord_channel_id": t.discord_channel_id,
            "awaiting_admin": t.awaiting_admin,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        }
        for t in tickets
    ]


@bot_router.get("/config")
def get_tickets_config():
    """Get ticket types configuration."""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'tickets.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"types": {}, "category_id": "", "status_channel_id": ""}


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


@panel_router.get("/adminold/tickets", response_class=HTMLResponse)
async def admin_tickets_list(request: Request, status: str = Query("open"), db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    q = db.query(Ticket)
    if status == "open":
        q = q.filter(Ticket.status != "closed")
    elif status == "closed":
        q = q.filter(Ticket.status == "closed")

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
    if not check_permission(admin, "tickets", "reply"):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    if not content.strip():
        return RedirectResponse(url=f"/adminold/tickets/{ticket_id}", status_code=302)

    # Queue bot action to send message in Discord channel
    bot_action = DiscordBotAction(
        action_type="ticket_send_message",
        payload={
            "ticket_id": ticket_id,
            "channel_id": ticket.discord_channel_id,
            "content": content.strip(),
            "author_name": admin.discord_username,
        },
        queued_by_admin_id=admin.id,
    )
    db.add(bot_action)

    # Also save message locally
    msg = TicketMessage(
        ticket_id=ticket_id,
        author_discord_id="system",
        author_username=admin.discord_username,
        author_display_name=f"[ND] {admin.discord_username}",
        content=content.strip(),
        is_bot=1,
    )
    db.add(msg)

    ticket.last_message_at = datetime.utcnow()
    ticket.awaiting_admin = 0
    db.commit()

    return RedirectResponse(url=f"/adminold/tickets/{ticket_id}", status_code=302)


def _load_ticket_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'tickets.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"types": {}}


def _serialize_ticket(t: Ticket) -> dict:
    return {
        "id": t.id,
        "ticket_type": t.ticket_type,
        "title": t.title,
        "status": t.status,
        "discord_channel_id": t.discord_channel_id,
        "creator_discord_id": t.creator_discord_id,
        "creator_username": t.creator_username,
        "creator_display_name": t.creator_display_name,
        "player_id": t.player_id,
        "closed_by": t.closed_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "awaiting_admin": bool(t.awaiting_admin),
    }


@panel_router.get("/api/v1/admin/tickets")
async def api_tickets_list(request: Request, status: str = Query("open"), db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    q = db.query(Ticket)
    if status == "open":
        q = q.filter(Ticket.status != "closed")
    elif status == "closed":
        q = q.filter(Ticket.status == "closed")
    tickets = q.order_by(desc(Ticket.created_at)).limit(100).all()

    return {
        "tickets": [_serialize_ticket(t) for t in tickets],
        "status_filter": status,
        "ticket_config": _load_ticket_config(),
        "can_edit": check_permission(admin, "tickets", "edit"),
    }


@panel_router.get("/api/v1/admin/tickets/{ticket_id}")
async def api_ticket_detail(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "view"):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    messages = db.query(TicketMessage).filter(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at).all()
    player = db.query(PlayerData).filter(PlayerData.player_id == ticket.player_id).first() if ticket.player_id else None

    return {
        "ticket": _serialize_ticket(ticket),
        "player": {"player_id": player.player_id, "player_last_time_name": player.player_last_time_name, "player_steamid": player.player_steamid} if player else None,
        "messages": [
            {
                "id": m.id, "author_discord_id": m.author_discord_id, "author_username": m.author_username,
                "author_display_name": m.author_display_name, "author_avatar_url": m.author_avatar_url,
                "content": m.content, "attachments": m.attachments or [], "is_bot": bool(m.is_bot),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
        "ticket_config": _load_ticket_config(),
        "can_edit": check_permission(admin, "tickets", "edit"),
    }


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/send")
async def api_ticket_send_message(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "reply"):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")

    bot_action = DiscordBotAction(
        action_type="ticket_send_message",
        payload={"ticket_id": ticket_id, "channel_id": ticket.discord_channel_id, "content": content, "author_name": admin.discord_username},
        queued_by_admin_id=admin.id,
    )
    db.add(bot_action)

    msg = TicketMessage(
        ticket_id=ticket_id, author_discord_id="system", author_username=admin.discord_username,
        author_display_name=f"[ND] {admin.discord_username}", content=content, is_bot=1,
    )
    db.add(msg)
    ticket.last_message_at = datetime.utcnow()
    ticket.awaiting_admin = 0
    db.commit()
    return {"status": "ok"}


@panel_router.post("/api/v1/admin/tickets/{ticket_id}/close")
async def api_ticket_close(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "close"):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.status = "closed"
    ticket.closed_by = admin.discord_username
    ticket.closed_at = datetime.utcnow()

    if ticket.discord_channel_id:
        db.add(DiscordBotAction(
            action_type="ticket_close_channel",
            payload={"ticket_id": ticket_id, "channel_id": ticket.discord_channel_id},
            queued_by_admin_id=admin.id,
        ))
    db.commit()
    return {"status": "ok"}


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
    return JSONResponse([
        {
            "id": m.id,
            "author_discord_id": m.author_discord_id,
            "author_username": m.author_username,
            "author_display_name": m.author_display_name,
            "author_avatar_url": m.author_avatar_url,
            "content": m.content,
            "attachments": m.attachments or [],
            "is_bot": m.is_bot,
            "created_at": m.created_at.strftime("%d.%m %H:%M") if m.created_at else "",
        }
        for m in messages
    ])


@panel_router.post("/adminold/tickets/{ticket_id}/close")
async def admin_ticket_close(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    """Close a ticket from the web panel."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "tickets", "close"):
        raise HTTPException(status_code=403)

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.status = "closed"
    ticket.closed_by = admin.discord_username
    ticket.closed_at = datetime.utcnow()

    # Queue bot action to delete Discord channel
    if ticket.discord_channel_id:
        bot_action = DiscordBotAction(
            action_type="ticket_close_channel",
            payload={
                "ticket_id": ticket_id,
                "channel_id": ticket.discord_channel_id,
            },
            queued_by_admin_id=admin.id,
        )
        db.add(bot_action)

    db.commit()
    return RedirectResponse(url="/adminold/tickets", status_code=302)
