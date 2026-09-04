from sqlalchemy.orm import Session
from typing import Optional, List, Tuple, Dict
from fastapi import HTTPException
import logging
import os
import httpx
import threading
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))

def _now_msk() -> datetime:
    """Current time in Moscow timezone (naive datetime for display)."""
    return datetime.now(tz=MSK).replace(tzinfo=None)

from models import (
    PlayerData, PlayerStats, AdminUser, PlayerKill, PlayerRoleAction,
    PlayerIP, PlayerName, PlayerRoleStat, PlayerSession, ServerPlayerPrivilege,
    AnticheatEvent, ServerActionQueue,
    PlayerNote, LabelCategory, PlayerLabel, PlayerTrap
)

# ── In-memory online players store ───────────────────────────────────────────
# Flat per-player tracking with TTL. Each player entry has its own `last_seen`.
# A player is "online" if seen within _ONLINE_TTL_SECONDS.
# This design has NO bulk-clear operation, so one server's report can NEVER
# wipe another server's players (fixes the "online overwrite" bug when both
# servers sit behind the same proxy / don't send a distinct server_name).
_online_players: Dict[str, dict] = {}           # steamid -> info (includes last_seen, server_name)
_online_players_lock = threading.Lock()

# A player is considered offline if not refreshed within this many seconds.
# Should be ~2.5x the game server's report interval (reports every ~30s → 90s TTL).
_ONLINE_TTL_SECONDS = 90


def _prune_stale_online(now=None):
    """Remove players whose last_seen exceeded the TTL. MUST hold the lock."""
    if now is None:
        now = _now_msk()
    stale = []
    for sid, info in _online_players.items():
        last_seen = info.get("last_seen")
        if last_seen is None:
            stale.append(sid)
            continue
        if (now - last_seen).total_seconds() > _ONLINE_TTL_SECONDS:
            stale.append(sid)
    for sid in stale:
        _online_players.pop(sid, None)
from schemas import (
    PlayerDataCreate, PlayerStatsCreate, PaginationParams, PlayerDataOut, MessageEnvelope, MessageProcessResult,
    PlayerKillCreate, PlayerRoleActionCreate, PlayerIPCreate, PlayerNameCreate, PlayerRoleStatCreate, PlayerSessionCreate
)
from repositories import (
    KitRepository, PlayerRepository, PlayerStatsRepository, PlayerKillRepository,
    PlayerRoleActionRepository, PlayerIPRepository, PlayerNameRepository,
    PlayerRoleStatRepository, PlayerSessionRepository
)
from utils import parse_iso_datetime, safe_get_int, safe_get_list, validate_steamid, validate_player_name
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EventDispatcherService:
    """Universal message dispatcher for extensible external/game integrations."""

    def __init__(self, db: Session):
        self.db = db
        self.player_service = PlayerService(db)
        self.stats_service = PlayerStatsService(db)
        self.kill_service = PlayerKillService(db)
        self.role_action_service = PlayerRoleActionService(db)
        self.ip_service = PlayerIPService(db)
        self.name_service = PlayerNameService(db)
        self.role_stats_service = PlayerRoleStatService(db)
        self.session_service = PlayerSessionService(db)
        self._handlers = {
            # Core operations
            "player.upsert": self._handle_player_upsert,
            "stats.upsert": self._handle_stats_upsert,
            "player_and_stats.upsert": self._handle_player_and_stats_upsert,
            # Extended operations
            "player_kills.upsert": self._handle_player_kills_upsert,
            "player_role_actions.upsert": self._handle_player_role_actions_upsert,
            "player_ips.upsert": self._handle_player_ips_upsert,
            "player_names.upsert": self._handle_player_names_upsert,
            "player_role_stats.upsert": self._handle_player_role_stats_upsert,
            "player_sessions.login": self._handle_player_session_login,
            "player_sessions.logout": self._handle_player_session_logout,
        }

    def process(self, message: MessageEnvelope) -> MessageProcessResult:
        handler = self._handlers.get(message.type)
        if not handler:
            raise HTTPException(status_code=400, detail=f"Unsupported message type: {message.type}")

        data = handler(message.payload)
        return MessageProcessResult(
            message_type=message.type,
            trace_id=message.trace_id,
            data=data,
        )

    def _handle_player_upsert(self, payload: dict) -> dict:
        player_in = PlayerDataCreate(**payload)
        player = self.player_service.upsert_player(player_in)
        return {"player_id": player.player_id}

    def _handle_stats_upsert(self, payload: dict) -> dict:
        player_id = payload.get("player_id")
        if player_id is None:
            raise HTTPException(status_code=400, detail="payload.player_id is required")

        stats_payload = {k: v for k, v in payload.items() if k != "player_id"}
        stats_in = PlayerStatsCreate(**stats_payload)
        stats = self.stats_service.upsert_stats(int(player_id), stats_in)
        return {"player_id": stats.player_id}

    def _handle_player_and_stats_upsert(self, payload: dict) -> dict:
        player_payload = payload.get("player")
        stats_payload = payload.get("stats", {})
        if not isinstance(player_payload, dict):
            raise HTTPException(status_code=400, detail="payload.player object is required")

        player_in = PlayerDataCreate(**player_payload)
        player = self.player_service.upsert_player(player_in)

        if stats_payload:
            stats_in = PlayerStatsCreate(**stats_payload)
            self.stats_service.upsert_stats(player.player_id, stats_in)

        return {"player_id": player.player_id}
    
    def _handle_player_kills_upsert(self, payload: dict) -> dict:
        player_id = payload.get("player_id")
        kills_data = payload.get("kills", {})
        if not player_id:
            raise HTTPException(status_code=400, detail="payload.player_id is required")
        
        for monster_type, kills_count in kills_data.items():
            self.kill_service.upsert_kill(int(player_id), monster_type, int(kills_count))
        
        return {"player_id": player_id, "monsters_updated": len(kills_data)}
    
    def _handle_player_role_actions_upsert(self, payload: dict) -> dict:
        player_id = payload.get("player_id")
        role = payload.get("role")
        action_type = payload.get("action_type")
        value = payload.get("value", 1)
        
        if not all([player_id, role, action_type]):
            raise HTTPException(status_code=400, detail="player_id, role, action_type are required")
        
        action = self.role_action_service.create_action(
            int(player_id), role, action_type, float(value)
        )
        return {"id": action.id, "player_id": player_id}
    
    def _handle_player_ips_upsert(self, payload: dict) -> dict:
        player_id = payload.get("player_id")
        ip = payload.get("ip")
        subnet = payload.get("subnet")
        
        if not all([player_id, ip, subnet]):
            raise HTTPException(status_code=400, detail="player_id, ip, subnet are required")
        
        ip_record = self.ip_service.upsert_ip(int(player_id), ip, subnet)
        return {"id": ip_record.id, "player_id": player_id, "ip": ip}
    
    def _handle_player_names_upsert(self, payload: dict) -> dict:
        player_id = payload.get("player_id")
        nickname = payload.get("nickname")
        
        if not all([player_id, nickname]):
            raise HTTPException(status_code=400, detail="player_id, nickname are required")
        
        name_record = self.name_service.upsert_name(int(player_id), nickname)
        return {"id": name_record.id, "player_id": player_id, "nickname": nickname}
    
    def _handle_player_role_stats_upsert(self, payload: dict) -> dict:
        player_id = payload.get("player_id")
        role = payload.get("role")
        rounds_played = payload.get("rounds_played", 0)
        mvp_score = payload.get("mvp_score", 0)
        
        if not all([player_id, role]):
            raise HTTPException(status_code=400, detail="player_id, role are required")
        
        role_stat = self.role_stats_service.upsert_role_stat(
            int(player_id), role, int(rounds_played), float(mvp_score)
        )
        return {"id": role_stat.id, "player_id": player_id, "role": role}
    
    def _handle_player_session_login(self, payload: dict) -> dict:
        player_steamid = payload.get("player_steamid")
        player_name = payload.get("player_last_time_name")
        ip = payload.get("ip")
        subnet = payload.get("subnet")
        login_time_str = payload.get("login_time")
        
        logger.debug(f"Login payload: steamid={player_steamid}, ip={ip}, subnet={subnet}, name={player_name}")
        
        if not all([player_steamid, login_time_str]):
            raise HTTPException(status_code=400, detail="player_steamid, login_time are required")
        
        try:
            login_time = parse_iso_datetime(login_time_str)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        # Найти или создать игрока
        player = self.player_service.get_or_create_player_by_steamid(player_steamid, player_name)
        
        # Закрыть старую открытую сессию если есть
        from models import PlayerSession
        old_session = self.db.query(PlayerSession).filter(
            PlayerSession.player_id == player.player_id,
            PlayerSession.logout_time == None
        ).order_by(PlayerSession.id.desc()).first()
        
        if old_session:
            old_session.logout_time = login_time
            old_session.session_duration = max(0, int((login_time - old_session.login_time).total_seconds()))
            self.db.flush()  # Flush before commit to avoid session conflicts
            self.db.commit()
            logger.warning(f"Closed stale session {old_session.id} for player {player.player_id}")
        
        # Обновить IP и имя если предоставлены
        if ip or subnet or player_name:
            update_data = {}
            if player_name:
                update_data["player_last_time_name"] = player_name
            if ip:
                update_data["last_ip"] = ip
            if subnet:
                update_data["last_subnet"] = subnet
            
            logger.debug(f"Updating player {player.player_id} with: {update_data}")
            player = self.player_service.update_player(player.player_id, update_data)
            logger.debug(f"After update - IP: {player.last_ip}, Subnet: {player.last_subnet}")
        
        # Создать сессию
        session = self.session_service.login(player.player_id, login_time)
        return {"session_id": session.id, "player_id": player.player_id, "session_number": session.session_number}
    
    def _handle_player_session_logout(self, payload: dict) -> dict:
        player_steamid = payload.get("player_steamid")
        logout_time_str = payload.get("logout_time")
        play_time_this_session = payload.get("play_time_this_session")
        stats = payload.get("stats", {})
        role_actions = payload.get("role_actions", [])
        role_stats = payload.get("role_stats", [])
        kills = payload.get("kills", {})
        
        if not all([player_steamid, logout_time_str]):
            raise HTTPException(status_code=400, detail="player_steamid, logout_time are required")
        
        try:
            logout_time = parse_iso_datetime(logout_time_str)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        # Найти открытую сессию по steamid
        player = self.player_service.player_repo.get_by_steamid(player_steamid)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        # Найти последнюю открытую сессию
        from models import PlayerSession
        session = self.db.query(PlayerSession).filter(
            PlayerSession.player_id == player.player_id,
            PlayerSession.logout_time == None
        ).order_by(PlayerSession.id.desc()).first()
        
        if not session:
            raise HTTPException(status_code=404, detail="No open session found")
        
        # Закрыть сессию
        session = self.session_service.logout(session.id, logout_time)
        
        # Обновить основную статистику если передана
        if stats:
            stats_create = PlayerStatsCreate(
                killed_monsters=stats.get("killed_monsters", 0),
                killed_players=stats.get("killed_players", 0),
                saved_players=stats.get("saved_players", 0),
                total_missions_captain=stats.get("total_missions_captain", 0),
                successful_missions_captain=stats.get("successful_missions_captain", 0),
                killed_npc=stats.get("killed_npc", 0)
            )
            self.stats_service.upsert_stats(player.player_id, stats_create)
        
        # Обновить убийства по типам монстров
        if kills:
            for monster_type, count in kills.items():
                self.kill_service.upsert_kill(player.player_id, monster_type, int(count))
        
        # Обновить действия по ролям
        # role_actions: [{"role": "captain", "action_type": "repair_hull", "value": 3.5}, ...]
        if role_actions:
            for ra in role_actions:
                role = ra.get("role", "")
                action_type = ra.get("action_type", "")
                value = float(ra.get("value", 1))
                if role and action_type:
                    self.role_action_service.create_action(player.player_id, role, action_type, value)
        
        # Обновить статистику по ролям
        # role_stats: [{"role": "captain", "rounds_played": 1, "mvp_score": 2.5}, ...]
        if role_stats:
            for rs in role_stats:
                role = rs.get("role", "")
                rounds_played = int(rs.get("rounds_played", 0))
                mvp_score = float(rs.get("mvp_score", 0))
                if role:
                    self.role_stats_service.accumulate_role_stat(
                        player.player_id, role, rounds_played, mvp_score
                    )
        
        # Обновить общее время игры
        current_play_time = player.play_time or 0
        duration_seconds = session.session_duration or 0
        
        if play_time_this_session:
            added_time = int(play_time_this_session)
            new_play_time = current_play_time + added_time
        else:
            added_time = duration_seconds
            new_play_time = current_play_time + added_time

        if added_time > 0:
            self.player_service.player_repo.update(player.player_id, {"play_time": new_play_time})
            logger.info(f"Updated player {player.player_id} play_time: {current_play_time}s -> {new_play_time}s (+{added_time}s)")
        
        return {"session_id": session.id, "player_id": player.player_id, "duration_seconds": session.session_duration or 0}


class PlayerService:
    """Business logic for player operations"""
    
    def __init__(self, db: Session):
        self.player_repo = PlayerRepository(db)
        self.stats_repo = PlayerStatsRepository(db)
    
    def get_all_players(
        self, page: int = 1, page_size: int = 50,
        rank_id: Optional[int] = None, sort: Optional[str] = None,
        discord_link: Optional[str] = None,
        label_mode: Optional[str] = None, label_ids: Optional[List[int]] = None,
        notes: Optional[str] = None, sanctions: Optional[str] = None,
        anomalies: Optional[str] = None, name_history: Optional[str] = None,
        activity: Optional[str] = None,
    ) -> Tuple[List[PlayerDataOut], int]:
        """Get all players with pagination, optional rank filter and sort order."""
        if page < 1:
            raise HTTPException(status_code=400, detail="Page must be >= 1")

        players, total = self.player_repo.get_all(
            page, page_size, rank_id=rank_id, sort=sort, discord_link=discord_link,
            label_mode=label_mode, label_ids=label_ids, notes=notes, sanctions=sanctions,
            anomalies=anomalies, name_history=name_history, activity=activity,
        )
        player_outs = [PlayerDataOut.from_orm_with_mappings(p, settings.ranks_mapping, settings.kits_mapping) for p in players]
        flags = self.player_repo.get_player_flags([player.player_id for player in players])
        for player_out in player_outs:
            for key, value in flags.get(player_out.player_id, {}).items():
                if key != "label_ids":
                    setattr(player_out, key, value)
            player_out.label_ids = flags.get(player_out.player_id, {}).get("label_ids", player_out.label_ids)
        return player_outs, total
    
    def get_player_by_id(self, player_id: int) -> Optional[PlayerDataOut]:
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        return PlayerDataOut.from_orm_with_mappings(player, settings.ranks_mapping, settings.kits_mapping)
    
    def get_player_by_identifier(
        self, 
        type: str, 
        identifier: str
    ) -> Optional[PlayerDataOut]:
        """
        Get player by steamid or name
        
        Args:
            type: 'steamid' or 'name'
            identifier: The value to search for
        """
        if type == "steamid":
            if not validate_steamid(identifier):
                raise HTTPException(status_code=400, detail="Invalid SteamID format")
            player = self.player_repo.get_by_steamid(identifier)
        elif type == "name":
            if not validate_player_name(identifier):
                raise HTTPException(status_code=400, detail="Invalid player name")
            player = self.player_repo.get_by_name(identifier)
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid type: use 'steamid' or 'name'"
            )
        
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return PlayerDataOut.from_orm_with_mappings(player, settings.ranks_mapping, settings.kits_mapping)
    
    def upsert_player(self, player_data: PlayerDataCreate) -> PlayerDataOut:
        """
        Create or update player by steamid
        
        Args:
            player_data: Player data from request
            
        Returns:
            Created or updated PlayerData
        """
        # Validate input
        if not validate_steamid(player_data.player_steamid):
            raise HTTPException(status_code=400, detail="Invalid SteamID format")
        
        if not validate_player_name(player_data.player_last_time_name):
            raise HTTPException(status_code=400, detail="Invalid player name")
        
        # Try to find existing
        existing = self.player_repo.get_by_steamid(player_data.player_steamid)
        
        if existing:
            # Update existing player
            update_data = {
                "player_last_time_name": player_data.player_last_time_name,
            }

            # NEVER overwrite achievements from game server API.
            # Achievements (kval_kits, items, stats, talents) are managed
            # exclusively through the admin panel. Game server should not
            # send achievements data — it will be ignored.
            
            # Only update play_time if explicitly provided (not None)
            if player_data.play_time is not None:
                update_data["play_time"] = player_data.play_time
            
            # Only update rank_id and kit_level if explicitly provided (not None)
            if player_data.rank_id is not None:
                update_data["rank_id"] = player_data.rank_id
            if player_data.kit_level is not None:
                update_data["kit_level"] = player_data.kit_level
            
            if player_data.last_login_time:
                try:
                    update_data["last_login_time"] = parse_iso_datetime(
                        player_data.last_login_time
                    )
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))
            
            # Add new optional fields
            if player_data.last_ip:
                update_data["last_ip"] = player_data.last_ip
            if player_data.last_subnet:
                update_data["last_subnet"] = player_data.last_subnet
            if player_data.first_login:
                try:
                    update_data["first_login"] = parse_iso_datetime(player_data.first_login)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))
            if player_data.avg_ping is not None:
                update_data["avg_ping"] = player_data.avg_ping
            
            logger.info(f"Updating player: {existing.player_id}")
            updated_player = self.player_repo.update(existing.player_id, update_data)
            return PlayerDataOut.from_orm_with_mappings(updated_player, settings.ranks_mapping, settings.kits_mapping)
        
        # Create new player (handle race condition with duplicate key)
        from sqlalchemy.exc import IntegrityError as SAIntegrityError
        new_player = PlayerData(
            player_last_time_name=player_data.player_last_time_name,
            player_steamid=player_data.player_steamid,
            play_time=player_data.play_time if player_data.play_time is not None else 0,
            rank_id=player_data.rank_id if player_data.rank_id is not None else 0,
            kit_level=player_data.kit_level if player_data.kit_level is not None else 0,
            achievements=safe_get_list(player_data.achievements),
            last_ip=player_data.last_ip,
            last_subnet=player_data.last_subnet,
            avg_ping=player_data.avg_ping,
        )
        
        if player_data.last_login_time:
            try:
                new_player.last_login_time = parse_iso_datetime(
                    player_data.last_login_time
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        
        if player_data.first_login:
            try:
                new_player.first_login = parse_iso_datetime(player_data.first_login)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        
        try:
            created_player = self.player_repo.create(new_player)
            
            # Create stats for new player
            new_stats = PlayerStats(player_id=created_player.player_id)
            self.stats_repo.create(created_player.player_id, new_stats)
            
            return PlayerDataOut.from_orm_with_mappings(created_player, settings.ranks_mapping, settings.kits_mapping)
        except SAIntegrityError:
            self.db.rollback()
            # Another request created this player concurrently — fetch and update
            existing = self.player_repo.get_by_steamid(player_data.player_steamid)
            if existing:
                return PlayerDataOut.from_orm_with_mappings(existing, settings.ranks_mapping, settings.kits_mapping)
            raise
    
    def search_players(
        self, query: str, limit: int = 10, discord_link: Optional[str] = None,
        label_mode: Optional[str] = None, label_ids: Optional[List[int]] = None,
        notes: Optional[str] = None, sanctions: Optional[str] = None,
        anomalies: Optional[str] = None, name_history: Optional[str] = None,
        activity: Optional[str] = None,
    ) -> List[PlayerData]:
        """Search players by name or SteamID"""
        return self.player_repo.search(
            query, limit, discord_link=discord_link, label_mode=label_mode, label_ids=label_ids,
            notes=notes, sanctions=sanctions, anomalies=anomalies,
            name_history=name_history, activity=activity,
        )
    
    def get_or_create_player_by_steamid(self, steamid: str, player_name: str = None) -> PlayerData:
        """Get existing player or create new one by steamid"""
        if not validate_steamid(steamid):
            raise HTTPException(status_code=400, detail="Invalid SteamID format")
        
        # Try to find existing
        existing = self.player_repo.get_by_steamid(steamid)
        if existing:
            return existing
        
        # Create new player (handle race condition with duplicate key)
        from sqlalchemy.exc import IntegrityError as SAIntegrityError
        try:
            new_player = PlayerData(
                player_steamid=steamid,
                player_last_time_name=player_name or f"Player_{steamid[-8:]}",
            )
            created_player = self.player_repo.create(new_player)
            
            # Create stats for new player
            new_stats = PlayerStats(player_id=created_player.player_id)
            self.stats_repo.create(created_player.player_id, new_stats)
            
            logger.info(f"Created new player: {created_player.player_id} ({steamid})")
            return created_player
        except SAIntegrityError:
            self.db.rollback()
            existing = self.player_repo.get_by_steamid(steamid)
            if existing:
                return existing
            raise
    
    def update_player(self, player_id: int, update_data: dict) -> PlayerData:
        """Update player data"""
        updated = self.player_repo.update(player_id, update_data)
        if not updated:
            raise HTTPException(status_code=404, detail="Player not found")
        logger.info(f"Updated player: {player_id}")
        return updated


class PlayerStatsService:
    """Business logic for player stats operations"""
    
    def __init__(self, db: Session):
        self.player_repo = PlayerRepository(db)
        self.stats_repo = PlayerStatsRepository(db)
    
    def get_stats(self, player_id: int) -> PlayerStats:
        """Get stats for player, raise 404 if not found"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        stats = self.stats_repo.get_by_player_id(player_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Stats not found")
        
        return stats
    
    def upsert_stats(
        self,
        player_id: int,
        stats_data: PlayerStatsCreate
    ) -> PlayerStats:
        """
        Create or accumulate stats for player
        
        Args:
            player_id: Player ID
            stats_data: Stats delta/values from Lua
            
        Returns:
            Updated or created PlayerStats
        """
        # Verify player exists
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        # Check if stats exist
        existing_stats = self.stats_repo.get_by_player_id(player_id)
        
        if existing_stats:
            # Accumulate delta values
            delta = {
                "successful_missions_captain": safe_get_int(
                    stats_data.successful_missions_captain
                ),
                "total_missions_captain": safe_get_int(
                    stats_data.total_missions_captain
                ),
                "saved_players": safe_get_int(stats_data.saved_players),
                "killed_monsters": safe_get_int(stats_data.killed_monsters),
                "killed_players": safe_get_int(stats_data.killed_players),
                "killed_npc": safe_get_int(stats_data.killed_npc),
            }
            logger.info(f"Accumulating stats for player: {player_id}")
            return self.stats_repo.accumulate(player_id, delta)
        
        # Create new stats
        new_stats = PlayerStats(
            player_id=player_id,
            successful_missions_captain=safe_get_int(
                stats_data.successful_missions_captain
            ),
            total_missions_captain=safe_get_int(
                stats_data.total_missions_captain
            ),
            saved_players=safe_get_int(stats_data.saved_players),
            killed_monsters=safe_get_int(stats_data.killed_monsters),
            killed_players=safe_get_int(stats_data.killed_players),
            killed_npc=safe_get_int(stats_data.killed_npc),
        )
        
        logger.info(f"Creating new stats for player: {player_id}")
        return self.stats_repo.create(player_id, new_stats)


class KitService:
    """Business logic for kit operations"""
    
    def __init__(self, db: Session):
        self.kit_repo = KitRepository(db)
    
    def get_all_kits(self, page: int = 1, page_size: int = 50) -> Tuple[List, int]:
        """Get all kits with pagination"""
        if page < 1:
            raise HTTPException(status_code=400, detail="Page must be >= 1")
        
        kits, total = self.kit_repo.get_all(page, page_size)
        return kits, total
    
    def get_kit(self, kit_id: int):
        kit = self.kit_repo.get_by_id(kit_id)
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        return kit


class PlayerKillService:
    """Business logic for player kills"""
    
    def __init__(self, db: Session):
        self.kill_repo = PlayerKillRepository(db)
        self.player_repo = PlayerRepository(db)
    
    def upsert_kill(self, player_id: int, monster_type: str, kills_count: int) -> PlayerKill:
        """Create or update kill count for monster"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.kill_repo.create_or_update(player_id, monster_type, kills_count)
    
    def accumulate_kill(self, player_id: int, monster_type: str, delta: int) -> PlayerKill:
        """Add delta to existing kill count"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.kill_repo.accumulate(player_id, monster_type, delta)
    
    def get_player_kills(self, player_id: int) -> List[PlayerKill]:
        """Get all kills for a player"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.kill_repo.get_all_by_player(player_id)


class PlayerRoleActionService:
    """Business logic for player role actions"""
    
    def __init__(self, db: Session):
        self.action_repo = PlayerRoleActionRepository(db)
        self.player_repo = PlayerRepository(db)
    
    def create_action(self, player_id: int, role: str, action_type: str, value: float) -> PlayerRoleAction:
        """Record a role action"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.action_repo.create(player_id, role, action_type, value)
    
    def get_player_actions(self, player_id: int) -> List[PlayerRoleAction]:
        """Get all actions for a player"""
        return self.action_repo.get_all_by_player(player_id)


class PlayerIPService:
    """Business logic for player IP tracking"""
    
    def __init__(self, db: Session):
        self.ip_repo = PlayerIPRepository(db)
        self.player_repo = PlayerRepository(db)
    
    def upsert_ip(self, player_id: int, ip: str, subnet: str) -> PlayerIP:
        """Create or update IP record"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.ip_repo.create_or_update(player_id, ip, subnet)
    
    def get_player_ips(self, player_id: int) -> List[PlayerIP]:
        """Get all IPs for a player"""
        return self.ip_repo.get_all_by_player(player_id)


class PlayerNameService:
    """Business logic for player name history"""
    
    def __init__(self, db: Session):
        self.name_repo = PlayerNameRepository(db)
        self.player_repo = PlayerRepository(db)
    
    def upsert_name(self, player_id: int, nickname: str) -> PlayerName:
        """Create or update name record"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.name_repo.create_or_update(player_id, nickname)
    
    def get_player_names(self, player_id: int) -> List[PlayerName]:
        """Get all nicknames/names for a player"""
        return self.name_repo.get_all_by_player(player_id)


class PlayerRoleStatService:
    """Business logic for player role statistics"""
    
    def __init__(self, db: Session):
        self.role_stat_repo = PlayerRoleStatRepository(db)
        self.player_repo = PlayerRepository(db)
    
    def upsert_role_stat(self, player_id: int, role: str, rounds_played: int, mvp_score: float) -> PlayerRoleStat:
        """Create or update role statistics"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.role_stat_repo.create_or_update(player_id, role, rounds_played, mvp_score)
    
    def accumulate_role_stat(self, player_id: int, role: str, rounds_delta: int, mvp_delta: float) -> PlayerRoleStat:
        """Accumulate role statistics"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.role_stat_repo.accumulate(player_id, role, rounds_delta, mvp_delta)
    
    def get_player_role_stats(self, player_id: int) -> List[PlayerRoleStat]:
        """Get all role statistics for a player"""
        return self.role_stat_repo.get_all_by_player(player_id)


class PlayerSessionService:
    """Business logic for player sessions"""
    
    def __init__(self, db: Session):
        self.session_repo = PlayerSessionRepository(db)
        self.player_repo = PlayerRepository(db)
    
    def login(self, player_id: int, login_time) -> PlayerSession:
        """Create a new login session"""
        player = self.player_repo.get_by_id(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        return self.session_repo.create(player_id, login_time)
    
    def logout(self, session_id: int, logout_time) -> PlayerSession:
        """Logout player session"""
        session = self.session_repo.logout(session_id, logout_time)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return session
    
    def get_player_sessions(self, player_id: int) -> List[PlayerSession]:
        """Get all sessions for a player"""
        return self.session_repo.get_all_by_player(player_id)
    
    def update_ping(self, session_id: int, avg_ping: int) -> PlayerSession:
        """Update average ping for session"""
        session = self.session_repo.update_ping(session_id, avg_ping)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return session


# Discord auth service
class DiscordAuthService:
    """Service for Discord OAuth authentication — always talks to Discord's
    API directly. Used to optionally route through a local proxy
    (discord_proxy.py) when the API server's host had discord.com blocked at
    the network level; that block has since been lifted/the server moved, so
    the proxy path was removed."""

    DISCORD_OAUTH_URL = "https://discord.com/api/v10/oauth2/authorize"

    def __init__(self):
        self.client_id = settings.discord_client_id
        self.client_secret = settings.discord_client_secret
        self.redirect_uri = settings.discord_redirect_uri

    def get_oauth_url(self, state: str = None, redirect_uri: str = None) -> str:
        """Generate Discord OAuth URL (always direct to Discord).

        `redirect_uri` overrides the configured default — used to support
        multiple entry-point domains (see routes/admin.py's
        _resolve_oauth_redirect_uri): Discord validates redirect_uri
        byte-for-byte against what's registered on its side, so whichever
        value is used here MUST be passed again, identically, to
        exchange_code below for the SAME login round-trip."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "response_type": "code",
            "scope": "identify",
        }
        if state:
            params["state"] = state

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.DISCORD_OAUTH_URL}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str = None) -> dict:
        """Exchange authorization code for access token.

        `redirect_uri` must match exactly what was passed to get_oauth_url
        for this same login attempt — Discord rejects the exchange
        otherwise, even though the code itself is valid."""
        effective_redirect_uri = redirect_uri or self.redirect_uri
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    "https://discord.com/api/v10/oauth2/token",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": effective_redirect_uri,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                # Discord's error body (error/error_description) says exactly
                # why — invalid_client (bad client_id/secret), invalid_grant
                # (code already used/expired, OR redirect_uri doesn't match
                # what was used for the /authorize step), etc. Logging only
                # the status code (the old behavior) made every 401 look
                # identical and impossible to diagnose without guessing.
                logger.error(
                    f"Failed to exchange code: {e.response.status_code} {e.response.text} "
                    f"(redirect_uri sent: {effective_redirect_uri})"
                )
                raise HTTPException(status_code=500, detail="Authentication failed")
            except Exception as e:
                logger.error(f"Failed to exchange code: {e} (redirect_uri sent: {effective_redirect_uri})")
                raise HTTPException(status_code=500, detail="Authentication failed")

    async def get_user_info(self, access_token: str) -> dict:
        """Get user info from Discord API"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                response.raise_for_status()
                return response.json()

            except Exception as e:
                logger.error(f"Failed to get user info: {e}")
                raise HTTPException(status_code=500, detail="Failed to get user info")


class AdminService:
    """Business logic for admin operations"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_all_admins(self) -> List[AdminUser]:
        """Get all admin users (excluding soft-deleted)"""
        return self.db.query(AdminUser).filter(
            (AdminUser.is_deleted == 0) | (AdminUser.is_deleted.is_(None))
        ).all()
    
    def get_admin_by_discord_id(self, discord_id: str) -> Optional[AdminUser]:
        """Get admin by Discord ID"""
        return self.db.query(AdminUser).filter(AdminUser.discord_id == discord_id).first()
    
    def create_admin(self, discord_user: dict, role: str = "admin") -> AdminUser:
        """Create new admin user from Discord data"""
        permissions = settings.roles_mapping.get(role, ["read"])
        admin = AdminUser(
            discord_id=discord_user["id"],
            discord_username=discord_user["username"],
            discord_avatar=discord_user.get("avatar"),
            role=role,
            permissions=permissions
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        return admin
    
    def update_admin_role(self, admin_id: int, role: str, permissions: List[str] = None) -> AdminUser:
        """Update admin role and permissions"""
        admin = self.db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        admin.role = role
        if permissions is None:
            permissions = settings.roles_mapping.get(role, ["read"])
        admin.permissions = permissions
        
        self.db.commit()
        self.db.refresh(admin)
        return admin
    
    def delete_admin(self, admin_id: int):
        """Soft-delete admin user: mark as deleted, revoke sessions, remove privilege group.
        
        The record stays in the DB so FK references remain intact,
        but the admin loses all access (is_deleted=1 blocks authentication).
        """
        admin = self.db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        # Mark as deleted
        admin.is_deleted = 1
        admin.privilege_group_id = None
        admin.is_superuser = False
        admin.individual_permissions = None
        
        # Revoke all active sessions
        from models import AdminSession
        self.db.query(AdminSession).filter(
            AdminSession.admin_id == admin_id,
            AdminSession.revoked == 0,
        ).update({"revoked": 1})
        
        self.db.commit()
    
    def update_last_login(self, discord_id: str):
        """Update last login timestamp"""
        admin = self.get_admin_by_discord_id(discord_id)
        if admin:
            from datetime import datetime
            admin.last_login = datetime.utcnow()
            self.db.commit()
    
    def update_server_permissions(self, admin_id: int, server_permissions: List[str]) -> AdminUser:
        """Update server-side privileges/permissions"""
        admin = self.db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        # Validate permissions exist in config
        valid_permissions = list(settings.server_permissions_mapping.keys())
        for perm in server_permissions:
            if perm not in valid_permissions:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid permission: {perm}. Valid permissions: {valid_permissions}"
                )
        
        admin.server_permissions = server_permissions
        self.db.commit()
        self.db.refresh(admin)
        return admin
    
    def get_admin_server_permissions(self, admin_id: int) -> dict:
        """Get server permissions for admin"""
        admin = self.db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        # Get details for each permission
        perms = []
        for perm in admin.server_permissions:
            if perm in settings.server_permissions_mapping:
                perms.append({
                    "id": perm,
                    **settings.server_permissions_mapping[perm]
                })
        
        return {"admin_id": admin_id, "permissions": perms}
    
    def has_server_permission(self, admin_id: int, permission: str) -> bool:
        """Check if admin has specific server permission"""
        admin = self.db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not admin:
            return False
        
        return permission in admin.server_permissions


class ServerPlayerPrivilegeService:
    """Business logic for managing server-side privileges for players"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def grant_privilege(self, steamid: str, privileges: List[str], 
                       reason: str = None, granted_by_admin_id: int = None,
                       expires_at = None) -> ServerPlayerPrivilege:
        """Grant server privileges to a player by SteamID"""
        # Validate privileges
        valid_privileges = list(settings.server_permissions_mapping.keys())
        for priv in privileges:
            if priv not in valid_privileges:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid privilege: {priv}. Valid privileges: {valid_privileges}"
                )
        
        # Check if player privilege record already exists
        privilege_record = self.db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.steamid == steamid
        ).first()
        
        if privilege_record:
            privilege_record.privileges = privileges
            privilege_record.reason = reason
            privilege_record.expires_at = expires_at
        else:
            # Try to find player_id by steamid
            player = self.db.query(PlayerData).filter(
                PlayerData.player_steamid == steamid
            ).first()
            
            privilege_record = ServerPlayerPrivilege(
                steamid=steamid,
                player_id=player.player_id if player else None,
                privileges=privileges,
                reason=reason,
                granted_by_admin_id=granted_by_admin_id,
                expires_at=expires_at
            )
            self.db.add(privilege_record)
        
        self.db.commit()
        self.db.refresh(privilege_record)
        return privilege_record
    
    def revoke_privilege(self, steamid: str):
        """Revoke all privileges for a player"""
        privilege_record = self.db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.steamid == steamid
        ).first()
        
        if privilege_record:
            self.db.delete(privilege_record)
            self.db.commit()
            return True
        return False
    
    def get_player_privileges(self, steamid: str) -> Optional[ServerPlayerPrivilege]:
        """Get privileges for a player by SteamID"""
        return self.db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.steamid == steamid
        ).first()
    
    def get_all_privileges(self, limit: int = 100, offset: int = 0) -> Tuple[List[ServerPlayerPrivilege], int]:
        """Get all player privileges with pagination"""
        query = self.db.query(ServerPlayerPrivilege)
        total = query.count()
        records = query.offset(offset).limit(limit).all()
        return records, total
    
    def search_privileges(self, query: str, limit: int = 50) -> List[ServerPlayerPrivilege]:
        """Search for player privileges by steamid or player name"""
        # Search by steamid
        results = self.db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.steamid.like(f"%{query}%")
        ).limit(limit).all()
        
        if len(results) < limit:
            # Also search by player name if available
            remaining = limit - len(results)
            player_results = self.db.query(ServerPlayerPrivilege).join(
                PlayerData, ServerPlayerPrivilege.player_id == PlayerData.player_id
            ).filter(
                PlayerData.player_last_time_name.like(f"%{query}%")
            ).limit(remaining).all()
            results.extend(player_results)
        
        return results
    
    def has_privilege(self, steamid: str, privilege: str) -> bool:
        """Check if player has specific privilege"""
        privilege_record = self.get_player_privileges(steamid)
        if not privilege_record:
            return False
        
        return privilege in privilege_record.privileges
    
    def update_privileges(self, steamid: str, privileges: List[str],
                         reason: str = None) -> ServerPlayerPrivilege:
        """Update privileges for a player"""
        return self.grant_privilege(steamid, privileges, reason=reason)


# ════════════════════════════════════════════════════════════════════════════
class ServerModerationService:
    """Business logic for game-server moderation: online players, anticheat, action queue."""

    def __init__(self, db: Session):
        self.db = db

    # ── Online players snapshot (in-memory) ────────────────────────────────────────────────

    def replace_online_players(self, players, server_name: Optional[str] = None) -> int:
        """Merge an online-players report into the in-memory store.

        Uses per-player TTL tracking instead of bulk snapshot replacement.
        Each reported player is refreshed with a new `last_seen`. Players are
        pruned individually once they exceed the TTL. This makes it impossible
        for one server's report to wipe another server's online players —
        the core fix for the "online overwrite" bug.

        Returns the count of players reported in THIS request.
        """
        now = _now_msk()

        if server_name:
            server_name = server_name.strip()

        # Collect valid (steamid, payload) tuples
        player_list = []
        for p in players:
            steamid = p.steamid if hasattr(p, "steamid") else p.get("steamid", "")
            if not steamid:
                continue
            player_list.append((steamid, p))

        # Batch fetch PlayerData for reported steamids
        steamids = [sid for sid, _ in player_list]
        pd_rows = {}
        if steamids:
            pd_rows = {
                r.player_steamid: r
                for r in self.db.query(PlayerData).filter(PlayerData.player_steamid.in_(steamids)).all()
            }

        newly_joined = set()

        with _online_players_lock:
            # Track which steamids THIS server currently reports
            reported_now = set()

            for steamid, p in player_list:
                reported_now.add(steamid)
                player_name = p.player_name if hasattr(p, "player_name") else p.get("player_name")
                job_id = p.job_id if hasattr(p, "job_id") else p.get("job_id")
                ping = p.ping if hasattr(p, "ping") else p.get("ping")

                pd = pd_rows.get(steamid)
                play_time = pd.play_time if pd else 0
                player_id = pd.player_id if pd else None
                db_name = pd.player_last_time_name if pd else None
                character_name = db_name if db_name and db_name != player_name else None

                existing = _online_players.get(steamid)
                # Detect newly joined (not previously online OR was on a different server)
                if existing is None:
                    newly_joined.add(steamid)
                    join_time = now
                else:
                    join_time = existing.get("join_time", now)

                _online_players[steamid] = {
                    "player_steamid": steamid,
                    "player_name": player_name or db_name or steamid,
                    "character_name": character_name,
                    "forced_display_name": pd.forced_display_name if pd else None,
                    "job_id": job_id,
                    "ping": ping,
                    "player_id": player_id,
                    "rank_id": pd.rank_id if pd else None,
                    "has_discord_link": bool(pd and pd.discord_id),
                    "label_ids": [int(label_id) for label_id in (pd.labels or []) if str(label_id).isdigit()] if pd else [],
                    "last_seen": now,
                    "join_time": join_time,
                    "total_play_time": play_time,
                    "is_new_player": play_time < 7200,
                    "server_name": server_name,
                }

            # Remove players that THIS server previously reported but no longer does.
            # Only touch players whose server_name matches this server — never
            # remove players belonging to a different server.
            if server_name:
                to_remove = [
                    sid for sid, info in _online_players.items()
                    if info.get("server_name") == server_name and sid not in reported_now
                ]
                for sid in to_remove:
                    _online_players.pop(sid, None)

            # Prune globally stale players (covers servers that stopped reporting,
            # or players reported without a server_name).
            _prune_stale_online(now)

            logger.debug(
                f"Online merge: server={server_name}, reported={len(reported_now)}, "
                f"total_online={len(_online_players)}"
            )

        # ── Trap check for newly joined players ────────────────────────────────
        if newly_joined:
            try:
                active_traps = (
                    self.db.query(PlayerTrap)
                    .filter(PlayerTrap.active == 1, PlayerTrap.target_steamid.in_(list(newly_joined)))
                    .all()
                )
                if active_traps:
                    from discord_bot import bot as discord_bot
                    for trap in active_traps:
                        with _online_players_lock:
                            pinfo = _online_players.get(trap.target_steamid, {})
                        pname = pinfo.get("player_name", trap.target_steamid)
                        try:
                            discord_bot.schedule(discord_bot.execute_bot_action("trap_triggered", {
                                "player_name": pname,
                                "steamid": trap.target_steamid,
                                "player_id": trap.player_id,
                                "reason": trap.reason,
                            }))
                        except Exception as e:
                            # Bot not running yet — still deactivate the trap
                            # (it fired, that's not in question) even if the
                            # Discord notification itself couldn't be sent.
                            logging.warning(f"Trap notification dispatch failed: {e}")
                        trap.active = 0
                        trap.triggered_at = _now_msk()
                    self.db.commit()
                    logging.info(f"[TRAP] {len(active_traps)} trap(s) triggered, bot notified")
            except Exception as e:
                logging.warning(f"Trap check error: {e}")

        # ── Record online snapshot for analytics ──────────────────────────────
        try:
            from models import OnlineSnapshot
            snapshot = OnlineSnapshot(
                server_name=server_name or "UNKNOWN",
                player_count=len(player_list),
                player_ids=steamids[:200] if steamids else [],  # cap at 200 to avoid huge JSON
                recorded_at=_now_msk(),
            )
            self.db.add(snapshot)
            self.db.commit()
        except Exception as e:
            logging.warning(f"Snapshot recording error: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        return len(player_list)

    def get_online_players(self, server_name: Optional[str] = None) -> List[dict]:
        with _online_players_lock:
            # Prune stale players first so counts are always accurate
            _prune_stale_online()
            if server_name:
                sn_lower = server_name.strip().lower()
                source = {
                    sid: info for sid, info in _online_players.items()
                    if (info.get("server_name") or "").lower() == sn_lower
                }
            else:
                source = dict(_online_players)
            result = sorted(source.values(), key=lambda x: (x.get("player_name") or ""))
        return result

    def get_online_servers(self) -> List[str]:
        """Return list of server names that currently have online players."""
        with _online_players_lock:
            _prune_stale_online()
            servers = set()
            for info in _online_players.values():
                sn = info.get("server_name")
                if sn:
                    servers.add(sn)
            return sorted(servers)

    def get_recent_offline_players(self, limit: int = 50) -> List[PlayerData]:
        with _online_players_lock:
            _prune_stale_online()
            online_steamids = set(_online_players.keys())
        q = self.db.query(PlayerData)
        if online_steamids:
            q = q.filter(~PlayerData.player_steamid.in_(online_steamids))
        return q.order_by(PlayerData.last_login_time.desc()).limit(limit).all()

    # ── Anticheat events ────────────────────────────────────────────────────────────

    def add_anticheat_event(self, event) -> AnticheatEvent:
        player_steamid = event.player_steamid if hasattr(event, "player_steamid") else event.get("player_steamid", "")
        pd = self.db.query(PlayerData).filter(PlayerData.player_steamid == player_steamid).first()
        occurred_at = event.occurred_at if hasattr(event, "occurred_at") else None
        row = AnticheatEvent(
            player_steamid=player_steamid,
            player_name=event.player_name if hasattr(event, "player_name") else event.get("player_name"),
            player_id=pd.player_id if pd else None,
            event_type=event.event_type if hasattr(event, "event_type") else event.get("event_type", ""),
            details=event.details if hasattr(event, "details") else event.get("details"),
            occurred_at=occurred_at or datetime.utcnow(),
            reviewed=0,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_anticheat_events(self, limit: int = 200, reviewed: Optional[bool] = None) -> List[AnticheatEvent]:
        q = self.db.query(AnticheatEvent)
        if reviewed is not None:
            q = q.filter(AnticheatEvent.reviewed == (1 if reviewed else 0))
        return q.order_by(AnticheatEvent.occurred_at.desc()).limit(limit).all()

    def mark_reviewed(self, event_id: int) -> bool:
        ev = self.db.query(AnticheatEvent).filter(AnticheatEvent.id == event_id).first()
        if not ev:
            return False
        ev.reviewed = 1
        self.db.commit()
        return True

    # ── Action queue ───────────────────────────────────────────────────────────────

    def enqueue_action(self, action_in, admin_id: Optional[int] = None, server_name: Optional[str] = None, payload: Optional[dict] = None) -> "ServerAction":
        """Create a new server action (v2 table)."""
        from datetime import datetime
        from models import ServerAction

        effective_server = server_name or (action_in.server_name if hasattr(action_in, "server_name") else (action_in.get("server_name") if isinstance(action_in, dict) else None))

        row = ServerAction(
            action_type=action_in.action_type if hasattr(action_in, "action_type") else action_in["action_type"],
            target_steamid=action_in.target_steamid if hasattr(action_in, "target_steamid") else action_in["target_steamid"],
            reason=action_in.reason if hasattr(action_in, "reason") else (action_in.get("reason") if isinstance(action_in, dict) else None),
            duration_minutes=action_in.duration_minutes if hasattr(action_in, "duration_minutes") else (action_in.get("duration_minutes") if isinstance(action_in, dict) else None),
            message_text=action_in.message_text if hasattr(action_in, "message_text") else (action_in.get("message_text") if isinstance(action_in, dict) else None),
            target_server=effective_server,  # ASPER1, ASPER2, or NULL=both
            queued_by_admin_id=admin_id,
            created_at=datetime.utcnow(),
            status_asper1=0 if (not effective_server or effective_server.upper() == "ASPER1") else 1,  # 1=not applicable (skip)
            status_asper2=0 if (not effective_server or effective_server.upper() == "ASPER2") else 1,
            payload=payload,
        )
        # If targeted to one server, mark the other as "done" (not applicable)
        if effective_server and effective_server.upper() == "ASPER1":
            row.status_asper2 = 1  # ASPER2 doesn't need to do anything
        elif effective_server and effective_server.upper() == "ASPER2":
            row.status_asper1 = 1  # ASPER1 doesn't need to do anything

        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_pending_actions(self, server_name: Optional[str] = None) -> List:
        """Get pending actions for a server from the v2 table."""
        from models import ServerAction

        query = self.db.query(ServerAction)

        if server_name:
            sn = server_name.strip().upper()
            if sn == "ASPER1":
                query = query.filter(ServerAction.status_asper1 == 0)
            elif sn == "ASPER2":
                query = query.filter(ServerAction.status_asper2 == 0)
            else:
                # Unknown server — return all with any pending
                from sqlalchemy import or_
                query = query.filter(or_(ServerAction.status_asper1 == 0, ServerAction.status_asper2 == 0))
        else:
            # No server filter — return anything pending
            from sqlalchemy import or_
            query = query.filter(or_(ServerAction.status_asper1 == 0, ServerAction.status_asper2 == 0))

        return query.order_by(ServerAction.created_at, ServerAction.id).all()

    def acknowledge_action(self, action_id: int, success: bool, error_message: Optional[str] = None, server_name: Optional[str] = None) -> bool:
        """ACK an action from a specific server (v2 table)."""
        from datetime import datetime
        from models import ServerAction, WarehouseItem

        row = (
            self.db.query(ServerAction)
            .filter(ServerAction.id == action_id)
            .with_for_update()
            .first()
        )
        if not row:
            return False

        # For unban: "not in banlist" = success (goal achieved) but still record the error
        effective_success = success
        if not success and row.action_type == "unban" and error_message:
            if "not in" in error_message.lower() or "not found" in error_message.lower():
                effective_success = True

        status_val = 1 if effective_success else 2
        now = datetime.utcnow()

        # Remember whether this ACK actually transitions the addressed action
        # from pending. Game plugins retry requests after network timeouts, so
        # a duplicate failed ACK must never refund reserved cargo twice.
        addressed_pending = False

        if server_name:
            sn = server_name.strip().upper()
            if "ASPER1" in sn or sn == "1":
                addressed_pending = row.status_asper1 == 0
                row.status_asper1 = status_val
                row.error_asper1 = error_message  # Always record error text
                row.executed_at_asper1 = now
            elif "ASPER2" in sn or sn == "2":
                addressed_pending = row.status_asper2 == 0
                row.status_asper2 = status_val
                row.error_asper2 = error_message
                row.executed_at_asper2 = now
        else:
            addressed_pending = row.status_asper1 == 0 or row.status_asper2 == 0
            row.status_asper1 = status_val
            row.status_asper2 = status_val
            row.error_asper1 = error_message
            row.error_asper2 = error_message
            row.executed_at_asper1 = now
            row.executed_at_asper2 = now

        cargo_refunded = False
        payload = dict(row.payload or {})
        if (
            row.action_type == "cargo_spawn"
            and not effective_success
            and addressed_pending
            and not payload.get("warehouse_refunded")
        ):
            for item in payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                identifier = str(item.get("identifier") or "").strip()
                quantity = item.get("quantity")
                if not identifier or not isinstance(quantity, int) or quantity <= 0:
                    continue
                stock = (
                    self.db.query(WarehouseItem)
                    .filter(WarehouseItem.identifier == identifier)
                    .with_for_update()
                    .first()
                )
                if stock is None:
                    stock = WarehouseItem(identifier=identifier, quantity=0)
                    self.db.add(stock)
                stock.quantity += quantity
                stock.updated_by_admin_id = row.queued_by_admin_id
                cargo_refunded = True

            # Persist the idempotency marker even if a malformed legacy action
            # had no restorable items; otherwise every repeated ACK would try
            # the same recovery path again.
            payload["warehouse_refunded"] = True
            payload["warehouse_refunded_at"] = now.isoformat()
            row.payload = payload

        self.db.commit()

        logger.info(
            "server_action.ack action_id=%s action_type=%s server=%s "
            "success=%s status=%s cargo_refunded=%s error=%r",
            row.id,
            row.action_type,
            server_name or "both",
            effective_success,
            status_val,
            cargo_refunded,
            error_message,
        )

        # ── Send personal notification to the admin who queued this action ──
        if row.queued_by_admin_id:
            try:
                from routes.notifications import create_notification
                action_labels = {
                    "ban": "Бан", "kick": "Кик", "unban": "Разбан",
                    "message": "Сообщение", "trap": "Ловушка",
                    "cargo_spawn": "Поставка",
                }
                label = action_labels.get(row.action_type, row.action_type)
                srv = server_name or "сервер"
                if effective_success:
                    create_notification(
                        self.db,
                        admin_id=row.queued_by_admin_id,
                        title=f"{label} исполнен",
                        body=f"{label} для {row.target_steamid} выполнен на {srv}",
                        ntype="success",
                        category="server_action",
                        icon="✅",
                        link=None,
                    )
                else:
                    refund_note = " Товар возвращён на склад." if cargo_refunded else ""
                    create_notification(
                        self.db,
                        admin_id=row.queued_by_admin_id,
                        title=f"{label} — ошибка",
                        body=(
                            f"{label} для {row.target_steamid} на {srv}: "
                            f"{error_message or 'неизвестная ошибка'}.{refund_note}"
                        ),
                        ntype="error",
                        category="server_action",
                        icon="❌",
                    )
            except Exception as _ne:
                logging.warning(f"Failed to create action notification: {_ne}")

        # If this was a successful trap ACK, fire Discord notification
        # For traps: trigger on first successful ACK only (check if not already triggered)
        if success and row.action_type == "trap":
            # Guard against double-firing: check if trap already created for this action
            existing_trap = self.db.query(PlayerTrap).filter(
                PlayerTrap.target_steamid == row.target_steamid,
                PlayerTrap.active == 0,
                PlayerTrap.triggered_at.isnot(None),
            ).order_by(PlayerTrap.triggered_at.desc()).first()
            # Only fire if no recent trap was triggered for this steamid in last 60 seconds
            from datetime import timedelta
            already_fired = False
            if existing_trap and existing_trap.triggered_at:
                if (datetime.utcnow() - existing_trap.triggered_at) < timedelta(seconds=60):
                    already_fired = True
            if not already_fired:
                self._handle_trap_triggered(row)

        return True

    def _handle_trap_triggered(self, action: ServerActionQueue):
        """Create PlayerTrap record and fire Discord webhook when trap ACK received."""
        try:
            steamid = action.target_steamid
            player = self.db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
            player_id = player.player_id if player else None
            player_name = player.player_last_time_name if player else steamid

            # Create trap record (already triggered)
            trap = PlayerTrap(
                player_id=player_id,
                target_steamid=steamid,
                reason=action.reason,
                set_by_admin_id=action.queued_by_admin_id,
                active=0,
                triggered_at=_now_msk(),
            )
            self.db.add(trap)
            self.db.commit()

            # Notify Discord bot directly instead of queuing
            trap_msg = (
                f"🪤 **Ловушка сработала!**\n"
                f"**Игрок:** {player_name}\n"
                f"**SteamID:** `{steamid}`\n"
                f"**Player ID:** {player_id or '-'}\n"
                f"**Причина ловушки:** {action.reason or '-'}"
            )
            from discord_bot import bot as discord_bot
            discord_bot.schedule(discord_bot.execute_bot_action("trap_triggered", {
                "player_name": player_name,
                "steamid": steamid,
                "player_id": player_id,
                "reason": action.reason,
                "message": trap_msg,
            }))
            logger.info(f"[TRAP] Notified bot for {steamid}")
        except Exception as e:
            logger.error(f"_handle_trap_triggered error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Player Notes (Personal File)
# ══════════════════════════════════════════════════════════════════════════════

class PlayerNoteService:
    def __init__(self, db: Session):
        self.db = db

    def get_notes(self, player_id: int) -> List[PlayerNote]:
        return (self.db.query(PlayerNote)
                .filter(PlayerNote.player_id == player_id)
                .order_by(PlayerNote.created_at.desc())
                .all())

    def save_note(self, player_id: int, admin_id: int, content: str) -> PlayerNote:
        """Always creates a new note — the player's notes are a running log,
        not a single slot (each note is independently editable, see update_note)."""
        note = PlayerNote(player_id=player_id, admin_id=admin_id, content=content)
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)
        return note

    def update_note(self, note_id: int, player_id: int, content: str) -> Optional[PlayerNote]:
        note = (self.db.query(PlayerNote)
                .filter(PlayerNote.id == note_id, PlayerNote.player_id == player_id)
                .first())
        if not note:
            return None
        note.content = content
        note.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(note)
        return note

    def delete_note(self, note_id: int, player_id: int) -> bool:
        # player_id в фильтре — та же защита, что и у update_note: не даёт
        # note_id с ЧУЖОГО профиля удалить заметку не в том контексте
        # (эндпоинт всё равно берёт note_id из URL текущего профиля, но
        # дублировать проверку на уровне сервиса дешевле, чем полагаться
        # только на то, что вызывающий код никогда не ошибётся).
        note = (self.db.query(PlayerNote)
                .filter(PlayerNote.id == note_id, PlayerNote.player_id == player_id)
                .first())
        if not note:
            return False
        self.db.delete(note)
        self.db.commit()
        return True


# ══════════════════════════════════════════════════════════════════════════════
# Label Categories
# ══════════════════════════════════════════════════════════════════════════════

class LabelFolderService:
    def __init__(self, db: Session):
        self.db = db
    def get_all(self):
        from models import LabelFolder
        return self.db.query(LabelFolder).order_by(LabelFolder.sort_order, LabelFolder.name).all()
    def create(self, name: str, admin_id: int):
        from models import LabelFolder
        max_order = self.db.query(LabelFolder).order_by(LabelFolder.sort_order.desc()).first()
        order = (max_order.sort_order + 1) if max_order else 0
        folder = LabelFolder(name=name, sort_order=order, created_by_admin_id=admin_id)
        self.db.add(folder); self.db.commit(); self.db.refresh(folder)
        return folder
    def update(self, folder_id: int, name: str):
        from models import LabelFolder
        f = self.db.query(LabelFolder).filter(LabelFolder.id == folder_id).first()
        if not f: return None
        f.name = name; self.db.commit(); return f
    def delete(self, folder_id: int) -> bool:
        from models import LabelFolder, LabelCategory
        f = self.db.query(LabelFolder).filter(LabelFolder.id == folder_id).first()
        if not f: return False
        self.db.query(LabelCategory).filter(LabelCategory.folder_id == folder_id).update({LabelCategory.folder_id: None})
        self.db.delete(f); self.db.commit(); return True
    def reorder(self, ordered_ids: list):
        from models import LabelFolder
        for idx, fid in enumerate(ordered_ids):
            self.db.query(LabelFolder).filter(LabelFolder.id == fid).update({LabelFolder.sort_order: idx})
        self.db.commit()


class LabelCategoryService:
    def __init__(self, db: Session):
        self.db = db

    def get_all(self) -> List[LabelCategory]:
        return self.db.query(LabelCategory).order_by(LabelCategory.sort_order, LabelCategory.name).all()

    def create(self, name: str, color: str, icon: str, admin_id: int, folder_id=None, description=None) -> LabelCategory:
        max_order = self.db.query(LabelCategory).order_by(LabelCategory.sort_order.desc()).first()
        order = (max_order.sort_order + 1) if max_order and max_order.sort_order is not None else 0
        cat = LabelCategory(name=name, color=color, icon=icon, created_by_admin_id=admin_id, folder_id=folder_id, sort_order=order, description=description)
        self.db.add(cat)
        self.db.commit()
        self.db.refresh(cat)
        return cat

    def update(self, cat_id: int, name: str = None, color: str = None, icon: str = None, folder_id=None, description=None, has_folder=False, has_desc=False) -> Optional[LabelCategory]:
        cat = self.db.query(LabelCategory).filter(LabelCategory.id == cat_id).first()
        if not cat:
            return None
        if name is not None: cat.name = name
        if color is not None: cat.color = color
        if icon is not None: cat.icon = icon
        if has_folder: cat.folder_id = folder_id
        if has_desc: cat.description = description
        self.db.commit()
        return cat

    def reorder(self, ordered_ids: list):
        for idx, cid in enumerate(ordered_ids):
            self.db.query(LabelCategory).filter(LabelCategory.id == cid).update({LabelCategory.sort_order: idx})
        self.db.commit()

    def delete(self, cat_id: int) -> bool:
        cat = self.db.query(LabelCategory).filter(LabelCategory.id == cat_id).first()
        if not cat:
            return False
        # remove all label assignments for this category first
        self.db.query(PlayerLabel).filter(PlayerLabel.category_id == cat_id).delete()
        self.db.delete(cat)
        self.db.commit()
        return True


# ══════════════════════════════════════════════════════════════════════════════
# Player Labels
# ══════════════════════════════════════════════════════════════════════════════

class PlayerLabelService:
    def __init__(self, db: Session):
        self.db = db

    def get_labels(self, player_id: int) -> List:
        """Returns list of (PlayerLabel, LabelCategory) tuples."""
        return (self.db.query(PlayerLabel, LabelCategory)
                .join(LabelCategory, PlayerLabel.category_id == LabelCategory.id)
                .filter(PlayerLabel.player_id == player_id)
                .all())

    def assign(self, player_id: int, category_id: int, note: Optional[str], admin_id: int) -> PlayerLabel:
        # prevent duplicate
        existing = (self.db.query(PlayerLabel)
                    .filter(PlayerLabel.player_id == player_id,
                            PlayerLabel.category_id == category_id)
                    .first())
        if existing:
            existing.note = note
            existing.assigned_by_admin_id = admin_id
            self.db.commit()
            return existing
        lbl = PlayerLabel(player_id=player_id, category_id=category_id,
                          note=note, assigned_by_admin_id=admin_id)
        self.db.add(lbl)
        self.db.commit()
        self.db.refresh(lbl)
        return lbl

    def remove(self, label_id: int) -> bool:
        lbl = self.db.query(PlayerLabel).filter(PlayerLabel.id == label_id).first()
        if not lbl:
            return False
        self.db.delete(lbl)
        self.db.commit()
        return True

    def get_labeled_players(self, category_id: Optional[int] = None,
                             search: Optional[str] = None) -> List:
        """Returns list of (PlayerLabel, LabelCategory, PlayerData) tuples."""
        q = (self.db.query(PlayerLabel, LabelCategory, PlayerData)
             .join(LabelCategory, PlayerLabel.category_id == LabelCategory.id)
             .join(PlayerData, PlayerLabel.player_id == PlayerData.player_id))
        if category_id:
            q = q.filter(PlayerLabel.category_id == category_id)
        if search:
            q = q.filter(
                PlayerData.player_last_time_name.ilike(f"%{search}%") |
                PlayerData.player_steamid.ilike(f"%{search}%")
            )
        return q.order_by(PlayerLabel.assigned_at.desc()).all()


# ══════════════════════════════════════════════════════════════════════════════
# Player Traps
# ══════════════════════════════════════════════════════════════════════════════

class PlayerTrapService:
    def __init__(self, db: Session):
        self.db = db

    def get_traps(self, player_id: int) -> List[PlayerTrap]:
        return (self.db.query(PlayerTrap)
                .filter(PlayerTrap.player_id == player_id)
                .order_by(PlayerTrap.set_at.desc())
                .all())

    def set_trap(self, player_id: int, steamid: str, reason: Optional[str],
                 admin_id: int) -> PlayerTrap:
        # deactivate existing active traps for this player
        (self.db.query(PlayerTrap)
         .filter(PlayerTrap.player_id == player_id, PlayerTrap.active == 1)
         .update({"active": 0}))
        trap = PlayerTrap(player_id=player_id, target_steamid=steamid,
                          reason=reason, set_by_admin_id=admin_id, active=1)
        self.db.add(trap)
        self.db.commit()
        self.db.refresh(trap)
        return trap

    def deactivate(self, trap_id: int) -> bool:
        trap = self.db.query(PlayerTrap).filter(PlayerTrap.id == trap_id).first()
        if not trap:
            return False
        trap.active = 0
        self.db.commit()
        return True

    def get_active_traps(self) -> List[PlayerTrap]:
        return self.db.query(PlayerTrap).filter(PlayerTrap.active == 1).all()

    def trigger(self, trap_id: int) -> bool:
        trap = self.db.query(PlayerTrap).filter(PlayerTrap.id == trap_id).first()
        if not trap:
            return False
        trap.active = 0
        trap.triggered_at = datetime.utcnow()
        self.db.commit()
        return True


# ══════════════════════════════════════════════════════════════════════════════
# Twink Detection
# ══════════════════════════════════════════════════════════════════════════════

class TwinkDetectionService:
    """Detects possible alt accounts (twinks) based on shared IPs, subnets, names."""

    def __init__(self, db: Session):
        self.db = db

    def analyze(self, player_id: int) -> List[dict]:
        """
        Returns list of suspect players with match reasons.
        Checks:
          1. Shared IP address
          2. Shared subnet
          3. Similar name (prefix match >= 6 chars)
        """
        from models import PlayerIP, PlayerName
        target = self.db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
        if not target:
            return []

        # Get target IPs and subnets
        target_ips_rows = (self.db.query(PlayerIP)
                           .filter(PlayerIP.player_id == player_id).all())
        target_ips = {r.ip for r in target_ips_rows}
        target_subnets = {r.subnet for r in target_ips_rows}

        # Get target names
        target_name_rows = (self.db.query(PlayerName)
                            .filter(PlayerName.player_id == player_id).all())
        target_names = {n.nickname.lower() for n in target_name_rows}
        if target.player_last_time_name:
            target_names.add(target.player_last_time_name.lower())

        suspects: Dict[int, dict] = {}  # player_id -> result

        def _add(pid: int, reason: str):
            if pid == player_id:
                return
            if pid not in suspects:
                suspects[pid] = {"player_id": pid, "reasons": [], "player": None}
            suspects[pid]["reasons"].append(reason)

        # 1. Shared IPs
        if target_ips:
            shared_ip_rows = (self.db.query(PlayerIP)
                              .filter(PlayerIP.ip.in_(target_ips),
                                      PlayerIP.player_id != player_id).all())
            for r in shared_ip_rows:
                _add(r.player_id, f"Одинаковый IP: {r.ip}")

        # 2. Shared subnets (weaker signal, only add if not already IP-matched)
        if target_subnets:
            shared_subnet_rows = (self.db.query(PlayerIP)
                                  .filter(PlayerIP.subnet.in_(target_subnets),
                                          PlayerIP.player_id != player_id).all())
            for r in shared_subnet_rows:
                already_ip = any("Одинаковый IP" in reason
                                  for reason in suspects.get(r.player_id, {}).get("reasons", []))
                if not already_ip:
                    _add(r.player_id, f"Одинаковая подсеть: {r.subnet}")

        # 3. Similar names (prefix >= 6 chars)
        for tname in target_names:
            if len(tname) < 6:
                continue
            prefix = tname[:6]
            similar_names = (self.db.query(PlayerName)
                             .filter(PlayerName.nickname.ilike(f"{prefix}%"),
                                     PlayerName.player_id != player_id).all())
            for n in similar_names:
                _add(n.player_id, f"Похожее имя: \u00ab{n.nickname}\u00bb")

        if not suspects:
            return []

        # Fetch PlayerData for all suspects
        pids = list(suspects.keys())
        players = {p.player_id: p for p in
                   self.db.query(PlayerData).filter(PlayerData.player_id.in_(pids)).all()}
        result = []
        for pid, info in suspects.items():
            pd = players.get(pid)
            result.append({
                "player_id": pid,
                "name": pd.player_last_time_name if pd else str(pid),
                "steamid": pd.player_steamid if pd else "",
                "play_time": pd.play_time if pd else 0,
                "reasons": info["reasons"],
                "reason_count": len(info["reasons"]),
            })
        # Sort by number of matching reasons (strongest suspicion first)
        result.sort(key=lambda x: -x["reason_count"])
        return result

    def get_all_twink_pairs(self, min_signals: int = 1) -> List[dict]:
        """
        Global analysis: find all player pairs sharing IPs or subnets.
        Returns list of groups (cluster) keyed by representative player.
        Performance: uses a single JOIN on player_ips, then groups in Python.
        """
        from models import PlayerIP
        from sqlalchemy import text

        # Self-join: find all (A, B) sharing the same IP
        ip_pairs = self.db.execute(text(
            "SELECT a.player_id AS pid_a, b.player_id AS pid_b, a.ip AS shared_ip "
            "FROM player_ips a JOIN player_ips b "
            "ON a.ip = b.ip AND a.player_id < b.player_id"
        )).fetchall()

        # Self-join by subnet (weaker signal)
        subnet_pairs = self.db.execute(text(
            "SELECT a.player_id AS pid_a, b.player_id AS pid_b, a.subnet AS shared_subnet "
            "FROM player_ips a JOIN player_ips b "
            "ON a.subnet = b.subnet AND a.player_id < b.player_id"
        )).fetchall()

        # Build edge map: pair -> set of reasons
        edges: Dict[tuple, list] = {}
        for row in ip_pairs:
            key = (row.pid_a, row.pid_b)
            edges.setdefault(key, []).append(f"Одинаковый IP: {row.shared_ip}")
        for row in subnet_pairs:
            key = (row.pid_a, row.pid_b)
            # Skip if IP reason already added for this pair (subnet is weaker)
            if not any("Одинаковый IP" in r for r in edges.get(key, [])):
                edges.setdefault(key, []).append(f"Подсеть: {row.shared_subnet}")

        if not edges:
            return []

        # Filter by min_signals
        edges = {k: v for k, v in edges.items() if len(v) >= min_signals}

        # Collect all player IDs involved
        all_pids = set()
        for a, b in edges:
            all_pids.add(a)
            all_pids.add(b)

        players = {p.player_id: p for p in
                   self.db.query(PlayerData).filter(PlayerData.player_id.in_(list(all_pids))).all()}

        result = []
        for (pid_a, pid_b), reasons in edges.items():
            pa = players.get(pid_a)
            pb = players.get(pid_b)
            result.append({
                "player_a": {
                    "player_id": pid_a,
                    "name": pa.player_last_time_name if pa else str(pid_a),
                    "steamid": pa.player_steamid if pa else "",
                    "play_time": pa.play_time if pa else 0,
                },
                "player_b": {
                    "player_id": pid_b,
                    "name": pb.player_last_time_name if pb else str(pid_b),
                    "steamid": pb.player_steamid if pb else "",
                    "play_time": pb.play_time if pb else 0,
                },
                "reasons": reasons,
                "signal_count": len(reasons),
            })

        result.sort(key=lambda x: -x["signal_count"])
        return result


# ══════════════════════════════════════════════════════════════════════════════
# Punishment Service
# ══════════════════════════════════════════════════════════════════════════════

PUNISHMENT_TYPES = ("warning", "reprimand", "kick", "ban")

PUNISHMENT_TYPE_LABELS = {
    "warning":   "Предупреждение",
    "reprimand": "Выговор",
    "kick":      "Кик",
    "ban":       "Бан",
}


class PunishmentService:
    """CRUD for player_punishments."""

    def __init__(self, db: Session):
        self.db = db
        from models import PlayerPunishment
        self._model = PlayerPunishment

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_by_id(self, punishment_id: int):
        return self.db.query(self._model).filter(self._model.id == punishment_id).first()

    def get_for_player(self, player_id: int):
        return (self.db.query(self._model)
                .filter(self._model.player_id == player_id)
                .order_by(self._model.issued_at.desc())
                .all())

    def list_by_type(self, ptype: str, search: str = "", status: str = "", superuser_only: bool = False,
                      limit: int = 100, offset: int = 0):
        """Return paginated punishments of given type, optionally filtered by
        player name/steamid/reason/issuer text, effective status
        (active/expired/void) and whether the issuer was a superuser.

        status фильтруется в SQL, а не через effective_status() постфактум —
        иначе LIMIT/OFFSET резали бы страницу ДО фильтрации и total/пагинация
        были бы враньём (то же соображение, что и в search выше)."""
        from models import PlayerData
        q = (self.db.query(self._model, PlayerData)
             .outerjoin(PlayerData, PlayerData.player_id == self._model.player_id)
             .filter(self._model.type == ptype))
        if search:
            like = f"%{search}%"
            q = q.filter(
                PlayerData.player_last_time_name.ilike(like) |
                PlayerData.player_steamid.ilike(like) |
                self._model.player_steamid.ilike(like) |
                self._model.reason.ilike(like) |
                self._model.issued_by_name.ilike(like) |
                self._model.notes.ilike(like)
            )
        if status == "void":
            q = q.filter(self._model.is_active == 0)
        elif status == "expired":
            q = q.filter(self._model.is_active == 1, self._model.expires_at.isnot(None), self._model.expires_at < _now_msk())
        elif status == "active":
            q = q.filter(
                self._model.is_active == 1,
                (self._model.expires_at.is_(None)) | (self._model.expires_at >= _now_msk()),
            )
        if superuser_only:
            q = q.filter(self._model.issued_by_was_superuser == True)  # noqa: E712
        total = q.count()
        rows = q.order_by(self._model.issued_at.desc()).offset(offset).limit(limit).all()
        return rows, total

    # ── Mutations ─────────────────────────────────────────────────────────────

    def create(self, player_id: int, player_steamid: str, player_ip: Optional[str],
               ptype: str, reason: Optional[str], issued_by_admin_id: int,
               issued_by_name: str, expires_at: Optional[datetime],
               notes: Optional[str] = None, issued_by_was_superuser: bool = False):
        from models import PlayerPunishment
        p = PlayerPunishment(
            player_id=player_id,
            player_steamid=player_steamid,
            player_ip=player_ip,
            type=ptype,
            reason=reason,
            issued_by_admin_id=issued_by_admin_id,
            issued_by_name=issued_by_name,
            issued_by_was_superuser=issued_by_was_superuser,
            issued_at=_now_msk(),
            expires_at=expires_at,
            is_active=1,
            notes=notes,
        )
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        self._notify_player_ingame(p)
        return p

    def _notify_player_ingame(self, punishment) -> None:
        """Ставит игровому серверу задачу показать игроку окно с причиной.

        Только для предупреждений и выговоров: кик и бан игрок и так замечает —
        его выкидывает с сервера, а окно показать уже некому.

        Ошибки намеренно проглатываются: наказание уже сохранено, и если
        уведомить игрока не вышло (сервер недоступен, игрок оффлайн), это не
        повод откатывать саму запись о наказании.
        """
        if punishment.type not in ("warning", "reprimand"):
            return
        try:
            from models import ServerAction

            label = "предупреждение" if punishment.type == "warning" else "выговор"
            reason = (punishment.reason or "").strip() or "причина не указана"
            issued_by = (punishment.issued_by_name or "").strip()
            text = f"[Администрация]: Внимание! Вы получили {label}! Причина: {reason}"
            if issued_by:
                text += f"\nВыдал: {issued_by}"

            # Отправляем на тот сервер, где игрок сейчас находится: иначе
            # второй сервер отчитается ошибкой «игрок не найден», и в панели
            # это будет выглядеть как сбой, хотя всё в порядке. Если игрок
            # оффлайн — target_server=None, задачу заберёт тот сервер, на
            # который игрок зайдёт (и покажет окно при следующей проверке).
            target_server = None
            try:
                for info in ServerModerationService(self.db).get_online_players():
                    if info.get("player_steamid") == punishment.player_steamid:
                        target_server = info.get("server_name")
                        break
            except Exception:
                pass

            self.db.add(ServerAction(
                action_type="msg_box",
                target_steamid=punishment.player_steamid,
                reason=punishment.reason,
                message_text=text,
                target_server=target_server,
                queued_by_admin_id=punishment.issued_by_admin_id,
                created_at=_now_msk(),
            ))
            self.db.commit()
        except Exception as e:
            logger.warning(f"Не удалось поставить уведомление о наказании игроку: {e}")

    def update(self, punishment_id: int, **fields) -> bool:
        p = self.get_by_id(punishment_id)
        if not p:
            return False
        for k, v in fields.items():
            if hasattr(p, k):
                setattr(p, k, v)
        self.db.commit()
        return True

    def delete(self, punishment_id: int) -> bool:
        p = self.get_by_id(punishment_id)
        if not p:
            return False
        self.db.delete(p)
        self.db.commit()
        return True

    def set_void(self, punishment_id: int) -> bool:
        """Mark punishment as inactive (void/irrelevant)."""
        return self.update(punishment_id, is_active=0)

    def set_all_active_bans_void(self, player_steamid: str) -> int:
        """Deactivate every DB ban for one Steam account.

        A server unban is account-wide, so leaving an older duplicate ban
        active makes ``/bans/check`` immediately block the player again.
        """
        steamid = (player_steamid or "").strip()
        if not steamid:
            return 0
        updated = (
            self.db.query(self._model)
            .filter(
                self._model.type == "ban",
                self._model.is_active == 1,
                self._model.player_steamid == steamid,
            )
            .update({self._model.is_active: 0}, synchronize_session=False)
        )
        self.db.commit()
        return int(updated)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_expired(self, punishment) -> bool:
        if not punishment.expires_at:
            return False
        return punishment.expires_at < _now_msk()

    def effective_status(self, punishment) -> str:
        """'active' | 'expired' | 'void'"""
        if not punishment.is_active:
            return "void"
        if self.is_expired(punishment):
            return "expired"
        return "active"
