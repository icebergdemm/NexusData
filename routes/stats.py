from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from services import PlayerStatsService, PlayerRoleStatService
from schemas import PlayerStatsOut, PlayerStatsCreate, PlayerRoleStatOut
from security import verify_api_key_dependency
from config import get_settings
from typing import List

settings = get_settings()

router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    dependencies=[Depends(verify_api_key_dependency)] if settings.require_auth else []
)


@router.get("/{player_id}", response_model=PlayerStatsOut)
def get_player_stats(player_id: int, db: Session = Depends(get_db)):
    """Get main stats for player by player ID"""
    service = PlayerStatsService(db)
    return service.get_stats(player_id)


@router.post("/{player_id}", response_model=PlayerStatsOut)
def update_player_stats(
    player_id: int,
    stats: PlayerStatsCreate,
    db: Session = Depends(get_db),
):
    """Create or accumulate stats for player (delta values)"""
    service = PlayerStatsService(db)
    return service.upsert_stats(player_id, stats)


# Alternative: Use Event Dispatcher for consistency
# @router.post("/{player_id}", response_model=dict)
# def update_player_stats_via_dispatcher(
#     player_id: int,
#     stats: PlayerStatsCreate,
#     db: Session = Depends(get_db),
# ):
#     """Alternative: Use POST /api/v1/events with type='stats.upsert'"""
#     from services import EventDispatcherService
#     from schemas import MessageEnvelope
#     
#     payload_dict = stats.dict(exclude_unset=True)
#     payload_dict["player_id"] = player_id
#     
#     message = MessageEnvelope(
#         type="stats.upsert",
#         version=1,
#         source="api",
#         payload=payload_dict
#     )
#     
#     dispatcher = EventDispatcherService(db)
#     return dispatcher.process(message)


@router.get("/{player_id}/role-stats", response_model=List[PlayerRoleStatOut])
def get_player_role_stats(
    player_id: int,
    db: Session = Depends(get_db)
):
    """Get role-specific statistics for player"""
    service = PlayerRoleStatService(db)
    return service.get_player_role_stats(player_id)


