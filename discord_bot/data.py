"""
Direct DB-access helpers for discord_bot/bot.py.

Ported from the now-removed bot-facing HTTP API (routes/discord_bot_api.py's
old `router`, routes/tickets_api.py's old `bot_router`) — the bot used to
reach these over a loopback HTTP call to its own API key (api_get/api_post).
Now the bot runs in-process, so bot.py just calls these directly. Every
function here is a plain synchronous call (its own SessionLocal(), like every
task in workers.py) — bot.py wraps each call in asyncio.to_thread(...) so the
blocking DB work never stalls the bot's asyncio event loop (same reasoning as
the rest of this app's run_in_threadpool usage).
"""
import os
from datetime import datetime
from typing import Optional

from database import SessionLocal


# ── Members sync ─────────────────────────────────────────────────────────

def sync_members(members: list, run_automatch: bool = True) -> dict:
    """Записывает порцию участников Discord.

    run_automatch=False используется при потоковой синхронизации порциями —
    автосопоставление вызывается один раз в конце (см. automatch_discord_ids),
    иначе тяжёлая операция повторялась бы на каждую порцию.
    """
    from models import DiscordMember

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        synced = 0
        for m in members:
            record = db.query(DiscordMember).filter(DiscordMember.discord_id == m["discord_id"]).first()
            if record:
                record.username = m.get("username")
                record.display_name = m.get("display_name")
                record.avatar = m.get("avatar")
                record.role_ids = m.get("role_ids") or []
                record.last_synced_at = now
            else:
                joined_at = m.get("joined_at")
                record = DiscordMember(
                    discord_id=m["discord_id"],
                    username=m.get("username"),
                    display_name=m.get("display_name"),
                    avatar=m.get("avatar"),
                    role_ids=m.get("role_ids") or [],
                    joined_at=datetime.fromisoformat(joined_at) if joined_at else None,
                    last_synced_at=now,
                )
                db.add(record)
            synced += 1
        db.commit()
        if run_automatch:
            _auto_match_discord_ids(db)
        return {"synced": synced}
    finally:
        db.close()


def automatch_discord_ids() -> None:
    """Автосопоставление имён Discord с игроками, отдельным вызовом.

    Нужен, когда участники синхронизируются порциями: сопоставление читает
    всех участников и всех неподтверждённых игроков разом, поэтому вызывать
    его на каждую порцию нельзя.
    """
    db = SessionLocal()
    try:
        _auto_match_discord_ids(db)
    finally:
        db.close()


def _auto_match_discord_ids(db):
    """Try to auto-match Discord members to players by display_name
    similarity. Only updates players where discord_id is NULL or
    discord_id_confirmed=0."""
    from models import DiscordMember, PlayerData

    members = db.query(DiscordMember).all()
    if not members:
        return
    players = db.query(PlayerData).filter(
        (PlayerData.discord_id.is_(None)) | (PlayerData.discord_id_confirmed == 0)
    ).all()
    if not players:
        return

    name_to_discord = {}
    for m in members:
        if m.display_name:
            name_to_discord[m.display_name.lower().strip()] = m.discord_id
        if m.username:
            name_to_discord[m.username.lower().strip()] = m.discord_id

    for player in players:
        if not player.player_last_time_name:
            continue
        matched = name_to_discord.get(player.player_last_time_name.lower().strip())
        if matched:
            player.discord_id = matched
            player.discord_id_confirmed = 0  # auto-matched, not confirmed

    db.commit()


# ── Bot config (channel bindings etc.) ───────────────────────────────────

def get_bot_config() -> dict:
    from models import DiscordBotConfig

    db = SessionLocal()
    try:
        return {c.key: c.value for c in db.query(DiscordBotConfig).all()}
    finally:
        db.close()


def set_bot_config(key: str, value: str) -> None:
    from models import DiscordBotConfig

    db = SessionLocal()
    try:
        record = db.query(DiscordBotConfig).filter(DiscordBotConfig.key == key).first()
        if record:
            record.value = value
        else:
            db.add(DiscordBotConfig(key=key, value=value))
        db.commit()
    finally:
        db.close()


# ── Anti-raid ─────────────────────────────────────────────────────────────

def report_antiraid_event(event_type: str, discord_id: str = None, username: str = None, details: dict = None) -> int:
    from models import DiscordAntiRaidLog

    db = SessionLocal()
    try:
        log = DiscordAntiRaidLog(event_type=event_type, discord_id=discord_id, username=username, details=details)
        db.add(log)
        db.commit()
        return log.id
    finally:
        db.close()


# ── Department chat mirroring ────────────────────────────────────────────

def list_department_chat_channel_ids() -> list:
    from models import DepartmentChatChannel

    db = SessionLocal()
    try:
        return list({row[0] for row in db.query(DepartmentChatChannel.discord_channel_id).all()})
    finally:
        db.close()


def ingest_department_chat_message(discord_channel_id: str, discord_message_id: str = None,
                                    author_name: str = None, author_avatar_url: str = None,
                                    content: str = None) -> bool:
    from models import DepartmentChatChannel, DepartmentChatMessage

    db = SessionLocal()
    try:
        channel = db.query(DepartmentChatChannel).filter(
            DepartmentChatChannel.discord_channel_id == discord_channel_id
        ).first()
        if not channel:
            return False
        db.add(DepartmentChatMessage(
            channel_id=channel.id,
            discord_message_id=discord_message_id,
            author_name=author_name,
            author_avatar_url=author_avatar_url,
            content=(content or "")[:4000],
        ))
        db.commit()
        return True
    finally:
        db.close()


# ── Round status embed data ──────────────────────────────────────────────

def get_round_status(server_name: str) -> dict:
    from routes.dispatch import get_round_state
    from services import ServerModerationService
    from models import ServerLiveState

    db = SessionLocal()
    try:
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
    finally:
        db.close()


# ── Message-id callbacks (sub-project / document Discord publish) ───────

def set_sub_project_message_id(project_id: int, message_id: str) -> bool:
    from models import SubProject

    db = SessionLocal()
    try:
        project = db.query(SubProject).filter(SubProject.id == project_id).first()
        if not project:
            return False
        project.discord_message_id = message_id
        db.commit()
        return True
    finally:
        db.close()


def set_document_message_id(document_id: int, message_id: str, channel_id: str) -> bool:
    from models import Document

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return False
        doc.discord_message_id = message_id
        doc.discord_channel_id = channel_id
        db.commit()
        return True
    finally:
        db.close()


# ── Ratings leaderboards (published as embeds) ───────────────────────────

def get_ratings_leaderboard(role: str = "captain", limit: int = 10) -> dict:
    from sqlalchemy import desc
    from rating_constants import ROLES
    from models import PlayerRating, PlayerData

    db = SessionLocal()
    try:
        if role not in ROLES:
            role = "captain"
        records = (
            db.query(PlayerRating)
            .filter(PlayerRating.role == role, PlayerRating.current_rating > 0)
            .order_by(desc(PlayerRating.current_rating))
            .limit(limit)
            .all()
        )
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
    finally:
        db.close()


def list_department_rating_channels() -> list:
    from models import Department

    db = SessionLocal()
    try:
        depts = db.query(Department).filter(
            Department.rating_channel_id.isnot(None), Department.is_archived.is_(False)
        ).all()
        return [
            {"department_id": d.id, "name": d.name, "icon": d.icon, "rating_channel_id": d.rating_channel_id}
            for d in depts
        ]
    finally:
        db.close()


def get_department_rating_leaderboard(department_id: int, limit: int = 10) -> Optional[dict]:
    from sqlalchemy import func as sa_func
    from sqlalchemy.orm import joinedload
    from models import Department, DepartmentMember, DepartmentRatingEntry

    db = SessionLocal()
    try:
        dept = db.query(Department).filter(Department.id == department_id).first()
        if not dept:
            return None

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
    finally:
        db.close()


# ── Tickets ───────────────────────────────────────────────────────────────

def _tickets_config_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "tickets.json")


def get_tickets_config() -> dict:
    import json

    try:
        with open(_tickets_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"types": {}, "category_id": "", "status_channel_id": "", "manager_role_ids": []}


def set_tickets_config(config: dict) -> None:
    """Перезаписать config/tickets.json целиком — вызывается со страницы
    «Discord бот» (routes/discord_bot_api.py), тем же файлом, который читает
    и бот. Отдельной таблицы под это не заводилось: файл и так был
    единственным источником правды, менять способ хранения ради веб-формы
    незачем."""
    import json

    with open(_tickets_config_path(), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_ticket_by_channel(channel_id: str) -> Optional[dict]:
    """Активный (не закрытый) тикет по ID канала — источник истины для всего,
    что должно работать по каналу, а не по значению, сохранённому в объекте
    вида (кнопке/View), которое переживает рестарт бота хуже, чем БД."""
    from models import Ticket

    db = SessionLocal()
    try:
        ticket = (
            db.query(Ticket)
            .filter(Ticket.discord_channel_id == str(channel_id), Ticket.status != "closed")
            .first()
        )
        if not ticket:
            return None
        return {
            "id": ticket.id, "title": ticket.title, "ticket_type": ticket.ticket_type,
            "creator_discord_id": ticket.creator_discord_id,
        }
    finally:
        db.close()


def get_open_tickets() -> list:
    from sqlalchemy import desc
    from models import Ticket

    db = SessionLocal()
    try:
        tickets = db.query(Ticket).filter(Ticket.status != "closed").order_by(desc(Ticket.created_at)).all()
        return [
            {
                "id": t.id,
                "type": t.ticket_type,
                "title": t.title,
                "status": t.status,
                "creator_discord_id": t.creator_discord_id,
                "creator_username": t.creator_username,
                "creator_display_name": t.creator_display_name,
                "discord_channel_id": t.discord_channel_id,
                "awaiting_admin": t.awaiting_admin,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
            }
            for t in tickets
        ]
    finally:
        db.close()


def create_ticket(ticket_type: str, title: str, creator_discord_id: str, creator_username: str = None,
                   creator_display_name: str = None, discord_channel_id: str = None) -> int:
    from models import Ticket, PlayerData

    db = SessionLocal()
    try:
        player = db.query(PlayerData).filter(PlayerData.discord_id == creator_discord_id).first()
        ticket = Ticket(
            ticket_type=ticket_type,
            title=title,
            status="open",
            discord_channel_id=discord_channel_id,
            creator_discord_id=creator_discord_id,
            creator_username=creator_username,
            creator_display_name=creator_display_name,
            player_id=player.player_id if player else None,
            awaiting_admin=1,
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        return ticket.id
    finally:
        db.close()


def add_ticket_message(ticket_id: int, author_discord_id: str, author_username: str = None,
                        author_display_name: str = None, author_avatar_url: str = None,
                        content: str = None, attachments: list = None, discord_message_id: str = None) -> bool:
    from models import Ticket, TicketMessage

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if not ticket:
            return False
        db.add(TicketMessage(
            ticket_id=ticket_id,
            discord_message_id=discord_message_id,
            author_discord_id=author_discord_id,
            author_username=author_username,
            author_display_name=author_display_name,
            author_avatar_url=author_avatar_url,
            content=content,
            attachments=attachments or [],
            is_bot=0,
        ))
        ticket.last_message_at = datetime.utcnow()
        if author_discord_id != ticket.creator_discord_id:
            ticket.awaiting_admin = 0
        db.commit()
        return True
    finally:
        db.close()


def close_ticket(ticket_id: int, closed_by: str) -> bool:
    from models import Ticket

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if not ticket:
            return False
        ticket.status = "closed"
        ticket.closed_by = closed_by
        ticket.closed_at = datetime.utcnow()
        db.commit()
        return True
    finally:
        db.close()
