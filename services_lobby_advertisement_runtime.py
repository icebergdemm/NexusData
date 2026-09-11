"""Runtime service for one-shot Discord advertisements between rounds."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal
from models import LobbyAdvertisementConfig, LobbyAdvertisementCycle, PlayerCommunication, RoundEvent
from services import ServerModerationService
from services_lobby_advertisement import (
    build_lobby_ad_command,
    build_lobby_ad_message,
    decide_lobby_ad_action,
    lobby_keyword_matches,
)
from services_server_restart import PterodactylClient, PterodactylError, normalize_server


SERVERS = ("ASPER1", "ASPER2")
DEFAULT_DELAY_SECONDS = 120
DEFAULT_MESSAGE = "[Администрация]: Наш discord сервер: {discord_link} (прямая ссылка) — Вводить в браузере"
DEFAULT_FALLBACK_URL = "ds-asper.ru"
DEFAULT_KEYWORDS = ["устав", "правила", "дискорд", "сервер"]
PROCESSING_LEASE = timedelta(seconds=60)
ACTIVE_SERVER_STATE = "running"


def get_round_state(db: Session, server_name: str) -> dict[str, Any]:
    """Load dispatch round state lazily to avoid the routes package cycle."""
    from routes.dispatch import get_round_state as dispatch_get_round_state

    return dispatch_get_round_state(db, server_name)


def _now() -> datetime:
    return datetime.utcnow()


def ensure_lobby_ad_configs(db: Session) -> list[LobbyAdvertisementConfig]:
    rows: list[LobbyAdvertisementConfig] = []
    for server in SERVERS:
        row = db.query(LobbyAdvertisementConfig).filter(
            LobbyAdvertisementConfig.server_name == server,
        ).first()
        if row is None:
            row = LobbyAdvertisementConfig(
                server_name=server,
                enabled=False,
                delay_seconds=DEFAULT_DELAY_SECONDS,
                message_text=DEFAULT_MESSAGE,
                fallback_url=DEFAULT_FALLBACK_URL,
                keywords=list(DEFAULT_KEYWORDS),
                early_trigger_enabled=True,
            )
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                row = db.query(LobbyAdvertisementConfig).filter(
                    LobbyAdvertisementConfig.server_name == server,
                ).first()
        if row is not None:
            rows.append(row)
    db.commit()
    return rows


def _latest_round_events(db: Session, server: str) -> tuple[RoundEvent | None, RoundEvent | None]:
    latest_start = db.query(RoundEvent).filter(
        RoundEvent.server_name == server,
        RoundEvent.event_type == "start",
    ).order_by(desc(RoundEvent.created_at), desc(RoundEvent.id)).first()
    latest_end = db.query(RoundEvent).filter(
        RoundEvent.server_name == server,
        RoundEvent.event_type == "end",
    ).order_by(desc(RoundEvent.created_at), desc(RoundEvent.id)).first()
    return latest_start, latest_end


def _current_cycle(db: Session, server: str, latest_end: RoundEvent | None) -> LobbyAdvertisementCycle | None:
    if latest_end is None:
        return None
    return db.query(LobbyAdvertisementCycle).filter(
        LobbyAdvertisementCycle.server_name == server,
        LobbyAdvertisementCycle.round_end_event_id == latest_end.id,
    ).first()


def _cycle_due_at(round_end_at: datetime, now: datetime, delay_seconds: int) -> datetime:
    """Calculate a due time without trusting a future database clock value."""
    anchor = min(round_end_at, now)
    return anchor + timedelta(seconds=max(0, int(delay_seconds or 0)))


def _ensure_cycle(
    db: Session,
    config: LobbyAdvertisementConfig,
    latest_end: RoundEvent | None,
    now: datetime | None = None,
) -> LobbyAdvertisementCycle | None:
    if latest_end is None:
        return None
    cycle = _current_cycle(db, config.server_name, latest_end)
    if cycle is not None:
        return cycle
    cycle = LobbyAdvertisementCycle(
        server_name=config.server_name,
        round_end_event_id=latest_end.id,
        due_at=_cycle_due_at(latest_end.created_at, now or _now(), config.delay_seconds),
        status="pending",
    )
    db.add(cycle)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _current_cycle(db, config.server_name, latest_end)
    db.refresh(cycle)
    return cycle


def _repair_pending_cycle_due_at(
    db: Session,
    config: LobbyAdvertisementConfig,
    cycle: LobbyAdvertisementCycle,
    now: datetime,
) -> None:
    """Rebase a pending cycle that was created with a future DB clock value."""
    if cycle.status != "pending" or cycle.due_at is None:
        return
    latest_allowed = now + timedelta(seconds=max(0, int(config.delay_seconds or 0)))
    if cycle.due_at <= latest_allowed:
        return
    cycle.due_at = latest_allowed
    db.commit()


def _lobby_keyword_hit(
    db: Session,
    server: str,
    latest_end: RoundEvent,
    latest_start: RoundEvent | None,
    keywords: list[str],
) -> bool:
    query = db.query(PlayerCommunication).filter(
        PlayerCommunication.server_name == server,
        PlayerCommunication.source == "voice",
        PlayerCommunication.asr_status == "recognized",
        PlayerCommunication.round_token.is_(None),
        PlayerCommunication.round_event_id.is_(None),
        PlayerCommunication.occurred_at >= latest_end.created_at,
    )
    if latest_start is not None and latest_start.created_at > latest_end.created_at:
        query = query.filter(PlayerCommunication.occurred_at < latest_start.created_at)
    for row in query.order_by(PlayerCommunication.occurred_at.asc()).all():
        text = row.transcript_text or row.original_text or ""
        if lobby_keyword_matches(text, keywords):
            return True
    return False


async def _fetch_discord_invite() -> str:
    from discord_bot import bot as discord_bot

    guild = discord_bot.bot.get_guild(discord_bot.GUILD_ID)
    if guild is None:
        raise RuntimeError("Discord guild is not available")
    invites = await guild.invites()
    now = datetime.now(timezone.utc)
    active = []
    for invite in invites:
        expires_at = getattr(invite, "expires_at", None)
        if expires_at is not None and expires_at <= now:
            continue
        max_uses = getattr(invite, "max_uses", 0) or 0
        uses = getattr(invite, "uses", 0) or 0
        if max_uses and uses >= max_uses:
            continue
        active.append(invite)
    if not active:
        raise RuntimeError("No active Discord invite is available")
    active.sort(key=lambda invite: (
        0 if not getattr(invite, "expires_at", None) else 1,
        -(getattr(invite, "created_at", datetime.min).timestamp() if getattr(invite, "created_at", None) else 0),
        str(getattr(invite, "code", "")),
    ))
    return str(active[0].url)


def resolve_discord_link(fallback_url: str) -> tuple[str, str, str | None]:
    """Return (link, source, error), using the configured fallback on failure."""
    fallback = (fallback_url or DEFAULT_FALLBACK_URL).strip() or DEFAULT_FALLBACK_URL
    try:
        from discord_bot import bot as discord_bot

        coroutine = _fetch_discord_invite()
        try:
            future = discord_bot.schedule(
                coroutine,
                description="Получение актуальной Discord-ссылки для lobby-рекламы",
            )
        except Exception:
            coroutine.close()
            raise
        return str(future.result(timeout=5)), "discord", None
    except Exception as exc:
        return fallback, "fallback", f"Discord invite unavailable: {exc}"


def _claim_cycle(db: Session, cycle: LobbyAdvertisementCycle, now: datetime) -> bool:
    token = secrets.token_hex(16)
    updated = db.query(LobbyAdvertisementCycle).filter(
        LobbyAdvertisementCycle.id == cycle.id,
        LobbyAdvertisementCycle.status == "pending",
        or_(
            LobbyAdvertisementCycle.processing_until.is_(None),
            LobbyAdvertisementCycle.processing_until < now,
        ),
    ).update({
        "status": "attempted",
        "processing_until": now + PROCESSING_LEASE,
        "processing_token": token,
        "attempted_at": now,
    }, synchronize_session=False)
    db.commit()
    return updated == 1


def _mark_skipped(db: Session, cycle: LobbyAdvertisementCycle, reason: str, now: datetime) -> None:
    cycle.status = "skipped"
    cycle.trigger_reason = reason
    cycle.skipped_at = now
    cycle.processing_until = None
    cycle.processing_token = None
    db.commit()


def _mark_error(db: Session, cycle: LobbyAdvertisementCycle, error: str) -> None:
    cycle.status = "error"
    cycle.last_error = str(error)[:2000]
    cycle.processing_until = None
    cycle.processing_token = None
    db.commit()


def _serialize_cycle(cycle: LobbyAdvertisementCycle | None) -> dict[str, Any] | None:
    if cycle is None:
        return None
    return {
        "id": cycle.id,
        "status": cycle.status,
        "due_at": f"{cycle.due_at.isoformat()}Z" if cycle.due_at else None,
        "trigger_reason": cycle.trigger_reason,
        "discord_link": cycle.discord_link,
        "discord_source": cycle.discord_source,
        "last_error": cycle.last_error,
        "attempted_at": f"{cycle.attempted_at.isoformat()}Z" if cycle.attempted_at else None,
        "sent_at": f"{cycle.sent_at.isoformat()}Z" if cycle.sent_at else None,
        "skipped_at": f"{cycle.skipped_at.isoformat()}Z" if cycle.skipped_at else None,
    }


def _server_runtime_state(client: PterodactylClient, server: str) -> tuple[str | None, str | None]:
    try:
        state = client.get_state_cached(server)
        return state, None
    except PterodactylError as exc:
        return None, str(exc)


def _snapshot_row(
    db: Session,
    config: LobbyAdvertisementConfig,
    client: PterodactylClient,
) -> dict[str, Any]:
    server = normalize_server(config.server_name)
    latest_start, latest_end = _latest_round_events(db, server)
    round_state = get_round_state(db, server)
    online_count = len(ServerModerationService(db).get_online_players(server_name=server))
    server_state, server_error = _server_runtime_state(client, server)
    cycle = _current_cycle(db, server, latest_end)
    now = _now()
    if cycle is None and config.enabled and not round_state.get("active"):
        cycle = _ensure_cycle(db, config, latest_end, now=now)
    elif cycle is not None and config.enabled and not round_state.get("active"):
        _repair_pending_cycle_due_at(db, config, cycle, now)
    lobby_active = bool(latest_end and (latest_start is None or latest_end.created_at > latest_start.created_at))
    if round_state.get("active"):
        current_stage = "round"
    elif cycle is None:
        current_stage = "lobby"
    elif cycle.status in {"pending", "attempted"}:
        current_stage = "waiting"
    elif cycle.status == "sent":
        current_stage = "shown"
    else:
        current_stage = "lobby"
    return {
        "server": server,
        "enabled": bool(config.enabled),
        "delay_seconds": int(config.delay_seconds or 0),
        "message": config.message_text,
        "fallback_url": config.fallback_url,
        "keywords": list(config.keywords or []),
        "early_trigger_enabled": bool(config.early_trigger_enabled),
        "online_count": online_count,
        "round_active": bool(round_state.get("active")),
        "lobby_active": lobby_active,
        "server_state": server_state,
        "server_running": server_state == ACTIVE_SERVER_STATE if server_state is not None else None,
        "server_error": server_error,
        "cycle": _serialize_cycle(cycle),
        "current_stage": current_stage,
    }


def lobby_advertisements_snapshot(db: Session) -> list[dict[str, Any]]:
    client = PterodactylClient()
    try:
        return [_snapshot_row(db, config, client) for config in ensure_lobby_ad_configs(db)]
    finally:
        client.close()


def save_lobby_ad_config(db: Session, server: str, payload: dict[str, Any], admin_id: int | None = None) -> LobbyAdvertisementConfig:
    normalized = normalize_server(server)
    row = db.query(LobbyAdvertisementConfig).filter(
        LobbyAdvertisementConfig.server_name == normalized,
    ).first()
    if row is None:
        ensure_lobby_ad_configs(db)
        row = db.query(LobbyAdvertisementConfig).filter(
            LobbyAdvertisementConfig.server_name == normalized,
        ).first()
    if row is None:
        raise ValueError("Настройка рекламы не найдена")

    if "enabled" in payload:
        row.enabled = bool(payload["enabled"])
    if "delay_seconds" in payload:
        row.delay_seconds = max(0, min(3600, int(payload["delay_seconds"])))
    if "message" in payload:
        message = str(payload["message"] or "").strip()
        if not message:
            raise ValueError("Текст рекламы не может быть пустым")
        row.message_text = message
    if "fallback_url" in payload:
        row.fallback_url = str(payload["fallback_url"] or "").strip() or DEFAULT_FALLBACK_URL
    if "keywords" in payload:
        keywords = payload["keywords"]
        if not isinstance(keywords, list):
            raise ValueError("Ключевые слова должны быть списком")
        row.keywords = [str(value).strip() for value in keywords if str(value).strip()][:30]
    if "early_trigger_enabled" in payload:
        row.early_trigger_enabled = bool(payload["early_trigger_enabled"])
    row.updated_by_admin_id = admin_id
    db.commit()
    db.refresh(row)
    return row


def send_lobby_advertisement_now(db: Session, server: str) -> dict[str, Any]:
    normalized = normalize_server(server)
    config = db.query(LobbyAdvertisementConfig).filter(
        LobbyAdvertisementConfig.server_name == normalized,
    ).first()
    if config is None:
        ensure_lobby_ad_configs(db)
        config = db.query(LobbyAdvertisementConfig).filter(
            LobbyAdvertisementConfig.server_name == normalized,
        ).first()
    client = PterodactylClient()
    try:
        server_state = client.get_state(normalized)
        if server_state != ACTIVE_SERVER_STATE:
            raise RuntimeError("Сервер не запущен в Pterodactyl")
        online_count = len(ServerModerationService(db).get_online_players(server_name=normalized))
        if online_count <= 0:
            raise RuntimeError("На сервере нет игроков")
        link, source, link_error = resolve_discord_link(config.fallback_url)
        message = build_lobby_ad_message(config.message_text, link)
        client.send_console_command(normalized, build_lobby_ad_command(message))
        return {"server": normalized, "message": message, "discord_link": link, "discord_source": source, "discord_error": link_error}
    finally:
        client.close()


def process_lobby_advertisements() -> dict[str, int]:
    db = SessionLocal()
    client = PterodactylClient()
    result = {"checked": 0, "sent": 0, "skipped": 0, "errors": 0}
    now = _now()
    try:
        for config in ensure_lobby_ad_configs(db):
            if not config.enabled:
                continue
            result["checked"] += 1
            server = normalize_server(config.server_name)
            latest_start, latest_end = _latest_round_events(db, server)
            round_state = get_round_state(db, server)
            cycle = _ensure_cycle(db, config, latest_end, now=now) if not round_state.get("active") else _current_cycle(db, server, latest_end)
            if cycle is None:
                continue
            _repair_pending_cycle_due_at(db, config, cycle, now)
            if cycle.status == "attempted":
                if cycle.processing_until and cycle.processing_until > now:
                    continue
                # The previous worker may have died after sending the command.
                # Do not resend and violate the one-shot guarantee; expose the
                # uncertain delivery as a terminal error for the operator.
                cycle.status = "error"
                cycle.last_error = "Предыдущая отправка прервалась; повтор не выполняется"
                cycle.processing_until = None
                cycle.processing_token = None
                db.commit()
                result["errors"] += 1
                continue
            if cycle.status != "pending":
                continue
            if latest_end is None:
                continue
            server_state, server_error = _server_runtime_state(client, server)
            online_count = len(ServerModerationService(db).get_online_players(server_name=server))
            keyword_hit = bool(config.early_trigger_enabled) and _lobby_keyword_hit(
                db,
                server,
                latest_end,
                latest_start,
                list(config.keywords or []),
            )
            action, reason = decide_lobby_ad_action(
                now=now,
                due_at=cycle.due_at,
                keyword_hit=keyword_hit,
                has_players=online_count > 0,
                server_running=server_state == ACTIVE_SERVER_STATE,
                round_active=bool(round_state.get("active")),
            )
            if action == "wait":
                continue
            if action == "skip":
                _mark_skipped(db, cycle, reason if server_error is None else "server_offline", now)
                result["skipped"] += 1
                continue
            if not _claim_cycle(db, cycle, now):
                continue
            try:
                link, source, link_error = resolve_discord_link(config.fallback_url)
                message = build_lobby_ad_message(config.message_text, link)
                command = build_lobby_ad_command(message)
                cycle.trigger_reason = reason
                cycle.discord_link = link
                cycle.discord_source = source
                cycle.last_error = link_error
                db.commit()
                client.send_console_command(server, command)
                cycle.status = "sent"
                cycle.sent_at = _now()
                cycle.processing_until = None
                cycle.processing_token = None
                db.commit()
                result["sent"] += 1
            except Exception as exc:
                _mark_error(db, cycle, str(exc))
                result["errors"] += 1
        return result
    finally:
        client.close()
        db.close()
