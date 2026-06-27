"""
Rating Engine v3 — power-compressed rating with persistent daily erosion.

Two-layer model:
  1. RAW rating  = compute_rating(weighted_points)   — from player_role_actions
  2. FINAL rating = raw_rating * decay_factor          — what players see

The decay_factor (0.30–1.0) is PERSISTENT: it survives full recalculation
(which rebuilds raw points from scratch). This lets us erode ratings over time
without the recalc wiping the erosion.

Erosion rules (applied once per day by the worker):
  - Everyone:   -DAILY_EROSION per day (baseline, keeps leaderboard "alive")
  - Inactive:   additional -INACTIVE_EXTRA_EROSION per day past threshold
  - Active play resets decay_factor back toward 1.0 (recovery)
  - Floor at DECAY_FACTOR_FLOOR so veterans aren't erased
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, Optional

from sqlalchemy.orm import Session

from database import SessionLocal
from models import PlayerRating, PlayerRoleAction, PlayerRoleStat, PlayerData
from rating_constants import (
    WEIGHT_MATRIX, ROLES, compute_rating,
    DECAY_THRESHOLD_DAYS, DAILY_EROSION, INACTIVE_EXTRA_EROSION, DECAY_FACTOR_FLOOR,
)

# Re-export compute_rating so `from rating_engine import compute_rating` works
__all__ = ["compute_rating", "calculate_all_ratings", "sync_ratings_to_db",
           "apply_daily_erosion", "recalculate_all", "get_or_create_rating",
           "insert_history", "get_player_ratings", "start_rating_worker", "stop_rating_worker"]

logger = logging.getLogger(__name__)


def calculate_all_ratings(db: Session) -> Dict[str, Dict[str, dict]]:
    """
    Calculate RAW ratings for all players from player_role_actions.
    Returns: {steam_id: {role: {"points": float, "rating": float, "rounds": int}}}
    Note: this is the raw rating BEFORE decay_factor is applied.
    """
    players = db.query(PlayerData.player_id, PlayerData.player_steamid).all()
    if not players:
        return {}
    pid_to_steam = {p.player_id: p.player_steamid for p in players}

    points: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

    BATCH_SIZE = 5000
    last_id = 0
    while True:
        batch = (
            db.query(PlayerRoleAction)
            .filter(PlayerRoleAction.id > last_id)
            .order_by(PlayerRoleAction.id)
            .limit(BATCH_SIZE)
            .all()
        )
        if not batch:
            break
        for action in batch:
            steam_id = pid_to_steam.get(action.player_id)
            if not steam_id:
                continue
            role = action.role
            if role not in WEIGHT_MATRIX:
                continue
            weight = WEIGHT_MATRIX[role].get(action.action_type, 0.0)
            if weight:
                points[steam_id][role] += float(action.value or 1) * weight
        last_id = batch[-1].id
        db.expire_all()

    # Rounds played (display only)
    rounds_by_player_role: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for rs in db.query(PlayerRoleStat).all():
        steam_id = pid_to_steam.get(rs.player_id)
        if steam_id and rs.role in ROLES:
            rounds_by_player_role[steam_id][rs.role] = int(rs.rounds_played or 0)

    result: Dict[str, Dict[str, dict]] = {}
    for steam_id, roles_dict in points.items():
        result[steam_id] = {}
        for role, total_pts in roles_dict.items():
            # total_pts can be negative if penalties outweigh — clamp to 0
            rating = compute_rating(max(0.0, total_pts))
            if rating > 0:
                result[steam_id][role] = {
                    "points": round(max(0.0, total_pts), 2),
                    "rating": rating,
                    "rounds": rounds_by_player_role.get(steam_id, {}).get(role, 0),
                }

    return result


def sync_ratings_to_db(db: Session, ratings: Dict[str, Dict[str, dict]]):
    """
    Write calculated RAW ratings to player_ratings, then apply persistent decay_factor.

    - cumulative_points / raw rating come from the fresh computation
    - decay_factor is PRESERVED from the existing row (not reset)
    - current_rating = raw_rating * decay_factor
    - players present in the new computation get last_activity_date = now
      (this drives recovery of decay_factor in the daily erosion pass)
    """
    now = datetime.utcnow()

    existing_rows = db.query(PlayerRating).all()
    existing = {(r.steam_id, r.role): r for r in existing_rows}

    computed_keys = set()
    new_objects = []

    for steam_id, roles_dict in ratings.items():
        for role, data in roles_dict.items():
            key = (steam_id, role)
            computed_keys.add(key)
            raw_rating = data["rating"]
            record = existing.get(key)
            if record:
                factor = record.decay_factor if record.decay_factor is not None else 1.0
                record.cumulative_points = data["points"]
                record.current_rating = round(raw_rating * factor, 2)
                record.last_activity_date = now
                record.updated_at = now
            else:
                new_objects.append(PlayerRating(
                    steam_id=steam_id,
                    role=role,
                    cumulative_points=data["points"],
                    current_rating=raw_rating,  # factor = 1.0 for new
                    last_activity_date=now,
                    last_decay_applied_at=now,
                    decay_factor=1.0,
                    last_daily_decay_at=now,
                ))

    # Zero out dropped records (no longer have any points)
    for key, record in existing.items():
        if key not in computed_keys and record.current_rating != 0:
            record.cumulative_points = 0.0
            record.current_rating = 0.0
            record.updated_at = now

    if new_objects:
        db.bulk_save_objects(new_objects)

    db.commit()
    logger.info(f"[Rating] Synced {len(ratings)} players ({len(new_objects)} new)")


def apply_daily_erosion(db: Session):
    """
    Apply persistent daily erosion to decay_factor.

    Runs at most once per day per record (guarded by last_daily_decay_at).
    - baseline erosion for everyone
    - extra erosion for inactive players
    - recovery toward 1.0 for recently active players
    """
    now = datetime.utcnow()
    inactive_threshold = now - timedelta(days=DECAY_THRESHOLD_DAYS)

    records = db.query(PlayerRating).filter(PlayerRating.current_rating > 0).all()
    changed = 0

    for record in records:
        last = record.last_daily_decay_at or record.last_activity_date or now
        days_elapsed = (now - last).days
        if days_elapsed < 1:
            continue  # already eroded today

        factor = record.decay_factor if record.decay_factor is not None else 1.0
        is_inactive = (record.last_activity_date or now) < inactive_threshold

        for _ in range(days_elapsed):
            if is_inactive:
                factor *= (1.0 - DAILY_EROSION - INACTIVE_EXTRA_EROSION)
            else:
                # Active players: tiny baseline erosion, but slowly recover toward 1.0
                factor *= (1.0 - DAILY_EROSION)
                factor = min(1.0, factor + 0.005)  # small daily recovery for active

        factor = max(DECAY_FACTOR_FLOOR, min(1.0, factor))

        # Recompute final rating from raw (compute_rating on stored points) * factor
        raw_rating = compute_rating(record.cumulative_points)
        record.decay_factor = round(factor, 4)
        record.current_rating = round(raw_rating * factor, 2)
        record.last_daily_decay_at = now
        record.updated_at = now
        changed += 1

    if changed:
        db.commit()
        logger.info(f"[Rating] Daily erosion applied to {changed} records")


def recalculate_all():
    """Full recalculation + daily erosion. Called by background worker."""
    db = SessionLocal()
    try:
        ratings = calculate_all_ratings(db)
        if ratings:
            sync_ratings_to_db(db, ratings)
        apply_daily_erosion(db)
    except Exception as e:
        logger.error(f"[Rating] Recalculation failed: {e}")
        db.rollback()
    finally:
        db.expire_all()
        db.close()


def get_player_ratings(db: Session, steam_id: str) -> Dict[str, dict]:
    """Get all ratings for a player from DB."""
    result = {}
    for role in ROLES:
        record = db.query(PlayerRating).filter(
            PlayerRating.steam_id == steam_id,
            PlayerRating.role == role,
        ).first()
        if record is None:
            result[role] = {"rating": 0.0, "cumulative_points": 0.0, "last_activity_date": None, "rounds": 0}
        else:
            result[role] = {
                "rating": round(record.current_rating, 2),
                "cumulative_points": round(record.cumulative_points, 2),
                "last_activity_date": record.last_activity_date.isoformat() if record.last_activity_date else None,
            }
    return result


def generate_snapshot_from_raw(db: Session, date_from=None, date_to=None) -> Dict[str, Dict[str, dict]]:
    return calculate_all_ratings(db)


# ── Helpers used by admin manual-adjust routes ───────────────────────────────

def get_or_create_rating(db: Session, steam_id: str, role: str) -> PlayerRating:
    """Fetch a PlayerRating row, creating it if missing."""
    record = db.query(PlayerRating).filter(
        PlayerRating.steam_id == steam_id,
        PlayerRating.role == role,
    ).first()
    if record is None:
        now = datetime.utcnow()
        record = PlayerRating(
            steam_id=steam_id,
            role=role,
            cumulative_points=0.0,
            current_rating=0.0,
            last_activity_date=now,
            last_decay_applied_at=now,
            decay_factor=1.0,
            last_daily_decay_at=now,
        )
        db.add(record)
        db.flush()
    return record


def insert_history(db: Session, steam_id: str, role: str, event_type: str,
                   points_delta: float, points_before: float, points_after: float,
                   rating_before: float, rating_after: float,
                   source_server=None, trace_id=None):
    """Append a row to rating_history (audit log of rating changes)."""
    try:
        from models import RatingHistory
        db.add(RatingHistory(
            steam_id=steam_id,
            role=role,
            event_type=event_type,
            points_delta=points_delta,
            cumulative_points_before=points_before,
            cumulative_points_after=points_after,
            rating_before=rating_before,
            rating_after=rating_after,
            source_server=source_server,
            session_trace_id=trace_id,
        ))
    except Exception as e:
        logger.warning(f"[Rating] insert_history failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Background Worker
# ══════════════════════════════════════════════════════════════════════════════

_worker_thread: Optional[threading.Thread] = None
_worker_running = False


def _rating_worker_loop():
    global _worker_running
    logger.info("[Rating] Background worker started (interval: 300s)")
    while _worker_running:
        try:
            recalculate_all()
        except Exception as e:
            logger.error(f"[Rating] Worker error: {e}")
        time.sleep(300)
    logger.info("[Rating] Background worker stopped")


def start_rating_worker():
    global _worker_thread, _worker_running
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_running = True
    _worker_thread = threading.Thread(target=_rating_worker_loop, daemon=True)
    _worker_thread.start()


def stop_rating_worker():
    global _worker_running
    _worker_running = False
