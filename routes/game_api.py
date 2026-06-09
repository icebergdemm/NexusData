"""
Game Server API endpoints:
- Traps (queue via action system)
- Bans (list active, check)
- Punishments (create)
- Unban (queue via action system)
- Server privileges (groups + player assignments)
- Submarines list (public)
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field

from database import get_db
from security import verify_external_api_key_dependency
from config import get_settings
from models import (
    PlayerData, PlayerPunishment,
    ServerPrivilegeGroup, ServerPlayerPrivilege, ServerAction,
)

settings = get_settings()

router = APIRouter(
    prefix="/api/v1/game",
    tags=["game-api"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# Public router (no auth required for submarine list)
public_router = APIRouter(
    prefix="/api/v1/public",
    tags=["public"],
)

MSK = timezone(timedelta(hours=3))


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class TrapQueueRequest(BaseModel):
    player_steamid: str = Field(..., description="SteamID64 игрока")
    reason: Optional[str] = Field(None, description="Причина установки ловушки")
    server_name: Optional[str] = Field(None, description="Имя сервера (null = все серверы)")


class BanOut(BaseModel):
    id: int
    player_id: int
    player_steamid: Optional[str]
    player_ip: Optional[str]
    reason: Optional[str]
    issued_by_name: Optional[str]
    issued_at: Optional[datetime]
    expires_at: Optional[datetime]
    is_permanent: bool
    notes: Optional[str]

    class Config:
        from_attributes = True


class PunishmentCreateRequest(BaseModel):
    player_steamid: str = Field(..., description="SteamID64 игрока")
    type: str = Field(..., description="Тип: warning | reprimand | kick | ban")
    reason: Optional[str] = Field(None, description="Причина")
    expires_days: Optional[int] = Field(None, ge=1, description="Срок в днях (None = бессрочно)")
    issued_by_name: str = Field(default="GameServer", description="Кто выдал")
    notes: Optional[str] = None


class PunishmentOut(BaseModel):
    id: int
    player_id: int
    player_steamid: Optional[str]
    type: str
    reason: Optional[str]
    issued_by_name: Optional[str]
    issued_at: Optional[datetime]
    expires_at: Optional[datetime]
    is_active: int

    class Config:
        from_attributes = True


class PrivilegeGroupOut(BaseModel):
    id: int
    name: str
    group_key: str
    description: Optional[str]
    commands: list
    allowed_action_ids: list

    class Config:
        from_attributes = True


class PlayerPrivilegeOut(BaseModel):
    id: int
    steamid: str
    player_id: Optional[int]
    player_name: Optional[str] = None  # resolved from PlayerData
    server_group_id: Optional[int]
    group_key: Optional[str] = None
    commands: list
    allowed_action_ids: list
    reason: Optional[str]
    server_scope: Optional[str] = None

    class Config:
        from_attributes = True


class UnbanRequest(BaseModel):
    player_steamid: str = Field(..., description="SteamID64 игрока")
    reason: Optional[str] = Field(None, description="Причина разбана")
    issued_by_name: str = Field(default="GameServer", description="Кто инициировал разбан")
    server_name: Optional[str] = Field(None, description="Имя сервера (null = все серверы)")


# ══════════════════════════════════════════════════════════════════════════════
# TRAPS (через систему очереди действий)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/traps/queue")
def queue_trap(body: TrapQueueRequest, db: Session = Depends(get_db)):
    """
    Поставить ловушку в очередь действий сервера.
    Сервер получит задачу через GET /api/v1/server/actions и будет ждать входа игрока.
    Когда игрок войдёт — сервер отправляет ACK.
    При ACK ловушка создаётся в БД и срабатывает Discord webhook.
    """
    steamid = body.player_steamid.strip()

    action = ServerAction(
        action_type="trap",
        target_steamid=steamid,
        reason=body.reason,
        target_server=body.server_name or None,
        queued_by_admin_id=None,
        created_at=_now_msk(),
        
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    return {
        "status": "queued",
        "action_id": action.id,
        "action_type": "trap",
        "target_steamid": steamid,
        "server_name": body.server_name,
        "reason": body.reason,
    }


# ══════════════════════════════════════════════════════════════════════════════
# BANS (GET active bans list)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/bans", response_model=List[BanOut])
def get_active_bans(
    include_expired: bool = Query(False, description="Включить истёкшие баны"),
    db: Session = Depends(get_db),
):
    """
    Получить список активных банов.
    """
    now = _now_msk()
    query = db.query(PlayerPunishment).filter(PlayerPunishment.type == "ban")

    if not include_expired:
        query = query.filter(PlayerPunishment.is_active == 1)

    bans = query.order_by(PlayerPunishment.issued_at.desc()).all()

    result = []
    for b in bans:
        if not include_expired and b.expires_at and b.expires_at < now:
            continue
        result.append(BanOut(
            id=b.id,
            player_id=b.player_id,
            player_steamid=b.player_steamid,
            player_ip=b.player_ip,
            reason=b.reason,
            issued_by_name=b.issued_by_name,
            issued_at=b.issued_at,
            expires_at=b.expires_at,
            is_permanent=b.expires_at is None,
            notes=b.notes,
        ))

    return result


@router.get("/bans/check")
def check_ban(steamid: str = Query(...), db: Session = Depends(get_db)):
    """
    Проверить, забанен ли игрок.
    """
    now = _now_msk()
    ban = db.query(PlayerPunishment).filter(
        PlayerPunishment.type == "ban",
        PlayerPunishment.is_active == 1,
        PlayerPunishment.player_steamid == steamid,
    ).order_by(PlayerPunishment.issued_at.desc()).first()

    if not ban:
        return {"banned": False}

    if ban.expires_at and ban.expires_at < now:
        return {"banned": False, "note": "ban_expired"}

    return {
        "banned": True,
        "ban_id": ban.id,
        "reason": ban.reason,
        "expires_at": ban.expires_at.isoformat() if ban.expires_at else None,
        "is_permanent": ban.expires_at is None,
        "issued_by": ban.issued_by_name,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUNISHMENTS (POST create)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/punishments", response_model=PunishmentOut)
def create_punishment(body: PunishmentCreateRequest, db: Session = Depends(get_db)):
    """
    Создать наказание (warning, reprimand, kick, ban).
    """
    valid_types = ("warning", "reprimand", "kick", "ban")
    if body.type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid type. Must be one of: {valid_types}")

    steamid = body.player_steamid.strip()
    player = db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
    player_id = player.player_id if player else 0

    expires_at = None
    if body.expires_days and body.expires_days > 0:
        expires_at = _now_msk() + timedelta(days=body.expires_days)

    punishment = PlayerPunishment(
        player_id=player_id,
        player_steamid=steamid,
        player_ip=player.last_ip if player else None,
        type=body.type,
        reason=body.reason,
        issued_by_admin_id=None,
        issued_by_name=body.issued_by_name,
        issued_at=_now_msk(),
        expires_at=expires_at,
        is_active=1,
        notes=body.notes,
    )
    db.add(punishment)
    db.commit()
    db.refresh(punishment)

    # Queue Discord notification for the punishment
    _queue_punishment_discord_notification(db, punishment)

    return punishment


# ══════════════════════════════════════════════════════════════════════════════
# UNBAN
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/unban")
def unban_player(body: UnbanRequest, db: Session = Depends(get_db)):
    """
    Разбанить игрока:
    1. Все активные баны в БД помечаются неактивными
    2. Создаётся задача 'unban' в очередь действий
    """
    steamid = body.player_steamid.strip()
    if not steamid:
        raise HTTPException(status_code=400, detail="player_steamid required")

    # Mark all active bans as void
    active_bans = db.query(PlayerPunishment).filter(
        PlayerPunishment.type == "ban",
        PlayerPunishment.is_active == 1,
        PlayerPunishment.player_steamid == steamid,
    ).all()

    voided_count = 0
    for b in active_bans:
        b.is_active = 0
        voided_count += 1

    # Convert Steam64 → STEAM2 for the game server
    try:
        from utils import steam64_to_steam2
        steam2 = steam64_to_steam2(steamid)
    except Exception:
        steam2 = None

    # Queue unban action
    action = ServerAction(
        action_type="unban",
        target_steamid=steam2 or steamid,
        reason=body.reason or f"Unban by {body.issued_by_name}",
        target_server=body.server_name or None,
        queued_by_admin_id=None,
        created_at=_now_msk(),
        
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    return {
        "status": "queued",
        "action_id": action.id,
        "action_type": "unban",
        "target_steamid": steamid,
        "server_name": body.server_name,
        "voided_punishments": voided_count,
        "reason": body.reason,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SERVER PRIVILEGES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/privileges/groups", response_model=List[PrivilegeGroupOut])
def get_privilege_groups(db: Session = Depends(get_db)):
    """
    Получить все серверные группы привилегий.
    """
    groups = db.query(ServerPrivilegeGroup).order_by(ServerPrivilegeGroup.id).all()
    return groups


@router.get("/privileges/players", response_model=List[PlayerPrivilegeOut])
def get_player_privileges(
    server_name: Optional[str] = Query(None, description="Имя сервера (ASPER1 или ASPER2) для фильтрации привилегий"),
    db: Session = Depends(get_db),
):
    """
    Получить список всех игроков с серверными привилегиями.
    Если указан server_name — возвращает только привилегии для этого сервера (и глобальные).
    """
    from sqlalchemy import or_
    query = db.query(ServerPlayerPrivilege)
    if server_name:
        sn = server_name.strip().upper()
        query = query.filter(
            or_(
                ServerPlayerPrivilege.server_scope.is_(None),
                ServerPlayerPrivilege.server_scope == sn,
            )
        )
    records = query.all()

    # Batch load player names
    steamids = [r.steamid for r in records]
    player_map = {}
    if steamids:
        players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steamids)).all()
        player_map = {p.player_steamid: p.player_last_time_name for p in players}

    result = []
    for r in records:
        group_key = None
        if r.server_group_id:
            grp = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == r.server_group_id).first()
            if grp:
                group_key = grp.group_key

        result.append(PlayerPrivilegeOut(
            id=r.id,
            steamid=r.steamid,
            player_id=r.player_id,
            player_name=player_map.get(r.steamid),
            server_group_id=r.server_group_id,
            group_key=group_key or r.group_id,
            commands=r.commands or [],
            allowed_action_ids=r.allowed_action_ids or [],
            reason=r.reason,
            server_scope=r.server_scope,
        ))
    return result


@router.get("/privileges/player")
def get_player_privilege(
    steamid: str = Query(...),
    server_name: Optional[str] = Query(None, description="Имя сервера для фильтрации — возвращает привилегии для этого сервера"),
    db: Session = Depends(get_db),
):
    """
    Получить привилегии конкретного игрока (объединённые: группа + индивидуальные).
    Если указан server_name — применяется фильтр по server_scope.
    """
    from sqlalchemy import or_
    query = db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.steamid == steamid)
    if server_name:
        sn = server_name.strip().upper()
        query = query.filter(
            or_(
                ServerPlayerPrivilege.server_scope.is_(None),
                ServerPlayerPrivilege.server_scope == sn,
            )
        )
    record = query.first()

    if not record:
        return {"has_privileges": False, "steamid": steamid}

    commands = list(record.commands or [])
    action_ids = list(record.allowed_action_ids or [])
    group_key = record.group_id

    if record.server_group_id:
        grp = db.query(ServerPrivilegeGroup).filter(
            ServerPrivilegeGroup.id == record.server_group_id
        ).first()
        if grp:
            group_key = grp.group_key
            for cmd in (grp.commands or []):
                if cmd not in commands:
                    commands.append(cmd)
            for aid in (grp.allowed_action_ids or []):
                if aid not in action_ids:
                    action_ids.append(aid)

    # Resolve player name
    player = db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()

    return {
        "has_privileges": True,
        "steamid": steamid,
        "player_id": record.player_id,
        "player_name": player.player_last_time_name if player else None,
        "group_key": group_key,
        "commands": commands,
        "allowed_action_ids": action_ids,
        "reason": record.reason,
        "server_scope": record.server_scope,
    }


# ══════════════════════════════════════════════════════════════════════════════
# RATINGS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/ratings/{steam_id}")
def get_player_ratings_endpoint(steam_id: str, db: Session = Depends(get_db)):
    """
    Get per-role ratings for a player. Applies lazy time decay on read.
    """
    if not steam_id or len(steam_id) < 10:
        raise HTTPException(status_code=400, detail="Invalid steam_id")

    from rating_engine import get_player_ratings
    ratings = get_player_ratings(db, steam_id)
    return {"steam_id": steam_id, "ratings": ratings}


# ══════════════════════════════════════════════════════════════════════════════
# DISCORD PUNISHMENT NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

def _queue_punishment_discord_notification(db, punishment):
    """Queue a Discord bot action to notify about a new punishment."""
    from models import DiscordBotAction

    type_labels = {
        "warning": "⚠️ Предупреждение",
        "reprimand": "📋 Выговор",
        "kick": "👢 Кик",
        "ban": "🔨 Бан",
    }
    type_colors = {
        "warning": 0xffaa00,
        "reprimand": 0xff8800,
        "kick": 0xff4400,
        "ban": 0xcc0000,
    }

    embed = {
        "title": type_labels.get(punishment.type, punishment.type),
        "color": type_colors.get(punishment.type, 0xff0000),
        "fields": [
            {"name": "Игрок", "value": f"`{punishment.player_steamid}`", "inline": True},
            {"name": "Выдал", "value": punishment.issued_by_name or "—", "inline": True},
            {"name": "Причина", "value": punishment.reason or "Не указана", "inline": False},
        ],
        "footer": {"text": "NexusData Moderation"},
        "timestamp": datetime.now(tz=timezone(timedelta(hours=3))).isoformat(),
    }

    if punishment.expires_at:
        embed["fields"].append({
            "name": "Истекает",
            "value": punishment.expires_at.strftime("%d.%m.%Y %H:%M"),
            "inline": True,
        })

    # Send to all-punishments channel (uses bot config key)
    action_all = DiscordBotAction(
        action_type="send_embed",
        payload={
            "channel_config_key": "punishment_channel_id",
            "embed": embed,
        },
    )
    db.add(action_all)

    # Additionally for bans — send to bans-specific channel
    if punishment.type == "ban":
        action_ban = DiscordBotAction(
            action_type="send_embed",
            payload={
                "channel_config_key": "ban_channel_id",
                "embed": embed,
            },
        )
        db.add(action_ban)

    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# SUBMARINES PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def _parse_crew(crew_str: Optional[str]) -> dict:
    """Parse crew string in format 'min-avg-max' or legacy free text."""
    if not crew_str:
        return {"raw": None, "min": None, "avg": None, "max": None}
    parts = crew_str.split("-")
    try:
        if len(parts) == 3:
            return {
                "raw": crew_str,
                "min": int(parts[0]),
                "avg": int(parts[1]),
                "max": int(parts[2]),
            }
        elif len(parts) == 2:
            mn, mx = int(parts[0]), int(parts[1])
            return {"raw": crew_str, "min": mn, "avg": (mn + mx) // 2, "max": mx}
        elif len(parts) == 1:
            val = int(parts[0])
            return {"raw": crew_str, "min": val, "avg": val, "max": val}
    except (ValueError, TypeError):
        pass
    return {"raw": crew_str, "min": None, "avg": None, "max": None}


@public_router.get("/submarines")
def get_submarines_list(db: Session = Depends(get_db)):
    """
    Публичный список кораблей/субмарин с характеристиками экипажа.
    Поле crew хранится в формате 'min-avg-max' (например '5-7-10').
    """
    from models import SubProject, SubProjectVersion
    from sqlalchemy import desc

    projects = db.query(SubProject).order_by(SubProject.name).all()
    result = []
    for p in projects:
        # Get deployed versions
        latest_deployed = (
            db.query(SubProjectVersion)
            .filter(
                SubProjectVersion.project_id == p.id,
                (SubProjectVersion.deployed_server_1 == 1) | (SubProjectVersion.deployed_server_2 == 1),
            )
            .order_by(SubProjectVersion.version_number.desc())
            .first()
        )

        crew_info = _parse_crew(p.crew)
        result.append({
            "id": p.id,
            "name": p.name,
            "project_type": p.project_type,
            "author": p.author,
            "crew": crew_info,
            "crew_min": crew_info["min"],
            "crew_avg": crew_info["avg"],
            "crew_max": crew_info["max"],
            "weapons": p.weapons,
            "has_reactor": p.has_reactor,
            "max_speed": p.max_speed,
            "decks": p.decks,
            "description": p.description,
            "deployed_server_1": latest_deployed.deployed_server_1 == 1 if latest_deployed else False,
            "deployed_server_2": latest_deployed.deployed_server_2 == 1 if latest_deployed else False,
            "latest_version": latest_deployed.version_number if latest_deployed else None,
            "latest_filename": latest_deployed.filename if latest_deployed else None,
        })

    return {"submarines": result, "total": len(result)}


@public_router.get("/submarines/{submarine_id}")
def get_submarine_by_id(submarine_id: int, db: Session = Depends(get_db)):
    """Получить данные конкретного корабля по ID."""
    from models import SubProject, SubProjectVersion

    project = db.query(SubProject).filter(SubProject.id == submarine_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Submarine not found")

    versions = (
        db.query(SubProjectVersion)
        .filter(SubProjectVersion.project_id == submarine_id)
        .order_by(SubProjectVersion.version_number.desc())
        .all()
    )

    crew_info = _parse_crew(project.crew)
    return {
        "id": project.id,
        "name": project.name,
        "project_type": project.project_type,
        "author": project.author,
        "crew": crew_info,
        "crew_min": crew_info["min"],
        "crew_avg": crew_info["avg"],
        "crew_max": crew_info["max"],
        "weapons": project.weapons,
        "has_reactor": project.has_reactor,
        "fabricator_medfab": project.fabricator_medfab,
        "has_deconstructor": project.has_deconstructor,
        "docking_ports": project.docking_ports,
        "decks": project.decks,
        "max_speed": project.max_speed,
        "extra_equipment": project.extra_equipment,
        "features": project.features,
        "description": project.description,
        "versions": [
            {
                "version_number": v.version_number,
                "filename": v.filename,
                "file_size": v.file_size,
                "changelog": v.changelog,
                "deployed_server_1": v.deployed_server_1 == 1,
                "deployed_server_2": v.deployed_server_2 == 1,
                "uploaded_at": v.uploaded_at.isoformat() if v.uploaded_at else None,
            }
            for v in versions
        ],
    }
