"""
API endpoints for ticket system (bot + web panel).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime
import secrets
import json
import os

from database import get_db
from security import check_permission
from config import get_settings
from models import (
    Ticket, TicketMessage, PlayerData, AdminUser, AdminSession,
)

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

    # Dispatch to the bot to send message in Discord channel
    from discord_bot import bot as discord_bot
    discord_bot.schedule(discord_bot.execute_bot_action("ticket_send_message", {
        "ticket_id": ticket_id,
        "channel_id": ticket.discord_channel_id,
        "content": content.strip(),
        "author_name": admin.discord_username,
    }))

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
        "player": {"player_id": player.player_id, "player_last_time_name": player.player_last_time_name, "forced_display_name": player.forced_display_name, "player_steamid": player.player_steamid} if player else None,
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

    from discord_bot import bot as discord_bot
    discord_bot.schedule(discord_bot.execute_bot_action("ticket_send_message", {
        "ticket_id": ticket_id, "channel_id": ticket.discord_channel_id, "content": content, "author_name": admin.discord_username,
    }))

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
        from discord_bot import bot as discord_bot
        discord_bot.schedule(discord_bot.execute_bot_action("ticket_close_channel", {
            "ticket_id": ticket_id, "channel_id": ticket.discord_channel_id,
        }))
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

    # Dispatch bot action to delete Discord channel
    if ticket.discord_channel_id:
        from discord_bot import bot as discord_bot
        discord_bot.schedule(discord_bot.execute_bot_action("ticket_close_channel", {
            "ticket_id": ticket_id,
            "channel_id": ticket.discord_channel_id,
        }))

    db.commit()
    return RedirectResponse(url="/adminold/tickets", status_code=302)
