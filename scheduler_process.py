"""Standalone entrypoint for the background scheduler/workers, used only in
multi-worker mode (config.py's UVICORN_WORKERS > 1 — see api_server.py).

Runs the exact same startup logic as main.py's FastAPI startup hook, minus
the HTTP server, so the periodic scheduler (Discord role sync, rating
publish, etc.) plus the log/playtime-stats/replay background workers exist
in exactly ONE process even when several uvicorn worker processes are also
running — those are launched with WORKER_ROLE=web and skip starting any of
this themselves (see main.py's startup() hook).

The Discord bot (discord_bot/bot.py) also lives here in multi-worker mode,
since it needs its own live asyncio event loop and this is the one process
    guaranteed to run exactly once. Ticket send/close requests from separate
    WORKER_ROLE=web processes cross this boundary through the marked durable
    DiscordBotAction queue; the bot process is its sole consumer. Other legacy
    schedule(...) call sites remain in-process only. In single-process mode the
    bot instead runs inside main.py's startup hook and consumes the same ticket
    queue there.
"""

import asyncio
import logging

# Use the same WARNING-only console and detailed file logging as the web and
# proxy processes. This process is otherwise an easy back door for the old
# INFO-level scheduler/polling flood in multi-worker deployments.
from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting dedicated scheduler/background-worker process")

    from database import init_db
    import models  # noqa: F401 — ensure all models are registered before create_all
    import runtime_control
    try:
        init_db()
        runtime_control.set_db_healthy(True)
    except Exception:
        # A dead DB shouldn't stop this process from starting either — the
        # workers' own SessionLocal() calls will just keep failing/retrying
        # per-task (each wrapped in its own try/except, see task_scheduler.py)
        # until the DB comes back, same degrade-gracefully approach as the
        # web process (see main.py's startup()).
        logger.exception("Database initialization failed at startup — continuing in degraded mode")
        runtime_control.set_db_healthy(False)

    # Same rating-config override load as main.py's startup hook (see there
    # for why) — this process is the one that actually runs rating
    # recalculation in multi-worker mode, so it must not run on stale
    # in-memory defaults after a superuser edit + restart.
    try:
        from database import SessionLocal
        import rating_constants
        _db = SessionLocal()
        try:
            if rating_constants.load_overrides_from_db(_db):
                logger.info("Rating config: applied saved override from AppSetting")
        finally:
            _db.close()
    except Exception:
        logger.exception("Failed to load rating config override at startup — using shipped defaults")

    from workers import start_playtime_worker, watch_runtime_state
    start_playtime_worker()  # starts the full task_scheduler with all periodic tasks
    from services_logs import start_log_worker
    start_log_worker()
    from services_playtime_stats import start_playtime_stats_worker
    start_playtime_stats_worker()
    from services_replays import start_replay_worker
    start_replay_worker()
    # Reacts to "workers on/off/restart" console commands (see console.py) —
    # the console process only ever writes runtime_control's state file,
    # this thread is what actually stops/restarts the workers living here.
    watch_runtime_state()

    logger.info("Scheduler/background workers started — running Discord bot on this process's event loop")
    # Every start_*() call above spawns its own daemon thread and returns
    # immediately; the Discord bot (in-process, see discord_bot/bot.py) needs
    # an actual running event loop though — supervise_bot() both provides
    # that (this replaces the old `while True: time.sleep(3600)` idle loop)
    # and keeps the process alive for the background threads above.
    from discord_bot import bot as discord_bot
    asyncio.run(discord_bot.supervise_bot())


if __name__ == "__main__":
    main()
