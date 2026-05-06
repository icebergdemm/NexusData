from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Tuple
from models import (
    Kit, PlayerData, PlayerStats, PlayerKill, PlayerRoleAction,
    PlayerIP, PlayerName, PlayerRoleStat, PlayerSession
)
from fastapi import HTTPException
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base repository with common CRUD operations"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def commit(self):
        """Commit changes to database"""
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            logger.error(f"Integrity error: {e}")
            raise HTTPException(status_code=409, detail="Duplicate entry")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Database error: {e}")
            raise HTTPException(status_code=500, detail="Database error")
    
    def refresh(self, obj):
        """Refresh object from database"""
        self.db.refresh(obj)


class KitRepository(BaseRepository):
    """Repository for Kit model"""
    
    def get_all(self, page: int = 1, page_size: int = 50) -> Tuple[List[Kit], int]:
        """Get all kits with pagination"""
        query = self.db.query(Kit)
        total = query.count()
        
        kits = query.offset((page - 1) * page_size).limit(page_size).all()
        return kits, total
    
    def get_by_id(self, kit_id: int) -> Optional[Kit]:
        return self.db.query(Kit).filter(Kit.id == kit_id).first()
    
    def get_by_job_id(self, job_id: str) -> Optional[Kit]:
        return self.db.query(Kit).filter(Kit.job_id == job_id).first()


class PlayerRepository(BaseRepository):
    """Repository for PlayerData model"""
    
    def get_all(self, page: int = 1, page_size: int = 50) -> Tuple[List[PlayerData], int]:
        """Get all players with pagination"""
        query = self.db.query(PlayerData)
        total = query.count()
        
        players = query.offset((page - 1) * page_size).limit(page_size).all()
        return players, total
    
    def get_by_id(self, player_id: int) -> Optional[PlayerData]:
        return self.db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    
    def get_by_steamid(self, steamid: str) -> Optional[PlayerData]:
        return self.db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
    
    def get_by_name(self, name: str) -> Optional[PlayerData]:
        return self.db.query(PlayerData).filter(
            PlayerData.player_last_time_name.ilike(f"%{name}%")
        ).first()
    
    def create(self, player: PlayerData) -> PlayerData:
        self.db.add(player)
        self.commit()
        self.refresh(player)
        logger.info(f"Created player: {player.player_id} ({player.player_steamid})")
        return player
    
    def update(self, player_id: int, data: dict) -> Optional[PlayerData]:
        player = self.get_by_id(player_id)
        if not player:
            return None
        
        for key, value in data.items():
            if value is not None:
                setattr(player, key, value)
        
        self.commit()
        self.refresh(player)
        logger.info(f"Updated player: {player_id}")
        return player
    
    def search(self, query: str, limit: int = 10) -> List[PlayerData]:
        """
        Search players by:
        - Current nickname (player_last_time_name)
        - SteamID (all variants)
        - Previous nicknames (PlayerName table)
        - IP addresses (PlayerIP table)
        """
        from sqlalchemy import or_, func, distinct
        
        query_lower = f"%{query.lower()}%"
        
        # Search by current nickname, steamid
        direct_search = self.db.query(PlayerData).filter(
            or_(
                PlayerData.player_last_time_name.ilike(query_lower),
                PlayerData.player_steamid.ilike(query_lower),
            )
        ).limit(limit).all()
        
        if len(direct_search) >= limit:
            return direct_search[:limit]
        
        # If not enough results, search by previous nicknames
        player_ids = set(p.player_id for p in direct_search)
        remaining_limit = limit - len(direct_search)
        
        if remaining_limit > 0:
            from models import PlayerName
            nickname_results = self.db.query(PlayerData).join(
                PlayerName, PlayerData.player_id == PlayerName.player_id
            ).filter(
                PlayerName.nickname.ilike(query_lower),
                PlayerData.player_id.notin_(player_ids) if player_ids else True
            ).distinct().limit(remaining_limit).all()
            
            direct_search.extend(nickname_results)
            player_ids.update(p.player_id for p in nickname_results)
            remaining_limit = limit - len(direct_search)
        
        # If still not enough, search by IP addresses
        if remaining_limit > 0:
            from models import PlayerIP
            ip_results = self.db.query(PlayerData).join(
                PlayerIP, PlayerData.player_id == PlayerIP.player_id
            ).filter(
                or_(
                    PlayerIP.ip.ilike(query_lower),
                    PlayerIP.subnet.ilike(query_lower),
                ),
                PlayerData.player_id.notin_(player_ids) if player_ids else True
            ).distinct().limit(remaining_limit).all()
            
            direct_search.extend(ip_results)
        
        return direct_search[:limit]


class PlayerStatsRepository(BaseRepository):
    """Repository for PlayerStats model"""
    
    def get_by_player_id(self, player_id: int) -> Optional[PlayerStats]:
        return self.db.query(PlayerStats).filter(PlayerStats.player_id == player_id).first()
    
    def create(self, player_id: int, stats: PlayerStats) -> PlayerStats:
        stats.player_id = player_id
        self.db.add(stats)
        self.commit()
        self.refresh(stats)
        logger.info(f"Created stats for player: {player_id}")
        return stats
    
    def accumulate(self, player_id: int, delta: dict) -> Optional[PlayerStats]:
        """Accumulate stats by adding delta values"""
        stats = self.get_by_player_id(player_id)
        if not stats:
            return None
        
        for key, value in delta.items():
            if value is not None and value != 0:
                current = getattr(stats, key, 0)
                setattr(stats, key, current + value)
        
        self.commit()
        self.refresh(stats)
        logger.info(f"Accumulated stats for player: {player_id}")
        return stats


class PlayerKillRepository(BaseRepository):
    """Repository for PlayerKill model"""
    
    def get_by_player_and_monster(self, player_id: int, monster_type: str) -> Optional[PlayerKill]:
        return self.db.query(PlayerKill).filter(
            PlayerKill.player_id == player_id,
            PlayerKill.monster_type == monster_type
        ).first()
    
    def get_all_by_player(self, player_id: int) -> List[PlayerKill]:
        return self.db.query(PlayerKill).filter(PlayerKill.player_id == player_id).all()
    
    def create_or_update(self, player_id: int, monster_type: str, kills_count: int) -> PlayerKill:
        """Create or update kill count for monster type"""
        existing = self.get_by_player_and_monster(player_id, monster_type)
        
        if existing:
            existing.kills_count = kills_count
        else:
            existing = PlayerKill(
                player_id=player_id,
                monster_type=monster_type,
                kills_count=kills_count
            )
            self.db.add(existing)
        
        self.commit()
        self.refresh(existing)
        return existing
    
    def accumulate(self, player_id: int, monster_type: str, delta: int) -> PlayerKill:
        """Add delta to kill count"""
        existing = self.get_by_player_and_monster(player_id, monster_type)
        
        if existing:
            existing.kills_count += delta
        else:
            existing = PlayerKill(
                player_id=player_id,
                monster_type=monster_type,
                kills_count=delta
            )
            self.db.add(existing)
        
        self.commit()
        self.refresh(existing)
        return existing


class PlayerRoleActionRepository(BaseRepository):
    """Repository for PlayerRoleAction model"""
    
    def create(self, player_id: int, role: str, action_type: str, value: float) -> PlayerRoleAction:
        action = PlayerRoleAction(
            player_id=player_id,
            role=role,
            action_type=action_type,
            value=value
        )
        self.db.add(action)
        self.commit()
        self.refresh(action)
        return action
    
    def get_all_by_player(self, player_id: int) -> List[PlayerRoleAction]:
        return self.db.query(PlayerRoleAction).filter(PlayerRoleAction.player_id == player_id).all()
    
    def get_by_player_role(self, player_id: int, role: str) -> List[PlayerRoleAction]:
        return self.db.query(PlayerRoleAction).filter(
            PlayerRoleAction.player_id == player_id,
            PlayerRoleAction.role == role
        ).all()


class PlayerIPRepository(BaseRepository):
    """Repository for PlayerIP model"""
    
    def get_by_player_and_ip(self, player_id: int, ip: str) -> Optional[PlayerIP]:
        return self.db.query(PlayerIP).filter(
            PlayerIP.player_id == player_id,
            PlayerIP.ip == ip
        ).first()
    
    def get_all_by_player(self, player_id: int) -> List[PlayerIP]:
        return self.db.query(PlayerIP).filter(PlayerIP.player_id == player_id).all()
    
    def create_or_update(self, player_id: int, ip: str, subnet: str) -> PlayerIP:
        """Create IP record or update last_seen"""
        existing = self.get_by_player_and_ip(player_id, ip)
        now = datetime.now()
        
        if existing:
            existing.last_seen = now
        else:
            existing = PlayerIP(
                player_id=player_id,
                ip=ip,
                subnet=subnet,
                first_seen=now,
                last_seen=now
            )
            self.db.add(existing)
        
        self.commit()
        self.refresh(existing)
        return existing


class PlayerNameRepository(BaseRepository):
    """Repository for PlayerName model (history of nicknames)"""
    
    def get_by_player_and_nickname(self, player_id: int, nickname: str) -> Optional[PlayerName]:
        return self.db.query(PlayerName).filter(
            PlayerName.player_id == player_id,
            PlayerName.nickname == nickname
        ).first()
    
    def get_all_by_player(self, player_id: int) -> List[PlayerName]:
        return self.db.query(PlayerName).filter(PlayerName.player_id == player_id).all()
    
    def create_or_update(self, player_id: int, nickname: str) -> PlayerName:
        """Create nickname record or update last_seen"""
        existing = self.get_by_player_and_nickname(player_id, nickname)
        now = datetime.now()
        
        if existing:
            existing.last_seen = now
        else:
            existing = PlayerName(
                player_id=player_id,
                nickname=nickname,
                first_seen=now,
                last_seen=now
            )
            self.db.add(existing)
        
        self.commit()
        self.refresh(existing)
        return existing


class PlayerRoleStatRepository(BaseRepository):
    """Repository for PlayerRoleStat model"""
    
    def get_by_player_and_role(self, player_id: int, role: str) -> Optional[PlayerRoleStat]:
        return self.db.query(PlayerRoleStat).filter(
            PlayerRoleStat.player_id == player_id,
            PlayerRoleStat.role == role
        ).first()
    
    def get_all_by_player(self, player_id: int) -> List[PlayerRoleStat]:
        return self.db.query(PlayerRoleStat).filter(PlayerRoleStat.player_id == player_id).all()
    
    def create_or_update(self, player_id: int, role: str, rounds_played: int, mvp_score: float) -> PlayerRoleStat:
        """Create or update role stats"""
        existing = self.get_by_player_and_role(player_id, role)
        
        if existing:
            existing.rounds_played = rounds_played
            existing.mvp_score = mvp_score
        else:
            existing = PlayerRoleStat(
                player_id=player_id,
                role=role,
                rounds_played=rounds_played,
                mvp_score=mvp_score
            )
            self.db.add(existing)
        
        self.commit()
        self.refresh(existing)
        return existing
    
    def accumulate(self, player_id: int, role: str, rounds_delta: int, mvp_delta: float) -> PlayerRoleStat:
        """Accumulate role stats"""
        existing = self.get_by_player_and_role(player_id, role)
        
        if existing:
            existing.rounds_played += rounds_delta
            existing.mvp_score += mvp_delta
        else:
            existing = PlayerRoleStat(
                player_id=player_id,
                role=role,
                rounds_played=rounds_delta,
                mvp_score=mvp_delta
            )
            self.db.add(existing)
        
        self.commit()
        self.refresh(existing)
        return existing


class PlayerSessionRepository(BaseRepository):
    """Repository for PlayerSession model"""
    
    def get_by_id(self, session_id: int) -> Optional[PlayerSession]:
        return self.db.query(PlayerSession).filter(PlayerSession.id == session_id).first()
    
    def get_all_by_player(self, player_id: int) -> List[PlayerSession]:
        return self.db.query(PlayerSession).filter(PlayerSession.player_id == player_id).all()
    
    def get_active_session(self, player_id: int) -> Optional[PlayerSession]:
        """Get active session (no logout_time)"""
        return self.db.query(PlayerSession).filter(
            PlayerSession.player_id == player_id,
            PlayerSession.logout_time.is_(None)
        ).first()
    
    def create(self, player_id: int, login_time: datetime) -> PlayerSession:
        """Create new session"""
        session = PlayerSession(
            player_id=player_id,
            login_time=login_time
        )
        self.db.add(session)
        self.commit()
        self.refresh(session)
        logger.info(f"Created session for player: {player_id}")
        return session
    
    def logout(self, session_id: int, logout_time: datetime) -> Optional[PlayerSession]:
        """End session with logout time"""
        session = self.get_by_id(session_id)
        if not session:
            return None
        
        session.logout_time = logout_time
        # Calculate session duration in seconds
        session.session_duration = int((logout_time - session.login_time).total_seconds())
        self.commit()
        self.refresh(session)
        logger.info(f"Closed session: {session_id}")
        return session
    
    def update_ping(self, session_id: int, avg_ping: int) -> Optional[PlayerSession]:
        """Update average ping for session"""
        session = self.get_by_id(session_id)
        if not session:
            return None
        
        session.avg_ping = avg_ping
        self.commit()
        self.refresh(session)
        return session
