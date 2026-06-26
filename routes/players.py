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

    # Batch-fetch matching previous-names and IPs for ALL result players at once
    # (avoids N+1: was 2 queries per result row).
    player_ids = [p.player_id for p in results]
    query_lower = q.lower()
    prev_names_by_pid = {}
    ips_by_pid = {}
    if player_ids:
        for pn in db.query(PlayerName).filter(
            PlayerName.player_id.in_(player_ids),
            PlayerName.nickname.ilike(f"%{q}%"),
        ).all():
            prev_names_by_pid.setdefault(pn.player_id, []).append(pn.nickname)
        for ip in db.query(PlayerIP).filter(
            PlayerIP.player_id.in_(player_ids),
            or_(PlayerIP.ip.ilike(f"%{q}%"), PlayerIP.subnet.ilike(f"%{q}%")),
        ).all():
            ips_by_pid.setdefault(ip.player_id, []).append(ip.ip)

    enriched_results = []
    for player in results:
        search_context = []
        name = player.player_last_time_name or ""
        steamid = player.player_steamid or ""
        if query_lower in name.lower():
            search_context.append("nickname")
        if query_lower in steamid.lower():
            search_context.append("steamid")

        prev_names = prev_names_by_pid.get(player.player_id, [])
        if prev_names:
            search_context.append("previous_nickname")
        ips = ips_by_pid.get(player.player_id, [])
        if ips:
            search_context.append("ip_address")

        enriched_results.append({
            "id": player.player_id,
            "name": name,
            "steamid": steamid,
            "play_time_hours": (player.play_time or 0) / 3600,
            "rank_id": player.rank_id,
            "kit_level": player.kit_level,
            "match_context": search_context or ["name_or_steamid"],
            "previous_names": prev_names,
            "ips": ips,
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

