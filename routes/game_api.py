"""
Game Server API endpoints:
- Traps (queue via action system)
- Bans (list active, check)
- Punishments (create)
- Unban (queue via action system)
- Server privileges (groups + player assignments)
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
    ServerPrivilegeGroup, ServerPlayerPrivilege, ServerActionQueue,
)

settings = get_settings()

router = APIRouter(
    prefix="/api/v1/game",
    tags=["game-api"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
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
    server_group_id: Optional[int]
    group_key: Optional[str] = None
    commands: list
    allowed_action_ids: list
    reason: Optional[str]

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

    action = ServerActionQueue(
        action_type="trap",
        target_steamid=steamid,
        reason=body.reason,
        server_name=body.server_name or None,
        queued_by_admin_id=None,
        queued_at=_now_msk(),
        executed=0,
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
    action = ServerActionQueue(
        action_type="unban",
        target_steamid=steam2 or steamid,
        reason=body.reason or f"Unban by {body.issued_by_name}",
        server_name=body.server_name or None,
        queued_by_admin_id=None,
        queued_at=_now_msk(),
        executed=0,
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
def get_player_privileges(db: Session = Depends(get_db)):
    """
    Получить список всех игроков с серверными привилегиями.
    """
    records = db.query(ServerPlayerPrivilege).all()
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
            server_group_id=r.server_group_id,
            group_key=group_key or r.group_id,
            commands=r.commands or [],
            allowed_action_ids=r.allowed_action_ids or [],
            reason=r.reason,
        ))
    return result


@router.get("/privileges/player")
def get_player_privilege(steamid: str = Query(...), db: Session = Depends(get_db)):
    """
    Получить привилегии конкретного игрока (объединённые: группа + индивидуальные).
    """
    record = db.query(ServerPlayerPrivilege).filter(
        ServerPlayerPrivilege.steamid == steamid
    ).first()

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

    return {
        "has_privileges": True,
        "steamid": steamid,
        "player_id": record.player_id,
        "group_key": group_key,
        "commands": commands,
        "allowed_action_ids": action_ids,
        "reason": record.reason,
    }
