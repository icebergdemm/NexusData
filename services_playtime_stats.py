"""
Playtime Statistics Worker.
Runs daily at 00:00 MSK to calculate play time per player per day/week/month.
Stores results in JSON files to avoid DB bloat.
Also supports reward group conditions based on playtime.
"""

import os
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PLAYTIME_STATS_DIR = "data/playtime_stats"
REWARD_GROUPS_FILE = "data/reward_groups.json"
PLAYTIME_CONDITIONS_FILE = "data/playtime_conditions.json"

MSK = timezone(timedelta(hours=3))


def _ensure_dirs():
    os.makedirs(PLAYTIME_STATS_DIR, exist_ok=True)
    os.makedirs("data", exist_ok=True)


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


def _today_msk() -> str:
    return _now_msk().strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════════════════
# Playtime stats calculation
# ══════════════════════════════════════════════════════════════════════════════

def calculate_playtime_stats():
    """Calculate playtime stats for all players: per day, week, month.
    Queries PlayerSession table and aggregates durations.
    Saves to data/playtime_stats/YYYY-MM-DD.json
    """
    from database import SessionLocal
    from models import PlayerSession, PlayerData
    from sqlalchemy import func

    _ensure_dirs()
    now = _now_msk()
    today = now.date()

    db = SessionLocal()
    try:
        # Define time boundaries
        day_start = datetime(today.year, today.month, today.day)
        week_start = day_start - timedelta(days=today.weekday())  # Monday
        month_start = datetime(today.year, today.month, 1)

        # Query all sessions that overlap with each period
        all_sessions = db.query(PlayerSession).filter(
            PlayerSession.login_time >= month_start - timedelta(hours=3)  # UTC offset
        ).all()

        # Build per-player stats
        player_stats: Dict[str, Dict] = {}

        for s in all_sessions:
            if not s.player_id or not s.session_duration:
                continue

            pid = str(s.player_id)
            if pid not in player_stats:
                # Get player info
                pd = db.query(PlayerData).filter(PlayerData.player_id == s.player_id).first()
                player_stats[pid] = {
                    "player_id": s.player_id,
                    "steamid": pd.player_steamid if pd else "",
                    "name": pd.player_last_time_name if pd else "",
                    "day": 0,
                    "week": 0,
                    "month": 0,
                }

            login_utc = s.login_time
            duration = s.session_duration or 0

            # Convert to MSK for period checking
            login_msk = login_utc + timedelta(hours=3) if login_utc else None
            if not login_msk:
                continue

            # Check which periods this session falls into
            if login_msk.date() == today:
                player_stats[pid]["day"] += duration
            if login_msk >= week_start:
                player_stats[pid]["week"] += duration
            if login_msk >= month_start:
                player_stats[pid]["month"] += duration

        # Save to file
        output = {
            "generated_at": now.isoformat(),
            "date": today.isoformat(),
            "week_start": week_start.isoformat(),
            "month_start": month_start.isoformat(),
            "players": player_stats,
        }

        filepath = os.path.join(PLAYTIME_STATS_DIR, f"{today.isoformat()}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)

        # Also save as "latest.json" for quick access
        latest_path = os.path.join(PLAYTIME_STATS_DIR, "latest.json")
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)

        logger.info(f"Playtime stats generated for {today}: {len(player_stats)} players")
        return output

    except Exception as e:
        logger.error(f"Failed to calculate playtime stats: {e}")
        return None
    finally:
        db.close()


def get_latest_playtime_stats() -> Optional[Dict]:
    """Load the latest playtime stats from disk."""
    _ensure_dirs()
    path = os.path.join(PLAYTIME_STATS_DIR, "latest.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Reward Groups (configurable groups of items/talents/stats)
# ══════════════════════════════════════════════════════════════════════════════

def load_reward_groups() -> List[Dict]:
    """Load configured reward groups from JSON."""
    _ensure_dirs()
    if os.path.exists(REWARD_GROUPS_FILE):
        try:
            with open(REWARD_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_reward_groups(groups: List[Dict]):
    """Save reward groups to JSON."""
    _ensure_dirs()
    with open(REWARD_GROUPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def load_playtime_conditions() -> List[Dict]:
    """Load playtime-based reward conditions from JSON."""
    _ensure_dirs()
    if os.path.exists(PLAYTIME_CONDITIONS_FILE):
        try:
            with open(PLAYTIME_CONDITIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_playtime_conditions(conditions: List[Dict]):
    """Save playtime conditions to JSON."""
    _ensure_dirs()
    with open(PLAYTIME_CONDITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(conditions, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# Rating reward conditions (top-N every N days)
# ══════════════════════════════════════════════════════════════════════════════

RATING_REWARDS_FILE = "data/rating_rewards.json"


def load_rating_rewards() -> List[Dict]:
    """Load rating-based reward conditions.
    Format: [{role, check_interval_days, places: [{place, reward_group_id}]}]
    """
    _ensure_dirs()
    if os.path.exists(RATING_REWARDS_FILE):
        try:
            with open(RATING_REWARDS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_rating_rewards(rewards: List[Dict]):
    """Save rating reward conditions."""
    _ensure_dirs()
    with open(RATING_REWARDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(rewards, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# Apply rewards to players
# ══════════════════════════════════════════════════════════════════════════════

def apply_reward_group_to_player(player_steamid: str, group_id: str):
    """Apply a reward group's items/stats/talents to a player's achievements."""
    from database import SessionLocal
    from models import PlayerData

    groups = load_reward_groups()
    group = next((g for g in groups if g.get("id") == group_id), None)
    if not group:
        logger.warning(f"Reward group {group_id} not found")
        return False

    db = SessionLocal()
    try:
        player = db.query(PlayerData).filter(PlayerData.player_steamid == player_steamid).first()
        if not player:
            logger.warning(f"Player {player_steamid} not found for reward")
            return False

        ach = player.achievements or {}
        if isinstance(ach, list):
            ach = {}

        # Apply items
        items = dict(ach.get("items", {}))
        for item_id, qty in (group.get("items") or {}).items():
            items[item_id] = items.get(item_id, 0) + qty
        ach["items"] = items

        # Apply stats
        stats = dict(ach.get("stats", {}))
        for key, val in (group.get("stats") or {}).items():
            if key == "all":
                stats["all"] = stats.get("all", 0) + val
            else:
                job_stats = dict(stats.get(key, {}))
                for sk, sv in val.items() if isinstance(val, dict) else []:
                    job_stats[sk] = job_stats.get(sk, 0) + sv
                stats[key] = job_stats
        ach["stats"] = stats

        # Apply talents
        talents = dict(ach.get("talents", {}))
        for job_id, talent_list in (group.get("talents") or {}).items():
            existing = list(talents.get(job_id, []))
            for t in talent_list:
                if t not in existing:
                    existing.append(t)
            talents[job_id] = existing
        ach["talents"] = talents

        player.achievements = ach
        db.commit()
        logger.info(f"Applied reward group {group_id} to player {player_steamid}")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to apply reward group: {e}")
        return False
    finally:
        db.close()


def check_and_apply_rating_rewards():
    """Check rating leaderboard and apply rewards to top players."""
    from database import SessionLocal
    from models import PlayerRating, PlayerData
    from sqlalchemy import desc

    rewards = load_rating_rewards()
    if not rewards:
        return

    db = SessionLocal()
    try:
        for reward_config in rewards:
            role = reward_config.get("role", "")
            places = reward_config.get("places", [])
            if not role or not places:
                continue

            # Get leaderboard for this role
            records = (
                db.query(PlayerRating)
                .filter(PlayerRating.role == role, PlayerRating.current_rating > 0)
                .order_by(desc(PlayerRating.current_rating))
                .limit(max(p.get("place", 0) for p in places))
                .all()
            )

            for place_config in places:
                place = place_config.get("place", 0)
                group_id = place_config.get("reward_group_id", "")
                if place < 1 or not group_id:
                    continue
                if place <= len(records):
                    player_rating = records[place - 1]
                    apply_reward_group_to_player(player_rating.steam_id, group_id)

    except Exception as e:
        logger.error(f"Failed to check rating rewards: {e}")
    finally:
        db.close()


def check_and_apply_playtime_rewards():
    """Check playtime conditions and apply reward groups to qualifying players."""
    conditions = load_playtime_conditions()
    if not conditions:
        return

    stats = get_latest_playtime_stats()
    if not stats or not stats.get("players"):
        return

    for condition in conditions:
        period = condition.get("period", "day")  # day, week, month
        min_seconds = condition.get("min_seconds", 0)
        group_id = condition.get("reward_group_id", "")
        if not group_id or min_seconds <= 0:
            continue

        for pid, pdata in stats["players"].items():
            playtime = pdata.get(period, 0)
            if playtime >= min_seconds:
                steamid = pdata.get("steamid", "")
                if steamid:
                    apply_reward_group_to_player(steamid, group_id)


# ══════════════════════════════════════════════════════════════════════════════
# Background Worker (daily at 00:00 MSK)
# ══════════════════════════════════════════════════════════════════════════════

_playtime_stats_worker_running = False


def _playtime_stats_worker_loop():
    """Background worker: runs daily at 00:00 MSK."""
    global _playtime_stats_worker_running
    _playtime_stats_worker_running = True
    logger.info("Playtime stats worker started")

    # Initial calculation on startup
    time.sleep(10)  # Wait for DB to be ready
    calculate_playtime_stats()

    while _playtime_stats_worker_running:
        now = _now_msk()
        # Calculate seconds until next 00:00 MSK
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        wait_seconds = (tomorrow - now).total_seconds()

        # Sleep until midnight (check every 60s if we should stop)
        slept = 0
        while slept < wait_seconds and _playtime_stats_worker_running:
            time.sleep(60)
            slept += 60

        if not _playtime_stats_worker_running:
            break

        # It's midnight MSK — run calculations
        logger.info("Midnight MSK — running playtime stats calculation")
        calculate_playtime_stats()

        # Check and apply rewards
        try:
            check_and_apply_rating_rewards()
        except Exception as e:
            logger.error(f"Rating rewards check failed: {e}")

        try:
            check_and_apply_playtime_rewards()
        except Exception as e:
            logger.error(f"Playtime rewards check failed: {e}")

    logger.info("Playtime stats worker stopped")


def start_playtime_stats_worker():
    """Start the daily playtime stats worker."""
    global _playtime_stats_worker_running
    if _playtime_stats_worker_running:
        return
    t = threading.Thread(target=_playtime_stats_worker_loop, daemon=True, name="PlaytimeStatsWorker")
    t.start()
