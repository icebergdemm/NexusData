import os
import subprocess
import sys
import uvicorn
from main import app
from config import get_settings
from console import start_console_thread

if __name__ == "__main__":
    settings = get_settings()
    workers = max(1, settings.uvicorn_workers)

    # Console commands (api/web/workers on|off|restart, maintenance, db check
    # — see console.py) work by writing to runtime_control's shared state
    # file, so starting this here — before uvicorn takes over the main
    # thread — is enough regardless of which mode below actually ends up
    # serving requests. This is also the only process that reads stdin at
    # all (this is what a Pterodactyl "Console" tab talks to).
    start_console_thread()

    if workers <= 1 or settings.debug:
        # Single-process mode — the previous, still-default behavior. The
        # scheduler and background workers run inline via main.py's startup
        # hook (WORKER_ROLE unset). uvicorn also refuses to combine
        # reload=True with workers>1, so debug mode always forces this path.
        uvicorn.run(
            "main:app",
            host=settings.server_host,
            port=settings.server_port,
            reload=settings.debug,
        )
    else:
        # Multi-worker mode: a dedicated side process runs the scheduler
        # exactly once (WORKER_ROLE=scheduler); N uvicorn worker processes
        # (WORKER_ROLE=web) serve HTTP only — every route in this app uses
        # blocking synchronous SQLAlchemy calls inside async def handlers,
        # which stalls that whole process's event loop for the query's
        # duration, so a single worker process meant one slow request froze
        # every other concurrent request site-wide. Real OS-level
        # parallelism across N processes is the safe mitigation without
        # rewriting every endpoint onto async SQLAlchemy.
        scheduler_env = {**os.environ, "WORKER_ROLE": "scheduler"}
        scheduler_proc = subprocess.Popen([sys.executable, "scheduler_process.py"], env=scheduler_env)
        try:
            os.environ["WORKER_ROLE"] = "web"
            uvicorn.run(
                "main:app",
                host=settings.server_host,
                port=settings.server_port,
                workers=workers,
            )
        finally:
            scheduler_proc.terminate()
