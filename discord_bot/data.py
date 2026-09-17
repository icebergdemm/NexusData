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
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlsplit

from database import SessionLocal


_TICKET_IPC_ACTION_TYPES = frozenset({"ticket_send_message", "ticket_close_channel"})
_TICKET_IPC_MARKER = "nexus-ticket-ipc-v1"


def enqueue_ticket_bot_action(
    action_type: str,
    payload: dict,
    *,
    queued_by_admin_id: int | None = None,
    dedupe_key: str | None = None,
) -> int:
    """Persist a ticket-only action for the process which owns the Discord bot.

    ``UVICORN_WORKERS>1`` deliberately runs HTTP and Discord in different OS
    processes.  The marker prevents old rows left by the removed generic bot
    queue from being replayed when this narrow IPC path is enabled again.
    """
    from models import DiscordBotAction

    if action_type not in _TICKET_IPC_ACTION_TYPES:
        raise ValueError("unsupported ticket IPC action")
    body = dict(payload or {})
    body["_nexus_ipc"] = _TICKET_IPC_MARKER
    if dedupe_key:
        body["_dedupe_key"] = str(dedupe_key)

    db = SessionLocal()
    try:
        if dedupe_key:
            candidates = db.query(DiscordBotAction).filter(
                DiscordBotAction.action_type == action_type,
                DiscordBotAction.executed.in_((0, 3)),
            ).with_for_update().all()
            for candidate in candidates:
                candidate_payload = candidate.payload if isinstance(candidate.payload, dict) else {}
                if (
                    candidate_payload.get("_nexus_ipc") == _TICKET_IPC_MARKER
                    and candidate_payload.get("_dedupe_key") == str(dedupe_key)
                ):
                    # The evidence-first closer may reach this same running
                    # row after Discord has accepted channel.delete() but the
                    # SQL Ticket close fails. Persist that irreversible phase
                    # transition on the existing action so its retry can
                    # distinguish our proven deletion from an unrelated
                    # manual channel removal.
                    if (
                        action_type == "ticket_close_channel"
                        and body.get("evidence_archive_confirmed") is True
                        and candidate_payload.get("evidence_archive_confirmed") is not True
                    ):
                        upgraded_payload = dict(candidate_payload)
                        upgraded_payload["evidence_archive_confirmed"] = True
                        candidate.payload = upgraded_payload
                        db.commit()
                    return int(candidate.id)

        action = DiscordBotAction(
            action_type=action_type,
            payload=body,
            queued_by_admin_id=queued_by_admin_id,
            executed=0,
        )
        db.add(action)
        db.commit()
        db.refresh(action)
        return int(action.id)
    finally:
        db.close()


def claim_next_ticket_bot_action() -> Optional[dict]:
    """Claim one marked ticket IPC action; never execute legacy queue rows."""
    from models import DiscordBotAction

    db = SessionLocal()
    try:
        while True:
            action = db.query(DiscordBotAction).filter(
                DiscordBotAction.action_type.in_(_TICKET_IPC_ACTION_TYPES),
                DiscordBotAction.executed == 0,
                (
                    (DiscordBotAction.executed_at.is_(None))
                    | (DiscordBotAction.executed_at <= datetime.utcnow())
                ),
            ).order_by(DiscordBotAction.id).with_for_update().first()
            if not action:
                return None
            payload = action.payload if isinstance(action.payload, dict) else {}
            if payload.get("_nexus_ipc") != _TICKET_IPC_MARKER:
                action.executed = 2
                action.executed_at = datetime.utcnow()
                action.error_message = "Ignored unmarked legacy bot action"
                db.commit()
                continue
            payload = dict(payload)
            payload["_execution_count"] = int(payload.get("_execution_count", 0) or 0) + 1
            action.payload = payload
            action.executed = 3
            action.executed_at = datetime.utcnow()
            action.error_message = None
            db.commit()
            return {
                "id": int(action.id),
                "action_type": action.action_type,
                "payload": dict(payload),
            }
    finally:
        db.close()


def finish_ticket_bot_action(action_id: int, success: bool, error: str | None = None) -> None:
    from models import DiscordBotAction

    db = SessionLocal()
    try:
        action = db.query(DiscordBotAction).filter(DiscordBotAction.id == int(action_id)).first()
        if not action:
            return
        payload = action.payload if isinstance(action.payload, dict) else {}
        if payload.get("_nexus_ipc") != _TICKET_IPC_MARKER:
            return
        action.executed = 1 if success else 2
        action.executed_at = datetime.utcnow()
        action.error_message = None if success else str(error or "Discord action failed")[:500]
        action.result = {"success": bool(success)}
        db.commit()
    finally:
        db.close()


def requeue_ticket_bot_action(
    action_id: int,
    error: str | None = None,
    *,
    delay_seconds: float = 0,
    increment_attempt: bool = False,
) -> None:
    """Return an interrupted in-flight action to the durable queue."""
    from models import DiscordBotAction

    db = SessionLocal()
    try:
        action = db.query(DiscordBotAction).filter(DiscordBotAction.id == int(action_id)).first()
        if not action:
            return
        payload = action.payload if isinstance(action.payload, dict) else {}
        if payload.get("_nexus_ipc") != _TICKET_IPC_MARKER:
            return
        if increment_attempt:
            updated_payload = dict(payload)
            updated_payload["_attempt"] = int(updated_payload.get("_attempt", 0) or 0) + 1
            action.payload = updated_payload
        action.executed = 0
        action.executed_at = (
            datetime.utcnow() + timedelta(seconds=max(0.0, float(delay_seconds)))
            if delay_seconds
            else None
        )
        action.error_message = str(error or "Interrupted; queued for retry")[:500]
        action.result = None
        db.commit()
    finally:
        db.close()


def recover_interrupted_ticket_bot_actions() -> int:
    """Requeue marked actions left in ``running`` state by a process crash."""
    from models import DiscordBotAction

    db = SessionLocal()
    recovered = 0
    try:
        actions = db.query(DiscordBotAction).filter(
            DiscordBotAction.action_type.in_(_TICKET_IPC_ACTION_TYPES),
            DiscordBotAction.executed == 3,
        ).all()
        for action in actions:
            payload = action.payload if isinstance(action.payload, dict) else {}
            if payload.get("_nexus_ipc") != _TICKET_IPC_MARKER:
                continue
            action.executed = 0
            action.executed_at = None
            action.error_message = "Recovered after Discord worker restart"
            action.result = None
            recovered += 1
        db.commit()
        return recovered
    finally:
        db.close()


def get_ticket_bot_action_status(action_id: int) -> Optional[dict]:
    from models import DiscordBotAction

    db = SessionLocal()
    try:
        action = db.query(DiscordBotAction).filter(DiscordBotAction.id == int(action_id)).first()
        if not action:
            return None
        payload = action.payload if isinstance(action.payload, dict) else {}
        if payload.get("_nexus_ipc") != _TICKET_IPC_MARKER:
            return None
        return {
            "id": int(action.id),
            "state": {0: "queued", 1: "done", 2: "failed", 3: "running"}.get(
                int(action.executed or 0), "failed"
            ),
            "success": bool((action.result or {}).get("success")) if action.executed == 1 else False,
            "error": action.error_message,
        }
    finally:
        db.close()


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


def record_punishment_discord_message(
    punishment_id: int,
    channel_id: int | str,
    message_id: int | str,
    state_hash: str | None,
) -> None:
    """Persist the Discord message created for one web punishment."""
    from models import PlayerPunishmentDiscordMessage

    now = datetime.utcnow()
    db = SessionLocal()
    try:
        link = db.query(PlayerPunishmentDiscordMessage).filter(
            PlayerPunishmentDiscordMessage.punishment_id == int(punishment_id),
            PlayerPunishmentDiscordMessage.channel_id == str(channel_id),
        ).first()
        if link is None:
            link = PlayerPunishmentDiscordMessage(
                punishment_id=int(punishment_id),
                channel_id=str(channel_id),
                message_id=str(message_id),
            )
            db.add(link)
        else:
            link.message_id = str(message_id)
        link.state_hash = state_hash
        link.sync_status = "synced"
        link.last_attempt_at = now
        link.synced_at = now
        link.last_error = None
        db.commit()
    finally:
        db.close()


def get_json_bot_config(key: str, default):
    """Read structured bot settings while keeping the simple key/value config table."""
    import json

    raw = get_bot_config().get(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def set_json_bot_config(key: str, value) -> None:
    import json

    set_bot_config(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def is_nd_autoban_enabled() -> bool:
    """The default preserves the former behaviour for existing installations."""
    return get_bot_config().get("nd_autoban_enabled", "1") != "0"


def set_nd_autoban_enabled(enabled: bool) -> None:
    set_bot_config("nd_autoban_enabled", "1" if enabled else "0")


def get_active_mutes() -> dict:
    """Return only unexpired mutes and prune stale records on read."""
    mutes = get_json_bot_config("discord_mutes", {})
    now = datetime.utcnow()
    active = {}
    for discord_id, value in mutes.items():
        try:
            if datetime.fromisoformat(value["expires_at"]) > now:
                active[str(discord_id)] = value
        except (KeyError, TypeError, ValueError):
            continue
    if active != mutes:
        set_json_bot_config("discord_mutes", active)
    return active


def set_mute(discord_id: str, expires_at: datetime, issued_by: dict, reason: str = "") -> None:
    mutes = get_active_mutes()
    mutes[str(discord_id)] = {
        "expires_at": expires_at.isoformat(), "issued_by": issued_by, "reason": reason,
    }
    set_json_bot_config("discord_mutes", mutes)


def remove_mute(discord_id: str) -> bool:
    mutes = get_active_mutes()
    if str(discord_id) not in mutes:
        return False
    del mutes[str(discord_id)]
    set_json_bot_config("discord_mutes", mutes)
    return True


def log_discord_command(action: str, details: dict) -> None:
    """Discord operators do not necessarily have a site account, so admin_id is NULL."""
    from models import AdminActionLog

    db = SessionLocal()
    try:
        db.add(AdminActionLog(admin_id=None, action=f"discord:{action}", details=details))
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
    from repositories import PlayerRepository
    from services import ServerModerationService
    from models import ServerLiveState

    db = SessionLocal()
    try:
        round_state = get_round_state(db, server_name)
        online_players = ServerModerationService(db).get_online_players(server_name=server_name)
        captains = []
        if round_state["active"]:
            captain_players = [
                player for player in online_players
                if str(player.get("job_id") or "").strip().casefold() == "captain"
            ]
            if captain_players:
                player_repository = PlayerRepository(db)
                for captain in captain_players:
                    profile = None
                    player_id = captain.get("player_id")
                    if player_id is not None:
                        try:
                            profile = player_repository.get_by_id(int(player_id))
                        except (TypeError, ValueError):
                            pass
                    if profile is None:
                        steamid = str(captain.get("player_steamid") or "").strip()
                        if steamid:
                            profile = player_repository.get_by_steamid(steamid)

                    discord_id = None
                    if (
                        profile is not None
                        and profile.discord_id
                        and profile.discord_id_confirmed == 1
                    ):
                        discord_id = str(profile.discord_id)
                    captains.append({
                        "name": str(captain.get("player_name") or "").strip() or "Неизвестный капитан",
                        "discord_id": discord_id,
                    })
        live = db.query(ServerLiveState).filter(ServerLiveState.server_name == server_name).first()
        return {
            "server_name": server_name,
            "active": round_state["active"],
            "location": round_state["location"],
            "submarine_name": round_state["submarine_name"],
            "mission": round_state["mission"],
            "started_at": round_state["started_at"].isoformat() if round_state["started_at"] else None,
            "player_count": len(online_players),
            "captains": captains,
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

def _aggregate_rating_records(records, limit: int):
    """Collapse role projections into one public board row per player."""
    grouped = {}
    for record in records:
        current = grouped.setdefault(record.steam_id, {
            "current_rating": 0.0,
            "cumulative_points": 0.0,
        })
        current["current_rating"] += float(record.current_rating or 0.0)
        current["cumulative_points"] += float(record.cumulative_points or 0.0)
    aggregated = [
        SimpleNamespace(
            steam_id=steam_id,
            current_rating=values["current_rating"],
            cumulative_points=values["cumulative_points"],
        )
        for steam_id, values in grouped.items()
    ]
    aggregated.sort(key=lambda item: (-item.current_rating, item.steam_id))
    return aggregated[:limit]

def get_ratings_leaderboard(role: str = "captain", limit: int = 10, mode: str = "mission") -> dict:
    """Return a role leaderboard for one rating mode.

    Older deployments have one PlayerRating table without a mode column. It
    remains the Mission source, but it must never be recycled as PvP data:
    a separate empty PvP board is more useful than a convincing lie.
    """
    from sqlalchemy import desc
    from rating_constants import ROLES
    from models import PlayerRating, PlayerData

    db = SessionLocal()
    try:
        if role != "all" and role not in ROLES:
            role = "captain"
        mode = mode if mode in {"mission", "pvp"} else "mission"
        try:
            from models import RatingProjectionState
        except ImportError:
            RatingProjectionState = None

        if RatingProjectionState is not None:
            query = db.query(RatingProjectionState).filter(
                RatingProjectionState.mode == mode,
                RatingProjectionState.current_rating > 0,
            )
            if role != "all":
                query = query.filter(RatingProjectionState.role == role)
            records = query.order_by(desc(RatingProjectionState.current_rating)).all()
            if role == "all":
                # PvP is one public board. Keep the calculation per role in
                # the admin UI, then aggregate the already-calculated role
                # totals here so Discord does not invent a second formula.
                records = _aggregate_rating_records(records, limit)
            else:
                records = records[:limit]
            # Keep the existing Mission board alive during the staged dev
            # rollout. An empty v2 projection is not proof that the old board
            # has been migrated; treating it as one would blank Discord for
            # no good reason.
            if not records and mode == "mission":
                legacy_query = db.query(PlayerRating).filter(PlayerRating.current_rating > 0)
                if role != "all":
                    legacy_query = legacy_query.filter(PlayerRating.role == role)
                legacy_records = legacy_query.order_by(desc(PlayerRating.current_rating)).all()
                if role == "all":
                    records = _aggregate_rating_records(legacy_records, limit)
                else:
                    records = legacy_records[:limit]
        else:
            # Pre-v2 fallback: legacy totals are Mission-only, never PvP.
            if mode == "pvp":
                return {"mode": mode, "role": role, "leaderboard": []}
            legacy_query = db.query(PlayerRating).filter(PlayerRating.current_rating > 0)
            if role != "all":
                legacy_query = legacy_query.filter(PlayerRating.role == role)
            legacy_records = legacy_query.order_by(desc(PlayerRating.current_rating)).all()
            if role == "all":
                records = _aggregate_rating_records(legacy_records, limit)
            else:
                records = legacy_records[:limit]
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
        return {"mode": mode, "role": role, "leaderboard": result}
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


def get_ticket_by_channel(channel_id: str, include_closed: bool = False) -> Optional[dict]:
    """Return the newest ticket bound to a Discord channel.

    Normal event ingestion uses ``include_closed=True`` so a last Gateway
    event racing with site-side closure is still archived. Interactive close
    flows keep the default and therefore cannot close an already closed row.
    """
    from models import Ticket

    db = SessionLocal()
    try:
        query = db.query(Ticket).filter(Ticket.discord_channel_id == str(channel_id))
        if not include_closed:
            query = query.filter(Ticket.status != "closed")
        ticket = query.order_by(Ticket.id.desc()).first()
        if not ticket:
            return None
        return {
            "id": ticket.id, "title": ticket.title, "ticket_type": ticket.ticket_type,
            "creator_discord_id": ticket.creator_discord_id,
            "status": ticket.status,
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
                "archive_state": getattr(t, "archive_state", None),
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


def get_ticket_message_by_discord_id(ticket_id: int, discord_message_id: str) -> Optional[dict]:
    """Return the locally archived pieces used to make Discord replay safe.

    Gateway reconnects and the final pre-delete history sweep can present the
    same Discord message more than once.  The bot uses this lightweight lookup
    to reuse already archived files instead of downloading them under fresh
    storage keys and leaving orphaned copies behind.
    """
    if not discord_message_id:
        return None

    from models import TicketMessage

    db = SessionLocal()
    try:
        message = db.query(TicketMessage).filter(
            TicketMessage.ticket_id == ticket_id,
            TicketMessage.discord_message_id == str(discord_message_id),
        ).first()
        if not message:
            return None
        return {
            "id": message.id,
            "attachments": message.attachments or [],
            "reference_snapshot": getattr(message, "reference_snapshot", None),
        }
    finally:
        db.close()


def get_ticket_message_archives(ticket_id: int) -> list[dict]:
    """Return every persisted media envelope for final close validation."""
    from models import TicketMessage

    db = SessionLocal()
    try:
        messages = db.query(TicketMessage).filter(TicketMessage.ticket_id == ticket_id).all()
        return [
            {
                "id": message.id,
                "discord_message_id": message.discord_message_id,
                "attachments": message.attachments or [],
                "reference_snapshot": getattr(message, "reference_snapshot", None),
            }
            for message in messages
        ]
    finally:
        db.close()


def _ticket_attachment_signature(values) -> tuple:
    signature = []
    for value in values or []:
        if isinstance(value, dict):
            filename = str(value.get("filename") or "")
            size = value.get("file_size", value.get("size", 0))
        else:
            filename = str(getattr(value, "filename", "") or "")
            size = getattr(value, "size", getattr(value, "file_size", 0))
        try:
            normalized_size = max(0, int(size or 0))
        except (TypeError, ValueError):
            normalized_size = 0
        signature.append((filename, normalized_size))
    return tuple(sorted(signature))


def link_site_ticket_message(
    ticket_id: int,
    discord_message_id: str,
    content: str | None,
    attachments,
    created_at=None,
) -> Optional[int]:
    """Attach an own-bot Discord echo to its pre-existing site message row.

    Site replies are written locally before Discord delivery. Gateway delivery
    and history backfill must therefore enrich that row, not create a second
    message authored by the bot. A tight timestamp plus exact text/file
    signature makes the legacy fallback deterministic enough for rows created
    before Discord IDs were persisted.
    """
    from models import Ticket, TicketMessage

    if not discord_message_id:
        return None
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).with_for_update().first()
        if not ticket:
            return None
        existing = db.query(TicketMessage).filter(
            TicketMessage.ticket_id == ticket_id,
            TicketMessage.discord_message_id == str(discord_message_id),
        ).first()
        if existing:
            return int(existing.id)

        timestamp = _discord_timestamp(created_at) or datetime.utcnow()
        lower = timestamp - timedelta(minutes=5)
        upper = timestamp + timedelta(minutes=5)
        candidates = db.query(TicketMessage).filter(
            TicketMessage.ticket_id == ticket_id,
            TicketMessage.is_bot == 1,
            TicketMessage.discord_message_id.is_(None),
            TicketMessage.created_at >= lower,
            TicketMessage.created_at <= upper,
        ).order_by(TicketMessage.created_at.desc(), TicketMessage.id.desc()).all()
        wanted_content = str(content or "")
        wanted_files = _ticket_attachment_signature(attachments)
        matching = [
            candidate for candidate in candidates
            if str(candidate.content or "") == wanted_content
            and _ticket_attachment_signature(candidate.attachments) == wanted_files
        ]
        if not matching:
            return None
        message = min(
            matching,
            key=lambda candidate: abs((candidate.created_at - timestamp).total_seconds()),
        )
        message.discord_message_id = str(discord_message_id)
        message.delivery_status = "sent"
        message.delivery_error = None
        db.commit()
        return int(message.id)
    finally:
        db.close()


def set_ticket_message_delivery(
    ticket_id: int,
    message_id: int,
    status: str,
    error: str | None = None,
    discord_message_id: str | None = None,
) -> bool:
    from models import TicketMessage

    db = SessionLocal()
    try:
        message = db.query(TicketMessage).filter(
            TicketMessage.id == int(message_id),
            TicketMessage.ticket_id == int(ticket_id),
        ).first()
        if not message:
            return False
        message.delivery_status = str(status)[:20]
        message.delivery_error = str(error)[:1000] if error else None
        if discord_message_id:
            message.discord_message_id = str(discord_message_id)
        db.commit()
        return True
    finally:
        db.close()


def get_ticket_message_delivery(ticket_id: int, message_id: int) -> Optional[dict]:
    """Return the durable delivery identity for an idempotent queued send."""
    from models import TicketMessage

    db = SessionLocal()
    try:
        message = db.query(TicketMessage).filter(
            TicketMessage.id == int(message_id),
            TicketMessage.ticket_id == int(ticket_id),
        ).first()
        if not message:
            return None
        return {
            "status": str(message.delivery_status or "received"),
            "discord_message_id": (
                str(message.discord_message_id) if message.discord_message_id else None
            ),
        }
    finally:
        db.close()


def ticket_is_stale_unanswered(ticket_id: int, cutoff) -> bool:
    """Revalidate a delayed auto-close immediately before touching Discord.

    A durable action can remain queued while the bot is disconnected.  In that
    time a moderator may answer the ticket, so eligibility must not be frozen
    at the scheduler's original scan.
    """
    from models import Ticket

    normalized_cutoff = _discord_timestamp(cutoff)
    if normalized_cutoff is None:
        return False
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == int(ticket_id)).first()
        if not ticket or ticket.status == "closed" or int(ticket.awaiting_admin or 0) != 1:
            return False
        reference = _discord_timestamp(ticket.last_message_at or ticket.created_at)
        return bool(reference and reference <= normalized_cutoff)
    finally:
        db.close()


def _discord_timestamp(value):
    """Normalize Discord's aware UTC datetime for MySQL's naive DATETIME."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


_DISCORD_CDN_HOSTS = frozenset({
    "cdn.discordapp.com", "media.discordapp.net", "cdn.discord.com",
})


def _discord_cdn_identities(url: str) -> set[tuple]:
    """Aliases for one Discord CDN object across signatures and hosts.

    Discord re-signs attachment links (``?ex=...&is=...&hm=...``) and serves the
    same object from both the CDN and the media proxy. Comparing raw strings
    made every refreshed link look like a brand new file, so a pre-archive row
    was duplicated instead of upgraded — and the duplicate, whose signature had
    long expired, could never be archived or cleared. The path carries the
    attachment id, which is stable across both.
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return set()
    if parsed.hostname not in _DISCORD_CDN_HOSTS or not parsed.path:
        return set()
    identities = {("cdn", parsed.path)}
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "attachments" and parts[2].isdigit():
        identities.add(("discord", parts[2]))
    return identities


def _attachment_identities(value) -> set[tuple]:
    """Return every stable alias so legacy URL rows can become rich records."""
    if isinstance(value, str):
        return ({("url", value)} | _discord_cdn_identities(value)) if value else set()
    if not isinstance(value, dict):
        return set()

    identities: set[tuple] = set()
    attachment_id = str(value.get("id") or value.get("discord_attachment_id") or "")
    if attachment_id:
        identities.add(("discord", attachment_id))
    storage_key = str(value.get("storage_key") or "")
    if storage_key:
        identities.add(("stored", storage_key))
    for key in ("url", "source_url", "proxy_url", "legacy_url"):
        url = str(value.get(key) or "")
        if url:
            identities.add(("url", url))
            identities |= _discord_cdn_identities(url)
    return identities


def _merge_ticket_attachments(existing, incoming) -> list:
    """Union evidence while allowing a retry to enrich a failed record."""
    result = []
    positions: dict[tuple, int] = {}
    for raw in [*(existing or []), *(incoming or [])]:
        item = dict(raw) if isinstance(raw, dict) else raw
        identities = _attachment_identities(item)
        matching_positions = {positions[identity] for identity in identities if identity in positions}
        if not matching_positions:
            index = len(result)
            result.append(item)
            for identity in identities:
                positions[identity] = index
            continue

        index = min(matching_positions)
        previous = result[index]
        if not isinstance(previous, dict) or not isinstance(item, dict):
            # Prefer the structured version when upgrading a historical bare
            # CDN URL; never downgrade a rich archived record back to a string.
            result[index] = item if isinstance(item, dict) else previous
            for identity in _attachment_identities(result[index]):
                positions[identity] = index
            continue
        merged = {**previous, **item}
        # A Discord history replay must never downgrade a file which is
        # already safely archived (e.g. the attachment was later removed).
        incoming_is_stored = item.get("archive_status") == "stored" and item.get("storage_key")
        if (
            previous.get("archive_status") == "stored"
            and previous.get("storage_key")
            and not incoming_is_stored
        ):
            for key in (
                "storage_key", "storage_namespace", "archive_status",
                "archive_error", "sha256", "archived_at", "file_size",
            ):
                merged[key] = previous.get(key)
        result[index] = merged
        for identity in _attachment_identities(merged):
            positions[identity] = index
    return result


def _reference_payloads(snapshot) -> list[dict]:
    if not isinstance(snapshot, dict):
        return []
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


def _merge_reference_snapshot(existing, incoming):
    """Normalize and merge reply/forward evidence without destructive sync."""
    if not isinstance(existing, dict):
        return incoming
    if not isinstance(incoming, dict):
        return existing

    old_payloads = _reference_payloads(existing)
    new_payloads = _reference_payloads(incoming)
    old_attachments = [raw for payload in old_payloads for raw in (payload.get("attachments") or [])]
    new_attachments = [raw for payload in new_payloads for raw in (payload.get("attachments") or [])]

    merged = {**existing, **incoming}
    # Store the stable application contract going forward. Values from legacy
    # Discord wrappers have already been lifted before the wrappers are dropped.
    merged.pop("message", None)
    merged.pop("message_snapshots", None)
    merged.pop("type", None)
    merged["kind"] = incoming.get("kind") or existing.get("kind") or existing.get("type") or "reply"
    merged["attachments"] = _merge_ticket_attachments(old_attachments, new_attachments)

    old_source = existing.get("source")
    new_source = incoming.get("source")
    if isinstance(old_source, dict) and isinstance(new_source, dict):
        merged["source"] = {**old_source, **{key: value for key, value in new_source.items() if value is not None}}

    for key in ("author", "content", "created_at", "edited_at", "embeds", "stickers"):
        if merged.get(key) not in (None, "", []):
            continue
        old_value = existing.get(key)
        if old_value in (None, "", []):
            old_value = next((payload.get(key) for payload in old_payloads if payload.get(key) not in (None, "", [])), None)
        if old_value not in (None, "", []):
            merged[key] = old_value
    return merged


def add_ticket_message(ticket_id: int, author_discord_id: str, author_username: str = None,
                        author_display_name: str = None, author_avatar_url: str = None,
                        content: str = None, attachments: list = None, discord_message_id: str = None,
                        reference_snapshot: dict = None, created_at=None,
                        is_bot: bool = False) -> Optional[int]:
    """Insert or enrich one Discord ticket message and return its local ID.

    ``discord_message_id`` is the idempotency key.  This matters both for
    reconnect-delivered gateway events and for the mandatory final channel
    history reconciliation before deletion.  Site-authored rows have no
    Discord ID and therefore retain the historical append-only behaviour.
    """
    from models import AdminUser, Ticket, TicketMessage

    db = SessionLocal()
    try:
        # Serialize concurrent Gateway/history upserts for the same ticket so
        # two workers cannot both miss the same Discord id and insert it.
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).with_for_update().first()
        if not ticket:
            return None
        message = None
        if discord_message_id:
            message = db.query(TicketMessage).filter(
                TicketMessage.ticket_id == ticket_id,
                TicketMessage.discord_message_id == str(discord_message_id),
            ).first()

        normalized_created_at = _discord_timestamp(created_at)
        if message:
            # A final history sweep can recover richer data than the original
            # event (for example after a transient attachment download error).
            message.author_discord_id = str(author_discord_id)
            message.author_username = author_username
            message.author_display_name = author_display_name
            message.author_avatar_url = author_avatar_url
            message.content = content
            message.is_bot = int(bool(is_bot))
            # A history fetch can occasionally omit attachment/snapshot data
            # that was present on the Gateway event. Never turn an already
            # durable archive into an empty one during an idempotent replay.
            if attachments is not None:
                message.attachments = _merge_ticket_attachments(message.attachments, attachments)
            message.reference_snapshot = _merge_reference_snapshot(
                message.reference_snapshot,
                reference_snapshot,
            )
            if normalized_created_at is not None:
                message.created_at = normalized_created_at
        else:
            message = TicketMessage(
                ticket_id=ticket_id,
                discord_message_id=str(discord_message_id) if discord_message_id else None,
                author_discord_id=str(author_discord_id),
                author_username=author_username,
                author_display_name=author_display_name,
                author_avatar_url=author_avatar_url,
                content=content,
                attachments=attachments or [],
                reference_snapshot=reference_snapshot,
                is_bot=int(bool(is_bot)),
            )
            if normalized_created_at is not None:
                message.created_at = normalized_created_at
            db.add(message)

        message_time = normalized_created_at or getattr(message, "created_at", None) or datetime.utcnow()
        current_last = _discord_timestamp(ticket.last_message_at)
        is_latest = current_last is None or message_time >= current_last
        if current_last is None or message_time > current_last:
            ticket.last_message_at = message_time

        # Reconciliation walks old history too. Only the chronologically
        # newest message may change the inbox's "awaiting admin" state.
        if is_latest:
            author_id = str(author_discord_id)
            if author_id == str(ticket.creator_discord_id):
                ticket.awaiting_admin = 1
            elif db.query(AdminUser.id).filter(
                AdminUser.discord_id == author_id,
                AdminUser.is_deleted == 0,
            ).first():
                ticket.awaiting_admin = 0
        db.commit()
        return message.id
    finally:
        db.close()


def mark_ticket_answered(ticket_id: int, created_at=None) -> bool:
    """Mark a ticket answered after Discord role-based staff verification."""
    from models import Ticket

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).with_for_update().first()
        if not ticket:
            return False
        staff_message_time = _discord_timestamp(created_at) or datetime.utcnow()
        current_last = _discord_timestamp(ticket.last_message_at)
        if current_last is None or staff_message_time >= current_last:
            ticket.awaiting_admin = 0
        db.commit()
        return True
    finally:
        db.close()


def close_ticket(
    ticket_id: int,
    closed_by: str,
    *,
    evidence_archive_confirmed: bool = False,
    archive_state: str = "complete",
    force: bool = False,
) -> bool:
    """Close a ticket, recording how trustworthy its archive actually is.

    ``archive_state`` is what the caller proved: ``complete`` after a verified
    sweep, ``partial`` when some evidence could not be mirrored and the closure
    was an explicit decision.  ``force`` additionally closes a ticket whose
    channel vanished out of band, which otherwise stays open for review.
    """
    from models import Ticket

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if not ticket:
            return False
        # An out-of-band Discord deletion has no remaining channel history to
        # reconcile.  Keep the ticket open and visible as an exception rather
        # than allowing a generic caller to claim a complete closure. A
        # moderator who accepts the loss can still close it explicitly, and
        # the incomplete archive stays recorded on the closed row.
        was_incomplete = getattr(ticket, "archive_state", None) == "incomplete"
        if was_incomplete and not (evidence_archive_confirmed or force):
            return False
        ticket.status = "closed"
        if was_incomplete and not evidence_archive_confirmed:
            ticket.archive_state = "incomplete"
        elif evidence_archive_confirmed:
            ticket.archive_state = archive_state or "complete"
        ticket.closed_by = closed_by
        ticket.closed_at = datetime.utcnow()
        db.commit()
        return True
    finally:
        db.close()


def mark_ticket_archive_incomplete(ticket_id: int) -> bool:
    """Persist a conservative exception for a channel deleted outside ND.

    The row remains open so moderators can see and recover it.  In particular,
    this helper never changes ``status`` to ``closed``: the Discord Gateway
    delete event cannot prove that history or attachments were archived.
    """
    from models import Ticket

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).with_for_update().first()
        if not ticket or ticket.status == "closed":
            return False
        ticket.archive_state = "incomplete"
        db.commit()
        return True
    finally:
        db.close()


def check_ticket_cooldown(creator_discord_id: str, ticket_type: str) -> Optional[datetime]:
    """Return expires_at if active cooldown exists, else None."""
    from models import TicketCooldown
    db = SessionLocal()
    try:
        rec = db.query(TicketCooldown).filter(
            TicketCooldown.creator_discord_id == str(creator_discord_id),
            TicketCooldown.ticket_type == ticket_type,
            TicketCooldown.expires_at > datetime.utcnow()
        ).order_by(TicketCooldown.expires_at.desc()).first()
        return rec.expires_at if rec else None
    finally:
        db.close()


def set_ticket_cooldown(creator_discord_id: str, ticket_type: str, days: int) -> None:
    from models import TicketCooldown
    if days <= 0:
        return
    db = SessionLocal()
    try:
        expires = datetime.utcnow() + timedelta(days=days)
        rec = TicketCooldown(creator_discord_id=str(creator_discord_id), ticket_type=ticket_type, expires_at=expires)
        db.add(rec); db.commit()
    finally:
        db.close()
