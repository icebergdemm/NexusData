import logging
import os
import subprocess
import sys
import threading
import uvicorn
from main import app
from config import get_settings

logger = logging.getLogger("api_server")

# NOTE: this process is always launched as a child of proxy_server.py now
# (which owns the public port, the stdin console, and this process's own
# lifecycle — see runtime_control.backend_enabled). It no longer reads stdin
# itself; running it standalone still works for a quick one-off check, but
# console commands (maintenance/api/web/workers/backend) require going
# through `python proxy_server.py`.

# The app always sits behind proxy_server.py (which itself sits behind nginx),
# so the TCP peer uvicorn sees is always the local proxy. Trust X-Forwarded-For
# ONLY from localhost so request.client.host reflects the real client IP (used
# for rate limiting + audit/punishment IP logging) — never trust it from an
# arbitrary peer, which would let a client spoof its own IP. nginx MUST set
# `proxy_set_header X-Forwarded-For $remote_addr;` (replace, not append) so a
# client cannot inject a forged leading entry — see the deployment guide.
_FORWARDED_ALLOW_IPS = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

if __name__ == "__main__":
    settings = get_settings()
    workers = max(1, settings.uvicorn_workers)

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
            proxy_headers=True,
            forwarded_allow_ips=_FORWARDED_ALLOW_IPS,
            access_log=False,
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

        # Nobody else watches this child in multi-worker mode — if it dies
        # (unhandled exception, OOM-kill, etc.) background tasks/the Discord
        # bot silently stop running until this whole process is restarted.
        # Auto-respawn it instead, mirroring proxy_server.py's
        # _backend_watcher_loop pattern for api_server.py's own child.
        _stop_scheduler_watcher = threading.Event()

        def _scheduler_watcher_loop():
            global scheduler_proc
            while not _stop_scheduler_watcher.wait(5.0):
                if scheduler_proc.poll() is not None:
                    logger.warning(
                        "scheduler_process.py died (exit %s) — restarting", scheduler_proc.returncode
                    )
                    scheduler_proc = subprocess.Popen([sys.executable, "scheduler_process.py"], env=scheduler_env)

        watcher = threading.Thread(target=_scheduler_watcher_loop, daemon=True, name="SchedulerWatcher")
        watcher.start()

        try:
            os.environ["WORKER_ROLE"] = "web"
            uvicorn.run(
                "main:app",
                host=settings.server_host,
                port=settings.server_port,
                workers=workers,
                proxy_headers=True,
                forwarded_allow_ips=_FORWARDED_ALLOW_IPS,
                access_log=False,
            )
        finally:
            _stop_scheduler_watcher.set()
            scheduler_proc.terminate()
