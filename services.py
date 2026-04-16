from sqlalchemy.orm import Session
from typing import Optional, List, Tuple
from fastapi import HTTPException
import logging
import os
import httpx
from models import (
    PlayerData, PlayerStats, AdminUser, PlayerKill, PlayerRoleAction,
    PlayerIP, PlayerName, PlayerRoleStat, PlayerSession, ServerPlayerPrivilege
)
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
        player_id = payload.get("player_id")
        login_time_str = payload.get("login_time")
        
        if not all([player_id, login_time_str]):
            raise HTTPException(status_code=400, detail="player_id, login_time are required")
        
        try:
            login_time = parse_iso_datetime(login_time_str)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        session = self.session_service.login(int(player_id), login_time)
        return {"session_id": session.id, "player_id": player_id, "session_number": session.session_number}
    
    def _handle_player_session_logout(self, payload: dict) -> dict:
        session_id = payload.get("session_id")
        logout_time_str = payload.get("logout_time")
        
        if not all([session_id, logout_time_str]):
            raise HTTPException(status_code=400, detail="session_id, logout_time are required")
        
        try:
            logout_time = parse_iso_datetime(logout_time_str)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        session = self.session_service.logout(int(session_id), logout_time)
        return {"session_id": session_id, "duration_seconds": session.session_duration or 0}


class PlayerService:
    """Business logic for player operations"""
    
    def __init__(self, db: Session):
        self.player_repo = PlayerRepository(db)
        self.stats_repo = PlayerStatsRepository(db)
    
    def get_all_players(self, page: int = 1, page_size: int = 50) -> Tuple[List[PlayerDataOut], int]:
        """Get all players with pagination"""
        if page < 1:
            raise HTTPException(status_code=400, detail="Page must be >= 1")
        
        players, total = self.player_repo.get_all(page, page_size)
        player_outs = [PlayerDataOut.from_orm_with_mappings(p, settings.ranks_mapping, settings.kits_mapping) for p in players]
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
                "play_time": player_data.play_time,
                "rank_id": player_data.rank_id,
                "kit_level": player_data.kit_level,
                "achievements": safe_get_list(player_data.achievements),
            }
            
            if player_data.last_login_time:
                try:
                    update_data["last_login_time"] = parse_iso_datetime(
                        player_data.last_login_time
                    )
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))
            
            logger.info(f"Updating player: {existing.player_id}")
            updated_player = self.player_repo.update(existing.player_id, update_data)
            return PlayerDataOut.from_orm_with_mappings(updated_player, settings.ranks_mapping, settings.kits_mapping)
        
        # Create new player
        new_player = PlayerData(
            player_last_time_name=player_data.player_last_time_name,
            player_steamid=player_data.player_steamid,
            play_time=safe_get_int(player_data.play_time),
            rank_id=player_data.rank_id,
            kit_level=player_data.kit_level,
            achievements=safe_get_list(player_data.achievements),
        )
        
        if player_data.last_login_time:
            try:
                new_player.last_login_time = parse_iso_datetime(
                    player_data.last_login_time
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        
        created_player = self.player_repo.create(new_player)
        
        # Create stats for new player
        new_stats = PlayerStats(player_id=created_player.player_id)
        self.stats_repo.create(created_player.player_id, new_stats)
        
        return PlayerDataOut.from_orm_with_mappings(created_player, settings.ranks_mapping, settings.kits_mapping)
    
    def search_players(self, query: str, limit: int = 10) -> List[PlayerData]:
        """Search players by name or SteamID"""
        return self.player_repo.search(query, limit)


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
    """Service for Discord OAuth authentication via Proxy"""
    
    DISCORD_OAUTH_URL = "https://discord.com/api/v10/oauth2/authorize"
    
    def __init__(self, use_proxy: bool = None, proxy_url: str = None):
        """
        Initialize Discord Auth Service
        
        Args:
            use_proxy: Whether to use Discord API proxy (if None, uses config)
            proxy_url: URL of the proxy server (if None, uses config)
        """
        self.client_id = settings.discord_client_id
        self.client_secret = settings.discord_client_secret
        self.redirect_uri = settings.discord_redirect_uri
        
        # Используем значения из конфига если не переданы явно
        self.use_proxy = use_proxy if use_proxy is not None else settings.use_discord_proxy
        self.proxy_url = proxy_url or settings.discord_proxy_url
        
        if self.use_proxy:
            logger.info(f"Using Discord API Proxy: {self.proxy_url}")
    
    def get_oauth_url(self, state: str = None) -> str:
        """Generate Discord OAuth URL (always direct to Discord)"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "identify",
        }
        if state:
            params["state"] = state
        
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.DISCORD_OAUTH_URL}?{query}"
    
    async def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for access token"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if self.use_proxy:
                    # Используем прокси
                    response = await client.post(
                        f"{self.proxy_url}/oauth/token",
                        params={
                            "code": code,
                            "redirect_uri": self.redirect_uri,
                        }
                    )
                else:
                    # Прямое подключение к Discord
                    response = await client.post(
                        "https://discord.com/api/v10/oauth2/token",
                        data={
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "grant_type": "authorization_code",
                            "code": code,
                            "redirect_uri": self.redirect_uri,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"}
                    )
                
                response.raise_for_status()
                return response.json()
            
            except Exception as e:
                logger.error(f"Failed to exchange code: {e}")
                raise HTTPException(status_code=500, detail="Authentication failed")
    
    async def get_user_info(self, access_token: str) -> dict:
        """Get user info from Discord API"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if self.use_proxy:
                    # Используем прокси
                    response = await client.get(
                        f"{self.proxy_url}/users/@me",
                        params={"access_token": access_token}
                    )
                else:
                    # Прямое подключение к Discord
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
        """Get all admin users"""
        return self.db.query(AdminUser).all()
    
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
        """Delete admin user"""
        admin = self.db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        self.db.delete(admin)
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


