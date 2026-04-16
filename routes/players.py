from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from services import (
    PlayerService, PlayerKillService, PlayerRoleActionService, 
    PlayerIPService, PlayerNameService, PlayerRoleStatService, PlayerSessionService
)
from schemas import (
    PlayerDataOut, PlayerDataCreate, PaginatedResponse, PaginationParams,
    PlayerKillOut, PlayerRoleActionOut, PlayerIPOut, PlayerNameOut,
    PlayerRoleStatOut, PlayerSessionOut
)
from security import verify_api_key_dependency
from config import get_settings
import math

settings = get_settings()

router = APIRouter(
    prefix="/players",
    tags=["players"]
)

auth_dependency = Depends(verify_api_key_dependency) if settings.require_auth else None


@router.get("", response_model=PaginatedResponse[PlayerDataOut], dependencies=[auth_dependency] if auth_dependency else [])
def get_all_players(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Get all players with pagination"""
    service = PlayerService(db)
    players, total = service.get_all_players(page, page_size)
    
    total_pages = math.ceil(total / page_size)
    has_next = page < total_pages
    has_prev = page > 1
    
    return {
        "items": players,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": has_next,
        "has_prev": has_prev,
    }


@router.get("/info", response_model=PlayerDataOut, dependencies=[auth_dependency] if auth_dependency else [])
def get_player_info(
    type: str = Query("steamid", description="steamid or name"),
    identifier: str = Query(..., description="Identifier value"),
    db: Session = Depends(get_db),
):
    """
    Get player by identifier (steamid or name)
    
    Query params:
    - type: 'steamid' or 'name'
    - identifier: the value to search for
    """
    service = PlayerService(db)
    return service.get_player_by_identifier(type, identifier)


@router.get("/search", dependencies=[auth_dependency] if auth_dependency else [])
def search_players(
    q: str = Query(..., min_length=1, max_length=50),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """
    Search players by:
    - Current nickname (player_last_time_name)
    - SteamID
    - Previous nicknames
    - IP addresses
    
    Returns list of matching players with search context
    """
    service = PlayerService(db)
    from models import PlayerName, PlayerIP
    from sqlalchemy import or_
    
    results = service.search_players(q, limit)
    
    # Enrich results with search context
    enriched_results = []
    for player in results:
        search_context = []
        
        # Check what matched
        query_lower = q.lower()
        if query_lower in player.player_last_time_name.lower():
            search_context.append("nickname")
        if query_lower in player.player_steamid.lower():
            search_context.append("steamid")
        
        # Check if matched by previous nickname
        prev_names = db.query(PlayerName).filter(
            PlayerName.player_id == player.player_id,
            PlayerName.nickname.ilike(f"%{q}%")
        ).all()
        if prev_names:
            search_context.append("previous_nickname")
        
        # Check if matched by IP
        ips = db.query(PlayerIP).filter(
            PlayerIP.player_id == player.player_id,
            or_(
                PlayerIP.ip.ilike(f"%{q}%"),
                PlayerIP.subnet.ilike(f"%{q}%")
            )
        ).all()
        if ips:
            search_context.append("ip_address")
        
        enriched_results.append({
            "id": player.player_id,
            "name": player.player_last_time_name,
            "steamid": player.player_steamid,
            "play_time_hours": player.play_time / 3600,
            "rank_id": player.rank_id,
            "kit_level": player.kit_level,
            "match_context": search_context or ["name_or_steamid"],
            "previous_names": [name.nickname for name in prev_names] if prev_names else [],
            "ips": [ip.ip for ip in ips] if ips else []
        })
    
    return enriched_results


@router.post("", response_model=PlayerDataOut, dependencies=[auth_dependency] if auth_dependency else [])
def create_or_update_player(
    player: PlayerDataCreate,
    db: Session = Depends(get_db),
):
    """Create new player or update existing by steamid"""
    service = PlayerService(db)
    return service.upsert_player(player)


# Player kills endpoints
@router.get("/{player_id}/kills", response_model=List[PlayerKillOut], dependencies=[auth_dependency] if auth_dependency else [])
def get_player_kills(
    player_id: int,
    db: Session = Depends(get_db)
):
    """Get all kills by monster type for player"""
    service = PlayerKillService(db)
    return service.get_player_kills(player_id)


# Player role actions endpoints
@router.get("/{player_id}/role-actions", response_model=List[PlayerRoleActionOut], dependencies=[auth_dependency] if auth_dependency else [])
def get_player_role_actions(
    player_id: int,
    role: str = Query(None, description="Filter by role"),
    db: Session = Depends(get_db)
):
    """Get role actions for player (optionally filtered by role)"""
    service = PlayerRoleActionService(db)
    actions = service.get_player_actions(player_id)
    if role:
        actions = [a for a in actions if a.role == role]
    return actions


# Player IPs endpoints
@router.get("/{player_id}/ips", response_model=List[PlayerIPOut], dependencies=[auth_dependency] if auth_dependency else [])
def get_player_ips(
    player_id: int,
    db: Session = Depends(get_db)
):
    """Get IP address history for player"""
    service = PlayerIPService(db)
    return service.get_player_ips(player_id)


# Player names endpoints
@router.get("/{player_id}/names", response_model=List[PlayerNameOut], dependencies=[auth_dependency] if auth_dependency else [])
def get_player_names(
    player_id: int,
    db: Session = Depends(get_db)
):
    """Get nickname history for player"""
    service = PlayerNameService(db)
    return service.get_player_names(player_id)


# Player role stats endpoints
@router.get("/{player_id}/role-stats", response_model=List[PlayerRoleStatOut], dependencies=[auth_dependency] if auth_dependency else [])
def get_player_role_stats(
    player_id: int,
    role: str = Query(None, description="Filter by role"),
    db: Session = Depends(get_db)
):
    """Get role statistics for player (optionally filtered by role)"""
    service = PlayerRoleStatService(db)
    stats = service.get_player_role_stats(player_id)
    if role:
        stats = [s for s in stats if s.role == role]
    return stats


# Player sessions endpoints
@router.get("/{player_id}/sessions", response_model=List[PlayerSessionOut], dependencies=[auth_dependency] if auth_dependency else [])
def get_player_sessions(
    player_id: int,
    db: Session = Depends(get_db)
):
    """Get all sessions for player"""
    service = PlayerSessionService(db)
    return service.get_player_sessions(player_id)

