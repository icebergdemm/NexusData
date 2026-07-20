"""Standalone entrypoint for the background scheduler/workers, used only in
multi-worker mode (config.py's UVICORN_WORKERS > 1 — see api_server.py).

Runs the exact same startup logic as main.py's FastAPI startup hook, minus
the HTTP server, so the periodic scheduler (Discord role sync, rating
publish, etc.) plus the log/playtime-stats/replay background workers exist
in exactly ONE process even when several uvicorn worker processes are also
running — those are launched with WORKER_ROLE=web and skip starting any of
this themselves (see main.py's startup() hook).
"""

import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting dedicated scheduler/background-worker process")

    from database import init_db
    import models  # noqa: F401 — ensure all models are registered before create_all
    init_db()

    from workers import start_playtime_worker
    start_playtime_worker()  # starts the full task_scheduler with all periodic tasks
    from services_logs import start_log_worker
    start_log_worker()
    from services_playtime_stats import start_playtime_stats_worker
    start_playtime_stats_worker()
    from services_replays import start_replay_worker
    start_replay_worker()

    logger.info("Scheduler/background workers started — idling to keep their threads alive")
    # Every start_*() call above spawns its own daemon thread and returns
    # immediately — this process just needs to stay alive for those threads
    # to keep running.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
