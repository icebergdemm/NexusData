"""
Background workers for Nexus Data.

All periodic tasks are now registered with the TaskScheduler instead of
spawning their own threads. This gives us:
  - Priority control (API requests always win)
  - Deduplication (no double-runs if a task takes longer than its interval)
  - Result caching (admin panel can poll completion status)
  - Unified logging and monitoring
"""

import logging
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal

logger = logging.getLogger(__name__)

# Try to import task_scheduler — if not present, fall back to simple thread model
try:
    from task_scheduler import scheduler, Priority
    _HAS_SCHEDULER = True
except ImportError:
    logger.warning("[Workers] task_scheduler not found, falling back to simple thread mode")
    _HAS_SCHEDULER = False
    scheduler = None
    Priority = None


# ═══════════════════════════════════════════════════════════════════════
# TASK IMPLEMENTATIONS (each opens its own DB session)
# ═══════════════════════════════════════════════════════════════════════

def task_compensate_playtime() -> dict:
    """Sync play_time from session durations. Runs every 30 min."""
    db = SessionLocal()
    try:
        result = db.execute(text("""
            UPDATE players_data pd
            JOIN (
                SELECT player_id, SUM(session_duration) AS total
                FROM player_sessions
                WHERE session_duration IS NOT NULL
                GROUP BY player_id
            ) st ON st.player_id = pd.player_id
            SET pd.play_time = st.total
            WHERE pd.play_time < st.total
        """))
        compensated = result.rowcount or 0
        db.commit()
        return {"compensated_players": compensated}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def task_recalculate_ratings() -> dict:
    """Full rating recalculation. Runs every 5 min."""
    from rating_engine import calculate_all_ratings, sync_ratings_to_db, apply_daily_erosion
    db = SessionLocal()
    try:
        ratings = calculate_all_ratings(db)
        if ratings:
            sync_ratings_to_db(db, ratings)
        apply_daily_erosion(db)
        return {"players_recalculated": len(ratings)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def task_sauron_twinks() -> dict:
    """Twink detection analysis. Triggered on demand, cached 10 min."""
    import services_sauron
    db = SessionLocal()
    try:
        results = services_sauron.detect_all_twink_pairs(db, days=90)
        return {"results": results, "count": len(results)}
    finally:
        db.close()


def task_sauron_deviant() -> dict:
    """Deviant behavior detection. Triggered on demand, cached 10 min."""
    import services_sauron
    db = SessionLocal()
    try:
        results = services_sauron.detect_deviant_behavior(db, days=30, min_incidents=2)
        return {"results": results, "count": len(results)}
    finally:
        db.close()


def task_sauron_summary() -> dict:
    """Lightweight Sauron summary counts."""
    import services_sauron
    db = SessionLocal()
    try:
        return services_sauron.get_sauron_summary(db)
    finally:
        db.close()


def task_cleanup_replays() -> dict:
    """Delete expired replay files. Runs every 6 hours."""
    import services_replays
    db = SessionLocal()
    try:
        deleted = services_replays.ReplayService(db).cleanup_expired()
        return {"deleted_replays": deleted}
    finally:
        db.close()


def task_refresh_logs() -> dict:
    """Refresh server log cache from SFTP. Runs every 30 min."""
    try:
        import services_logs
        services_logs.refresh_all_logs()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def task_refresh_analytics() -> dict:
    """Recompute the analytics dashboard's default-view snapshot. Runs every 5 min.

    Serves /admin/analytics so the page doesn't recompute all charts on every visit.
    """
    import services_analytics
    db = SessionLocal()
    try:
        return services_analytics.compute_default_snapshot(db)
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════
# STARTUP: register all periodic tasks and start scheduler
# ═══════════════════════════════════════════════════════════════════════

def start_playtime_worker():
    """Legacy entrypoint — now starts the full scheduler with all tasks."""
    if _HAS_SCHEDULER:
        _start_scheduler()
    else:
        _start_simple_workers()


def _start_simple_workers():
    """Fallback: simple daemon threads without scheduler (when task_scheduler.py is missing)."""
    import time

    def _loop(fn, interval):
        while True:
            try:
                fn()
            except Exception as e:
                logger.error(f"Worker {fn.__name__} error: {e}")
            time.sleep(interval)

    for fn, interval in [
        (task_compensate_playtime, 30 * 60),
        (task_recalculate_ratings, 5 * 60),
        (task_cleanup_replays, 6 * 60 * 60),
    ]:
        t = threading.Thread(target=_loop, args=(fn, interval), daemon=True, name=f"Worker-{fn.__name__}")
        t.start()
        logger.info(f"[Workers] Started fallback worker: {fn.__name__} every {interval}s")


def _start_scheduler():
    """Register periodic tasks and start the scheduler."""
    # Playtime compensation: LOW priority, every 30 min
    scheduler.register_periodic(
        task_compensate_playtime,
        interval_secs=30 * 60,
        priority=Priority.LOW,
        task_id="playtime_compensation",
    )

    # Rating recalculation: LOW priority, every 5 min
    scheduler.register_periodic(
        task_recalculate_ratings,
        interval_secs=5 * 60,
        priority=Priority.LOW,
        task_id="rating_recalc",
    )

    # Replay cleanup: BACKGROUND priority, every 6 hours
    scheduler.register_periodic(
        task_cleanup_replays,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="replay_cleanup",
    )

    # Log refresh: handled by services_logs built-in worker (interval set to 60s there)
    # No need to duplicate in scheduler

    # Analytics dashboard snapshot: LOW priority, every 5 min
    scheduler.register_periodic(
        task_refresh_analytics,
        interval_secs=5 * 60,
        priority=Priority.LOW,
        task_id="analytics_refresh",
    )

    scheduler.start()
    logger.info("[Workers] All periodic tasks registered and scheduler started")
