from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Tuple
from models import (
    Kit, PlayerData, PlayerStats, PlayerKill, PlayerRoleAction,
    PlayerIP, PlayerName, PlayerRoleStat, PlayerSession, PlayerLabel,
    PlayerNote, PlayerPunishment, PlayerAnomaly
)
from fastapi import HTTPException
import logging
from datetime import datetime, timedelta
from utils import derive_subnet

logger = logging.getLogger(__name__)


def _apply_discord_link_filter(query, discord_link: Optional[str]):
    if discord_link == "linked":
        return query.filter(PlayerData.discord_id.isnot(None), PlayerData.discord_id != "")
    if discord_link == "unlinked":
        return query.filter((PlayerData.discord_id.is_(None)) | (PlayerData.discord_id == ""))
    return query


def _player_exists(model, condition=None):
    clauses = [model.player_id == PlayerData.player_id]
    if condition is not None:
        clauses.append(condition)
    return exists().where(*clauses)


def _active_punishment_exists():
    now = datetime.utcnow()
    return exists().where(
        PlayerPunishment.player_id == PlayerData.player_id,
        PlayerPunishment.is_active == 1,
        or_(PlayerPunishment.expires_at.is_(None), PlayerPunishment.expires_at >= now),
    )


def _open_anomaly_exists():
    return exists().where(
        or_(
            PlayerAnomaly.player_id == PlayerData.player_id,
            PlayerAnomaly.related_player_id == PlayerData.player_id,
        ),
        PlayerAnomaly.status != "dismissed",
    )


def _ip_history_exists():
    return select(PlayerIP.player_id).where(
        PlayerIP.player_id == PlayerData.player_id,
        _usable_ip_sql(PlayerIP.ip),
    ).group_by(PlayerIP.player_id).having(func.count(PlayerIP.id) >= 2).exists()


def _usable_ip_sql(column):
    """Cheap SQL prefilter for legacy non-global IPv4 rows.

    New writes are validated by ``is_bogon_ip``. This predicate keeps the
    common container/LAN/reserved ranges out of list facets and joins; every
    user-facing serializer still applies the complete Python check.
    """
    excluded = [
        "0.%", "10.%", "127.%", "169.254.%", "192.0.2.%", "192.168.%",
        "198.18.%", "198.19.%", "198.51.100.%", "203.0.113.%", "100.64.%",
        "100.65.%", "100.66.%", "100.67.%", "100.68.%", "100.69.%",
        "100.70.%", "100.71.%", "100.72.%", "100.73.%", "100.74.%",
        "100.75.%", "100.76.%", "100.77.%", "100.78.%", "100.79.%",
        "100.8%.%", "100.9%.%", "100.10%.%", "100.11%.%", "100.12%.%",
        "100.127.%",
    ] + [f"172.{value}.%" for value in range(16, 32)] + [f"{value}.%" for value in range(224, 240)]
    return not_(or_(*(column.like(pattern) for pattern in excluded)))


def _apply_player_facets(
    query,
    *,
    label_mode: Optional[str] = None,
    label_ids: Optional[List[int]] = None,
    notes: Optional[str] = None,
    sanctions: Optional[str] = None,
    anomalies: Optional[str] = None,
    name_history: Optional[str] = None,
    ip_history: Optional[str] = None,
    activity: Optional[str] = None,
):
    labels = exists().where(PlayerLabel.player_id == PlayerData.player_id)
    if label_mode == "with":
        query = query.filter(labels)
    elif label_mode == "without":
        query = query.filter(~labels)
    if label_ids:
        query = query.filter(exists().where(
            PlayerLabel.player_id == PlayerData.player_id,
            PlayerLabel.category_id.in_(label_ids),
        ))

    facet_exists = {
        "notes": _player_exists(PlayerNote, PlayerNote.content != ""),
        "sanctions": _active_punishment_exists(),
        "anomalies": _open_anomaly_exists(),
        "name_history": _player_exists(PlayerName, PlayerName.nickname != PlayerData.player_last_time_name),
        "ip_history": _ip_history_exists(),
    }
    for value, mode in (
        (facet_exists["notes"], notes),
        (facet_exists["sanctions"], sanctions),
        (facet_exists["anomalies"], anomalies),
        (facet_exists["name_history"], name_history),
        (facet_exists["ip_history"], ip_history),
    ):
        if mode == "with":
            query = query.filter(value)
        elif mode == "without":
            query = query.filter(~value)

    if activity == "new":
        query = query.filter(PlayerData.play_time < 7200)
    elif activity and activity.startswith("inactive_"):
        try:
            days = int(activity.split("_", 1)[1])
        except (TypeError, ValueError):
            days = 0
        if days > 0:
            query = query.filter(PlayerData.last_login_time <= datetime.utcnow() - timedelta(days=days))
    return query


def apply_player_sort(query, sort: Optional[str]):
    sort_spec = PlayerRepository.SORT_COLUMNS.get(sort or "")
    if sort_spec:
        column, descending = sort_spec
        return query.order_by(column.desc() if descending else column.asc())
    expressions = {
        "active_sanctions_desc": _active_punishment_exists(),
        "active_sanctions_asc": _active_punishment_exists(),
        "open_anomalies_desc": _open_anomaly_exists(),
        "open_anomalies_asc": _open_anomaly_exists(),
        "discord_link_desc": and_(PlayerData.discord_id.isnot(None), PlayerData.discord_id != ""),
        "discord_link_asc": and_(PlayerData.discord_id.isnot(None), PlayerData.discord_id != ""),
    }
    expression = expressions.get(sort or "")
    if expression is None:
        return query
    descending = sort.endswith("_desc")
    return query.order_by(expression.desc() if descending else expression.asc())


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
    
    SORT_COLUMNS = {
        "playtime_desc": (PlayerData.play_time, True),
        "playtime_asc": (PlayerData.play_time, False),
        "last_login_desc": (PlayerData.last_login_time, True),
        "last_login_asc": (PlayerData.last_login_time, False),
        "last_activity_desc": (PlayerData.last_login_time, True),
        "last_activity_asc": (PlayerData.last_login_time, False),
    }

    def get_all(
        self, page: int = 1, page_size: int = 50,
        rank_id: Optional[int] = None, sort: Optional[str] = None,
        discord_link: Optional[str] = None,
        label_mode: Optional[str] = None, label_ids: Optional[List[int]] = None,
        notes: Optional[str] = None, sanctions: Optional[str] = None,
        anomalies: Optional[str] = None, name_history: Optional[str] = None,
        ip_history: Optional[str] = None,
        activity: Optional[str] = None,
    ) -> Tuple[List[PlayerData], int]:
        """Get all players with pagination, optional rank filter and sort order."""
        query = self.db.query(PlayerData)
        if rank_id is not None:
            query = query.filter(PlayerData.rank_id == rank_id)
        query = _apply_discord_link_filter(query, discord_link)
        query = _apply_player_facets(
            query, label_mode=label_mode, label_ids=label_ids, notes=notes,
            sanctions=sanctions, anomalies=anomalies, name_history=name_history,
            ip_history=ip_history,
            activity=activity,
        )
        query = apply_player_sort(query, sort)

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
    
    def search(
        self, query: str, limit: int = 10, discord_link: Optional[str] = None,
        label_mode: Optional[str] = None, label_ids: Optional[List[int]] = None,
        notes: Optional[str] = None, sanctions: Optional[str] = None,
        anomalies: Optional[str] = None, name_history: Optional[str] = None,
        ip_history: Optional[str] = None,
        activity: Optional[str] = None,
    ) -> List[PlayerData]:
        """
        Search players by:
        - Current nickname (player_last_time_name)
        - SteamID (all variants)
        - Previous nicknames (PlayerName table)
        - IP addresses (PlayerIP table)
        """
        from sqlalchemy import or_, func, distinct
        
        query_lower = f"%{query.lower()}%"
        
        # Search by current nickname, steamid, Discord ID
        direct_search_query = self.db.query(PlayerData).filter(
            or_(
                PlayerData.player_last_time_name.ilike(query_lower),
                PlayerData.player_steamid.ilike(query_lower),
                PlayerData.discord_id.ilike(query_lower),
            )
        )
        direct_search_query = _apply_discord_link_filter(direct_search_query, discord_link)
        direct_search_query = _apply_player_facets(
            direct_search_query, label_mode=label_mode, label_ids=label_ids, notes=notes,
            sanctions=sanctions, anomalies=anomalies, name_history=name_history,
            ip_history=ip_history,
            activity=activity,
        )
        direct_search = direct_search_query.limit(limit).all()
        
        if len(direct_search) >= limit:
            return direct_search[:limit]
        
        # If not enough results, search by previous nicknames
        player_ids = set(p.player_id for p in direct_search)
        remaining_limit = limit - len(direct_search)
        
        if remaining_limit > 0:
            from models import PlayerName
            nick_q = self.db.query(PlayerData).join(
                PlayerName, PlayerData.player_id == PlayerName.player_id
            ).filter(PlayerName.nickname.ilike(query_lower))
            nick_q = _apply_discord_link_filter(nick_q, discord_link)
            nick_q = _apply_player_facets(
                nick_q, label_mode=label_mode, label_ids=label_ids, notes=notes,
                sanctions=sanctions, anomalies=anomalies, name_history=name_history,
                ip_history=ip_history,
                activity=activity,
            )
            if player_ids:
                nick_q = nick_q.filter(PlayerData.player_id.notin_(player_ids))
            nickname_results = nick_q.distinct().limit(remaining_limit).all()
            
            direct_search.extend(nickname_results)
            player_ids.update(p.player_id for p in nickname_results)
            remaining_limit = limit - len(direct_search)
        
        # If still not enough, search by IP addresses
        if remaining_limit > 0:
            from models import PlayerIP
            ip_q = self.db.query(PlayerData).join(
                PlayerIP, PlayerData.player_id == PlayerIP.player_id
            ).filter(
                _usable_ip_sql(PlayerIP.ip),
                or_(
                    PlayerIP.ip.ilike(query_lower),
                    PlayerIP.subnet.ilike(query_lower),
                )
            )
            ip_q = _apply_discord_link_filter(ip_q, discord_link)
            ip_q = _apply_player_facets(
                ip_q, label_mode=label_mode, label_ids=label_ids, notes=notes,
                sanctions=sanctions, anomalies=anomalies, name_history=name_history,
                ip_history=ip_history,
                activity=activity,
            )
            if player_ids:
                ip_q = ip_q.filter(PlayerData.player_id.notin_(player_ids))
            ip_results = ip_q.distinct().limit(remaining_limit).all()
            
            direct_search.extend(ip_results)
        
        return direct_search[:limit]

    def search_paginated(
        self, query: str, page: int = 1, page_size: int = 50,
        rank_id: Optional[int] = None, sort: Optional[str] = None,
        discord_link: Optional[str] = None,
        label_mode: Optional[str] = None, label_ids: Optional[List[int]] = None,
        notes: Optional[str] = None, sanctions: Optional[str] = None,
        anomalies: Optional[str] = None, name_history: Optional[str] = None,
        ip_history: Optional[str] = None,
        activity: Optional[str] = None,
    ) -> Tuple[List[PlayerData], int]:
        """Search all supported identifiers before applying pagination."""
        query_lower = f"%{query.lower()}%"
        previous_name = exists().where(
            PlayerName.player_id == PlayerData.player_id,
            PlayerName.nickname.ilike(query_lower),
        )
        ip_match = exists().where(
            PlayerIP.player_id == PlayerData.player_id,
            _usable_ip_sql(PlayerIP.ip),
            or_(PlayerIP.ip.ilike(query_lower), PlayerIP.subnet.ilike(query_lower)),
        )
        search_query = self.db.query(PlayerData).filter(or_(
            PlayerData.player_last_time_name.ilike(query_lower),
            PlayerData.player_steamid.ilike(query_lower),
            PlayerData.discord_id.ilike(query_lower),
            previous_name,
            ip_match,
        ))
        if rank_id is not None:
            search_query = search_query.filter(PlayerData.rank_id == rank_id)
        search_query = _apply_discord_link_filter(search_query, discord_link)
        search_query = _apply_player_facets(
            search_query, label_mode=label_mode, label_ids=label_ids, notes=notes,
            sanctions=sanctions, anomalies=anomalies, name_history=name_history,
            ip_history=ip_history,
            activity=activity,
        )
        search_query = apply_player_sort(search_query, sort)
        total = search_query.count()
        players = search_query.offset((page - 1) * page_size).limit(page_size).all()
        return players, total

    def get_player_flags(self, player_ids: List[int], visible_label_ids: Optional[set[int]] = None) -> dict[int, dict[str, object]]:
        """Bulk-load safe moderation flags without exposing note or punishment contents."""
        if not player_ids:
            return {}
        flags = {
            player_id: {
                "label_ids": [],
                "has_labels": False,
                "has_notes": False,
                "has_active_punishments": False,
                "has_open_anomalies": False,
                "has_previous_names": False,
                "has_ip_history": False,
            }
            for player_id in player_ids
        }
        for label in self.db.query(PlayerLabel).filter(PlayerLabel.player_id.in_(player_ids)).all():
            if visible_label_ids is not None and label.category_id not in visible_label_ids:
                continue
            label_ids = flags[label.player_id]["label_ids"]
            if label.category_id not in label_ids:
                label_ids.append(label.category_id)
            flags[label.player_id]["has_labels"] = True
        for player_id, in self.db.query(PlayerNote.player_id).filter(PlayerNote.player_id.in_(player_ids), PlayerNote.content != "").distinct():
            flags[player_id]["has_notes"] = True
        for player_id, in self.db.query(PlayerName.player_id).join(
            PlayerData, PlayerData.player_id == PlayerName.player_id
        ).filter(
            PlayerName.player_id.in_(player_ids),
            PlayerName.nickname != PlayerData.player_last_time_name,
        ).distinct():
            flags[player_id]["has_previous_names"] = True
        for player_id, in self.db.query(PlayerIP.player_id).filter(
            PlayerIP.player_id.in_(player_ids),
            _usable_ip_sql(PlayerIP.ip),
        ).group_by(PlayerIP.player_id).having(func.count(PlayerIP.id) >= 2):
            flags[player_id]["has_ip_history"] = True
        for player_id, in self.db.query(PlayerPunishment.player_id).filter(
            PlayerPunishment.player_id.in_(player_ids),
            PlayerPunishment.is_active == 1,
            or_(PlayerPunishment.expires_at.is_(None), PlayerPunishment.expires_at >= datetime.utcnow()),
        ).distinct():
            flags[player_id]["has_active_punishments"] = True
        for player_id, in self.db.query(PlayerAnomaly.player_id).filter(
            PlayerAnomaly.player_id.in_(player_ids), PlayerAnomaly.status != "dismissed",
        ).distinct():
            flags[player_id]["has_open_anomalies"] = True
        for player_id, in self.db.query(PlayerAnomaly.related_player_id).filter(
            PlayerAnomaly.related_player_id.in_(player_ids), PlayerAnomaly.status != "dismissed",
        ).distinct():
            if player_id in flags:
                flags[player_id]["has_open_anomalies"] = True
        return flags


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
        """Accumulate stats by adding delta values atomically (avoids lost updates)."""
        stats = self.get_by_player_id(player_id)
        if not stats:
            return None

        increments = {}
        for key, value in delta.items():
            if value is not None and value != 0:
                col = getattr(PlayerStats, key, None)
                if col is not None:
                    increments[col] = col + value

        if increments:
            self.db.query(PlayerStats).filter(
                PlayerStats.player_id == player_id
            ).update(increments, synchronize_session=False)
            self.commit()
            self.db.refresh(stats)
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
        """Add delta to kill count atomically (avoids lost updates)."""
        existing = self.get_by_player_and_monster(player_id, monster_type)
        if existing:
            # Atomic in-DB increment instead of read-modify-write
            self.db.query(PlayerKill).filter(
                PlayerKill.id == existing.id
            ).update({PlayerKill.kills_count: PlayerKill.kills_count + delta},
                     synchronize_session=False)
            self.commit()
            self.db.refresh(existing)
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

        if not subnet:
            # player_ips.subnet is NOT NULL — derive instead of letting the
            # DB raise a raw 1048 (same failure as the players_data triggers).
            subnet = derive_subnet(ip)
        if not subnet:
            raise HTTPException(status_code=422, detail="Subnet is required")
        
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
        return (
            self.db.query(PlayerName)
            .filter(PlayerName.player_id == player_id)
            .order_by(PlayerName.first_seen.desc(), PlayerName.id.desc())
            .all()
        )
    
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
        """Accumulate role stats atomically (avoids lost updates)."""
        existing = self.get_by_player_and_role(player_id, role)
        if existing:
            self.db.query(PlayerRoleStat).filter(
                PlayerRoleStat.id == existing.id
            ).update({
                PlayerRoleStat.rounds_played: PlayerRoleStat.rounds_played + rounds_delta,
                PlayerRoleStat.mvp_score: PlayerRoleStat.mvp_score + mvp_delta,
            }, synchronize_session=False)
            self.commit()
            self.db.refresh(existing)
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
        return (
            self.db.query(PlayerSession)
            .filter(PlayerSession.player_id == player_id)
            .order_by(PlayerSession.login_time.desc())
            .all()
        )
    
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
        # Calculate session duration in seconds (clamp to avoid negatives on clock skew)
        session.session_duration = max(0, int((logout_time - session.login_time).total_seconds()))
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
