"""
Rating Engine: computes per-role ratings from raw player_role_actions data.
Runs as a background worker every 60 seconds, recalculating all ratings from scratch.
"""

import math
import logging
import threading
import time
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal
from models import PlayerRating, PlayerRoleAction, PlayerData
from rating_constants import K, S, WEIGHT_MATRIX, ROLES

logger = logging.getLogger(__name__)


def compute_rating(cumulative_points: float) -> float:
    """Compute rating from cumulative points using logarithmic formula."""
    if cumulative_points <= 0:
        return 0.0
    return K * math.log(1 + cumulative_points / S)


def calculate_all_ratings(db: Session) -> Dict[str, Dict[str, dict]]:
    """
    Calculate ratings for ALL players from raw player_role_actions table.
    Uses batched loading to avoid loading all rows into memory at once.
    Returns: {steam_id: {role: {"points": float, "rating": float}}}
    """
    # Get player_id -> steam_id mapping first (lightweight)
    players = db.query(PlayerData.player_id, PlayerData.player_steamid).all()
    if not players:
        return {}
    pid_to_steam = {p.player_id: p.player_steamid for p in players}

    # Aggregate weighted points per (steam_id, role) using batched iteration
    points_by_player_role = defaultdict(lambda: defaultdict(float))

    BATCH_SIZE = 5000
    offset = 0
    while True:
        batch = db.query(PlayerRoleAction).offset(offset).limit(BATCH_SIZE).all()
        if not batch:
            break

        for action in batch:
            steam_id = pid_to_steam.get(action.player_id)
            if not steam_id:
                continue

            role = action.role
            if role not in WEIGHT_MATRIX:
                continue

            action_type = action.action_type
            value = float(action.value or 0)
            weight = WEIGHT_MATRIX.get(role, {}).get(action_type, 0.0)
            points_by_player_role[steam_id][role] += value * weight

        # Allow GC to reclaim the batch
        offset += BATCH_SIZE
        db.expire_all()

    # Compute ratings
    result = {}
    for steam_id, roles_dict in points_by_player_role.items():
        result[steam_id] = {}
        for role, points in roles_dict.items():
            if points > 0:
                result[steam_id][role] = {
                    "points": round(points, 2),
                    "rating": round(compute_rating(points), 2),
                }

    return result


def sync_ratings_to_db(db: Session, ratings: Dict[str, Dict[str, dict]]):
    """Write calculated ratings to player_ratings table."""
    now = datetime.utcnow()

    for steam_id, roles_dict in ratings.items():
        for role, data in roles_dict.items():
            record = db.query(PlayerRating).filter(
                PlayerRating.steam_id == steam_id,
                PlayerRating.role == role,
            ).first()

            if record:
                record.cumulative_points = data["points"]
                record.current_rating = data["rating"]
                record.last_activity_date = now
                record.updated_at = now
            else:
                record = PlayerRating(
                    steam_id=steam_id,
                    role=role,
                    cumulative_points=data["points"],
                    current_rating=data["rating"],
                    last_activity_date=now,
                    last_decay_applied_at=now,
                )
                db.add(record)

    db.commit()
    logger.info(f"[Rating] Synced ratings for {len(ratings)} players")


def recalculate_all():
    """Full recalculation from raw data. Called by background worker."""
    db = SessionLocal()
    try:
        ratings = calculate_all_ratings(db)
        if ratings:
            sync_ratings_to_db(db, ratings)
    except Exception as e:
        logger.error(f"[Rating] Recalculation failed: {e}")
        db.rollback()
    finally:
        db.expire_all()
        db.close()
        del db  # Help GC


def get_player_ratings(db: Session, steam_id: str) -> Dict[str, dict]:
    """Get all ratings for a player from DB."""
    result = {}
    for role in ROLES:
        record = db.query(PlayerRating).filter(
            PlayerRating.steam_id == steam_id,
            PlayerRating.role == role,
        ).first()

        if record is None:
            result[role] = {"rating": 0.0, "cumulative_points": 0.0, "last_activity_date": None}
        else:
            result[role] = {
                "rating": round(record.current_rating, 2),
                "cumulative_points": round(record.cumulative_points, 2),
                "last_activity_date": record.last_activity_date.isoformat() if record.last_activity_date else None,
            }

    return result


def generate_snapshot_from_raw(db: Session, date_from: Optional[datetime] = None,
                                date_to: Optional[datetime] = None) -> Dict[str, Dict[str, dict]]:
    """
    Generate rating snapshot from raw player_role_actions, optionally filtered by date.
    Note: player_role_actions doesn't have timestamps, so date filtering uses player_sessions.
    If no dates — uses all data (same as calculate_all_ratings).
    """
    # For now, calculate from all raw data (player_role_actions has no timestamp)
    # Date filtering would require joining with sessions — complex, skip for MVP
    return calculate_all_ratings(db)


# ══════════════════════════════════════════════════════════════════════════════
# Background Worker
# ══════════════════════════════════════════════════════════════════════════════

_worker_thread: Optional[threading.Thread] = None
_worker_running = False


def _rating_worker_loop():
    """Background thread: recalculate ratings every 5 minutes (was 60s — too aggressive)."""
    global _worker_running
    logger.info("[Rating] Background worker started (interval: 300s)")
    while _worker_running:
        try:
            recalculate_all()
        except Exception as e:
            logger.error(f"[Rating] Worker error: {e}")
        time.sleep(300)  # 5 minutes — ratings don't need real-time updates
    logger.info("[Rating] Background worker stopped")


def start_rating_worker():
    """Start the background rating recalculation worker."""
    global _worker_thread, _worker_running
    if _worker_thread and _worker_thread.is_alive():
        return  # Already running

    _worker_running = True
    _worker_thread = threading.Thread(target=_rating_worker_loop, daemon=True)
    _worker_thread.start()


def stop_rating_worker():
    """Stop the background worker."""
    global _worker_running
    _worker_running = False
