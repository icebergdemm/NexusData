"""
Always-up reverse-proxy front door for NexusData.

Owns the public port (default 0.0.0.0:8000 — the same port nginx on the VDS
already points at, see deploy/onlyoffice/NATIVE_INSTALL.md) and the stdin
console (see console.py). Run THIS instead of api_server.py, everywhere
(including local dev) — this is the one process that should never need
restarting for a normal backend code deploy.

The real app (api_server.py) runs as a CHILD process on an internal-only
port (default 127.0.0.1:8001) that this process starts, stops, restarts and
health-checks — see runtime_control.backend_enabled / console.py's
`backend on|off|restart`.

Deliberately has almost no dependency on the rest of this codebase (no
main.py/config.py/SQLAlchemy import) — a bug anywhere in the actual app must
never be able to take this process down too, since its whole job is to show
users a clean "technical works" page instead of a connection error while the
app is down for maintenance, mid-deploy, or crashed. It does NOT duplicate
the app's own maintenance_mode gate (main.py's maintenance_gate_middleware +
the SPA's MaintenanceBanner already handle that gracefully while the backend
process is alive) — it only ever steps in when the backend is genuinely
unreachable, which is exactly the case that gate can't cover by itself.

Deploy workflow this unlocks:
  maintenance on <текст>   (console — optional, customizes the page's text)
  backend off              (console — stops api_server.py; proxy stays up)
  ... replace code ...
  backend on               (console — respawns api_server.py)
  maintenance off          (console)
"""
import html
import logging
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

import runtime_control
from console import start_console_thread

logging.basicConfig(level=logging.INFO, format="%(asctime)s [proxy] %(message)s")
logger = logging.getLogger("proxy")

PROXY_HOST = os.environ.get("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8000"))
BACKEND_HOST = os.environ.get("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8001"))
BACKEND_BASE_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

# How often the watcher thread checks runtime_control + the child process's
# liveness — console.py's _BACKEND_RESTART_WAIT_SECONDS is tuned to be
# comfortably longer than this.
_WATCH_INTERVAL_SECONDS = 2.0

# Connection-specific headers that must never be relayed as-is between hops
# — httpx/Starlette each manage their own framing for these. Content-Length/
# Content-Encoding are deliberately NOT here: we stream raw upstream bytes
# unchanged (see aiter_raw() below), so whatever the backend declared for
# those stays accurate and is safe to forward through.
_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

_DEFAULT_MAINTENANCE_MESSAGE = "Ведутся технические работы. Пожалуйста, зайдите позже."


def _maintenance_html(message: str) -> str:
    safe_message = html.escape(message or _DEFAULT_MAINTENANCE_MESSAGE)
    return f"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Технические работы</title>
<style>
  html, body {{ height: 100%; margin: 0; }}
  body {{
    display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f1115; color: #e6e6e6; text-align: center; padding: 24px;
  }}
  .card {{ max-width: 480px; }}
  .icon {{ font-size: 48px; margin-bottom: 16px; }}
  h1 {{ font-size: 22px; margin: 0 0 12px; }}
  p {{ font-size: 15px; color: #a0a4ab; line-height: 1.5; margin: 0; white-space: pre-wrap; }}
</style>
</head><body>
  <div class="card">
    <div class="icon">\U0001f6e0️</div>
    <h1>Технические работы</h1>
    <p>{safe_message}</p>
  </div>
</body></html>"""


def _serve_maintenance() -> Response:
    message = runtime_control.read_state().maintenance_message
    return Response(_maintenance_html(message), status_code=503, media_type="text/html; charset=utf-8")


_http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(_app):
    global _http_client
    _http_client = httpx.AsyncClient()
    try:
        yield
    finally:
        await _http_client.aclose()


async def _proxy(request: Request) -> Response:
    url = f"{BACKEND_BASE_URL}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS]

    try:
        upstream_request = _http_client.build_request(
            request.method, url, headers=headers, content=request.stream(),
        )
        upstream = await _http_client.send(upstream_request, stream=True)
    except httpx.HTTPError as ex:
        logger.warning(f"Backend unreachable ({ex.__class__.__name__}) — serving maintenance page")
        return _serve_maintenance()
    except Exception:
        logger.exception("Unexpected error proxying request")
        return _serve_maintenance()

    response_headers = [(k, v) for k, v in upstream.headers.multi_items() if k.lower() not in _HOP_BY_HOP_HEADERS]

    async def _stream_body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    response = StreamingResponse(_stream_body(), status_code=upstream.status_code)
    response.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response_headers]
    return response


routes = [
    Route("/", _proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
    Route("/{path:path}", _proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
]

app = Starlette(routes=routes, lifespan=lifespan)


# ── Backend child-process lifecycle ─────────────────────────────────────

_backend_proc: Optional[subprocess.Popen] = None
_backend_lock = threading.Lock()
_stop_watcher = threading.Event()


def _start_backend():
    global _backend_proc
    with _backend_lock:
        if _backend_proc is not None and _backend_proc.poll() is None:
            return  # already running
        env = {**os.environ, "SERVER_HOST": BACKEND_HOST, "SERVER_PORT": str(BACKEND_PORT)}
        # Cap glibc's per-thread malloc arenas for the backend (and the
        # scheduler process it spawns, which inherits this env). The default
        # (up to 8×CPU arenas) is the main reason a threaded Python service's
        # RSS balloons and never shrinks at idle — freed memory scatters
        # across many arenas that glibc won't return to the OS. 2 arenas keeps
        # the footprint tight; combined with the periodic malloc_trim in
        # workers.task_memory_maintenance this fixes idle RAM growth. Must be
        # set BEFORE the child's libc initializes, hence here (env), not in
        # the app itself. setdefault so an explicit override still wins.
        env.setdefault("MALLOC_ARENA_MAX", "2")
        logger.info(f"Starting backend (api_server.py) on {BACKEND_HOST}:{BACKEND_PORT}")
        _backend_proc = subprocess.Popen([sys.executable, "api_server.py"], env=env)


def _stop_backend():
    global _backend_proc
    with _backend_lock:
        if _backend_proc is None:
            return
        if _backend_proc.poll() is None:
            logger.info("Stopping backend (api_server.py)")
            _backend_proc.terminate()
            try:
                _backend_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Backend didn't exit in time — killing")
                _backend_proc.kill()
        _backend_proc = None


def _backend_watcher_loop():
    """Reacts to runtime_control.backend_enabled and auto-restarts the
    backend if it dies unexpectedly while still supposed to be running —
    the same 'write shared state, whoever cares reacts' pattern
    workers.py's watch_runtime_state() already uses for workers_enabled."""
    while not _stop_watcher.is_set():
        try:
            state = runtime_control.read_state(force=True)
            with _backend_lock:
                running = _backend_proc is not None and _backend_proc.poll() is None
            if state.backend_enabled and not running:
                _start_backend()
            elif not state.backend_enabled and running:
                _stop_backend()
        except Exception:
            logger.exception("Backend watcher error")
        _stop_watcher.wait(_WATCH_INTERVAL_SECONDS)


if __name__ == "__main__":
    start_console_thread()
    _start_backend()
    watcher = threading.Thread(target=_backend_watcher_loop, daemon=True, name="BackendWatcher")
    watcher.start()
    logger.info(f"Proxy listening on {PROXY_HOST}:{PROXY_PORT}, backend at {BACKEND_BASE_URL}")
    try:
        uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)
    finally:
        _stop_watcher.set()
        _stop_backend()
