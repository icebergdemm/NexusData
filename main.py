from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
import logging
import os
import tempfile

# ── Fix "No space left on device" for multipart uploads ──────────────────────
# Python's tempfile module uses /tmp by default which may have limited space/inodes.
# Redirect temp files to the application's own data directory.
_app_tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tmp")
os.makedirs(_app_tmp_dir, exist_ok=True)
tempfile.tempdir = _app_tmp_dir

from config import get_settings, reset_settings_cache
from database import init_db
import models  # ensure all models are registered before create_all
from routes import status_router, kits_router, players_router, stats_router, admin_router, events_router, moderation_api_router, moderation_panel_router, sub_projects_router, sub_projects_polygon_router, sub_checker_router, game_api_router, game_public_router, ratings_router, rating_events_router, rating_admin_v2_router, discord_bot_panel_router, discord_viewer_router, tickets_panel_router, logs_router, dispatch_api_router, dispatch_panel_router, dispatch_client_router, replays_api_router, replays_ws_router, replays_panel_router, analytics_router, notifications_router, documents_router, onlyoffice_router, departments_router, tasks_router, world_state_router, world_state_admin_router, world_state_client_router, warehouse_admin_router, warehouse_client_router, cache_sync_api_router, sounds_router, dolls_game_router, dolls_admin_router, achievements_game_router, achievements_admin_router, error_log_router, perf_monitor_router, monster_spawn_game_router, monster_spawn_admin_router, ai_dossier_router, communications_router, server_restart_router, voice_index_router
from workers import start_playtime_worker, stop_all_workers, watch_runtime_state
import runtime_control
from datetime import datetime

settings = get_settings()
logger = logging.getLogger(__name__)

# Structured console/file/DB logging (logging_config.py) — was defined but
# never actually wired up anywhere before this, so ERROR-level logs across
# the whole app only ever reached Python's bare stderr "last resort" handler
# and nothing was persisted anywhere searchable. This activates real
# handlers (incl. services_error_log.DatabaseErrorHandler, which feeds the
# "Журнал ошибок" admin page — superusers only, see routes/error_log.py).
from logging_config import setup_logging
setup_logging()

# Ensure fresh settings on startup (clear any cached old instances)
reset_settings_cache()
settings = get_settings()

# Jinja2 templates for global error pages
templates = Jinja2Templates(directory="templates")


class _NoGzipForRawPayloads:
    """Strips Accept-Encoding before GZipMiddleware ever sees the request, so
    those responses go out untouched. Pure-ASGI (not BaseHTTPMiddleware) to
    avoid buffering the whole request/response — this only ever touches
    request headers, never the body.

    Two cases:

    1. Requests authenticated via API key (the Barotrauma game-server plugin,
       external X-API-Key/client_api_key integrations) — those clients want
       the raw bytes.

    2. File-download endpoints (`.../download` and replay `.../archive`). These serve payloads that are
       ALREADY compressed (replay .barodemo.zip, gzipped .sub files), so
       gzipping them again gains nothing while burning real CPU — at
       compresslevel=9 on a 100+ МБ replay that is very much not free.
       Worse, it actively broke the replay player's progress UI: Starlette's
       GZipMiddleware `del headers["Content-Length"]` for STREAMING responses
       (see its IdentityResponder.send_with_compression), and FileResponse is
       streaming. The browser then reports no total size, so the player could
       not compute a download percentage and showed a wrong/absent size.

    Must be registered via app.add_middleware(...) AFTER GZipMiddleware in
    create_app() — Starlette wraps middleware so the last one added is
    outermost, i.e. runs first on the way in, which is what lets this strip
    the header before GZipMiddleware's own dispatch inspects it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        skip_gzip = any(k == b"x-api-key" for k, _v in scope.get("headers") or [])
        if not skip_gzip:
            skip_gzip = b"api_key=" in scope.get("query_string", b"")
        if not skip_gzip:
            # Только точное окончание пути: не ловим случайные совпадения
            # вроде "/downloads" или "?x=/download".
            path = scope.get("path", "").rstrip("/")
            skip_gzip = path.endswith("/download") or path.endswith("/archive")

        if skip_gzip:
            scope = {
                **scope,
                "headers": [
                    (k, v) for k, v in scope["headers"] if k != b"accept-encoding"
                ],
            }

        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    """Application factory"""
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        # The interactive API schema (Swagger/ReDoc/openapi.json) enumerates
        # every endpoint of an admin/moderation panel — needless attack-surface
        # disclosure in production. Only expose it in debug/dev.
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    # Add session middleware with max_age
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age,
        https_only=not settings.debug,
        same_site="lax",
    )

    # Setup CORS. A wildcard origin combined with credentials is a serious
    # misconfiguration: Starlette then reflects the caller's Origin and sends
    # Access-Control-Allow-Credentials: true, letting ANY website make
    # cookie-authenticated requests on behalf of a logged-in admin. The SPA is
    # served same-origin (see the /admin mount below) so it never needs CORS
    # credentials, and the game-server/external integrations authenticate with
    # an X-API-Key header (not a browser, not cookies) so restricting origins
    # doesn't affect them. If origins are left as "*", force credentials off.
    cors_origins = settings.cors_origins_list or ["*"]
    cors_credentials = settings.cors_credentials
    if "*" in cors_origins and cors_credentials:
        logger.warning(
            "CORS: wildcard origin ('*') with credentials is unsafe — disabling "
            "credentials. Set CORS_ORIGINS to your explicit site origin(s) in .env."
        )
        cors_credentials = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_credentials,
        allow_methods=settings.cors_methods,
        allow_headers=settings.cors_headers,
    )

    # GZip compression for responses > 500 bytes — but never for API-key
    # (machine) clients: the Barotrauma game-server plugin's HTTP client
    # advertises Accept-Encoding: gzip without actually decompressing the
    # response, so it received raw gzip bytes and choked trying to parse
    # them as JSON ("'0x1F' is an invalid start of a value" — 0x1F is
    # gzip's magic byte). Browsers/the SPA always decompress correctly and
    # never send an API key, so stripping Accept-Encoding only for API-key
    # requests (added AFTER GZipMiddleware below, so it runs BEFORE it on
    # the way in — see Starlette's outermost-added-last ordering) fixes the
    # game server without touching admin-panel compression at all.
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(_NoGzipForRawPayloads)

    # Exception handlers
    from starlette.responses import JSONResponse as _JSONResponse

    def _is_api(request) -> bool:
        p = str(request.url.path or "")
        return p.startswith("/api/")

    # Every browser-facing page except /login is the React SPA now — instead
    # of rendering the old Jinja2 error/maintenance templates (visually
    # inconsistent with the app, see templates/404.html, 403.html, 401.html,
    # error_generic.html, maintenance.html — none of these are deleted, just
    # unused from here on, in case something still references them directly),
    # serve the SPA shell itself. React's own error handling then takes over:
    # MaintenanceBanner (polls /healthz) for degraded/maintenance states,
    # AppErrorBoundary for render crashes, RequireAuth's own 401-redirect for
    # auth — all already styled consistently with the rest of the app. The
    # HTTP status code is still set correctly (503/403/etc.) for anything
    # that inspects it (monitoring, curl) — browsers render the body
    # regardless of status code as long as it isn't a near-empty response.
    _spa_index_path = os.path.join("frontend", "dist", "index.html")

    def _serve_spa_or_fallback(status_code: int, fallback_message: str):
        from fastapi.responses import FileResponse
        if os.path.isfile(_spa_index_path):
            return FileResponse(_spa_index_path, status_code=status_code)
        return PlainTextResponse(fallback_message, status_code=status_code)

    def _error_response(request, status_code, title, message, details):
        """Return JSON for API clients, the SPA shell for browsers (with a
        plain-text fallback if the frontend hasn't been built yet)."""
        if _is_api(request):
            return _JSONResponse(status_code=status_code,
                                 content={"success": False, "error": message, "details": details})
        return _serve_spa_or_fallback(status_code, message)

    def _maintenance_response(request, message: str, status_code: int = 503):
        """Same JSON-for-API/SPA-for-browser split as _error_response above,
        but for a deliberate degraded state (maintenance mode, a subsystem
        toggled off via the console, or a dead DB) rather than an exception —
        see maintenance_gate_middleware below. The SPA's own MaintenanceBanner
        polls /healthz and shows the real message/design — this just needs
        to get React booted at all, even mid-outage."""
        if _is_api(request):
            return _JSONResponse(
                status_code=status_code,
                content={"success": False, "maintenance": True, "error": message},
            )
        return _serve_spa_or_fallback(status_code, message)

    def _short_db_error(exc: Exception) -> str:
        """Exception text WITHOUT the full SQL statement/params.

        str(SQLAlchemyError) embeds the complete statement (often ~10 KB).
        During the 2026-09-09 disk-full outage the game servers' 1-second
        ingest retries made db_exception_handler rewrite that whole blob into
        error.log twice per second — pure log amplification exactly when the
        disk was already the problem. The DBAPI-level message (e.g.
        "(1021, 'Disk got full...')") plus the exception type is what a
        human needs; the full SQL stays available in the code/queries.
        """
        orig = getattr(exc, "orig", None)
        text = f"{type(exc).__name__}: {orig if orig is not None else exc}"
        return text[:500]

    @app.exception_handler(IntegrityError)
    async def integrity_exception_handler(request, exc):
        logger.error("Integrity error on %s %s: %s", request.method, request.url.path, _short_db_error(exc))
        return _error_response(request, 409, "Ошибка целостности данных",
                               "Данный элемент уже существует или нарушены ограничения целостности.",
                               "integrity_error")

    @app.exception_handler(SQLAlchemyError)
    async def db_exception_handler(request, exc):
        logger.error("Database error on %s %s: %s", request.method, request.url.path, _short_db_error(exc))
        return _error_response(request, 500, "Ошибка базы данных",
                               "Не удалось выполнить операцию с базой данных. Пожалуйста, попробуйте позже.",
                               "database_error")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        logger.warning(f"Validation error on {request.url.path}: {exc}")
        # Return JSON for API endpoints, HTML for admin panel
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Некорректный запрос", "details": str(exc)},
            )
        return _error_response(request, 422, "Некорректный запрос",
                               "Что-то пошло не так. Проверьте данные и попробуйте снова.",
                               "validation_error")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        logger.exception(f"Unhandled error on {request.url.path}: {exc}")
        return _error_response(request, 500, "Внутренняя ошибка",
                               "Что-то пошло не так. Мы уже работаем над исправлением.",
                               "internal_error")
    
    # Include routers
    app.include_router(status_router, prefix="/api/v1")
    app.include_router(kits_router, prefix="/api/v1")
    app.include_router(players_router, prefix="/api/v1")
    app.include_router(stats_router, prefix="/api/v1")
    app.include_router(admin_router)
    app.include_router(voice_index_router)
    app.include_router(events_router)
    app.include_router(moderation_api_router)
    app.include_router(moderation_panel_router)
    app.include_router(sub_projects_router)
    app.include_router(sub_projects_polygon_router)
    app.include_router(sub_checker_router)
    app.include_router(game_api_router)
    app.include_router(game_public_router)
    app.include_router(ratings_router)
    app.include_router(rating_events_router)
    app.include_router(rating_admin_v2_router)
    app.include_router(discord_bot_panel_router)
    app.include_router(discord_viewer_router)
    app.include_router(tickets_panel_router)
    app.include_router(logs_router)
    app.include_router(dispatch_api_router)
    app.include_router(dispatch_panel_router)
    app.include_router(dispatch_client_router)
    app.include_router(replays_api_router)
    app.include_router(replays_ws_router)
    app.include_router(replays_panel_router)
    app.include_router(analytics_router)
    app.include_router(notifications_router)
    app.include_router(documents_router)
    app.include_router(onlyoffice_router)
    app.include_router(departments_router)
    app.include_router(tasks_router)
    app.include_router(world_state_router)
    app.include_router(world_state_admin_router)
    app.include_router(world_state_client_router)
    app.include_router(warehouse_admin_router)
    app.include_router(warehouse_client_router)
    app.include_router(cache_sync_api_router)
    app.include_router(sounds_router)
    app.include_router(dolls_game_router)
    app.include_router(dolls_admin_router)
    app.include_router(achievements_game_router)
    app.include_router(achievements_admin_router)
    app.include_router(error_log_router)
    app.include_router(perf_monitor_router)
    app.include_router(monster_spawn_game_router)
    app.include_router(monster_spawn_admin_router)
    app.include_router(ai_dossier_router)
    app.include_router(communications_router)
    app.include_router(server_restart_router)

    # ── Register global Jinja2 filters for MSK time display ──────────────────
    from utils_time import utc_to_msk, format_msk
    import jinja2

    def _jinja_filter_msk(dt, fmt="%d.%m.%Y %H:%M"):
        """Jinja2 filter: {{ some_datetime|msk }} or {{ some_datetime|msk('%H:%M') }}"""
        return format_msk(dt, fmt)

    # Patch all Jinja2 environments created by route modules
    for route in app.routes:
        endpoint = getattr(route, 'endpoint', None)
        # Also check sub-applications
    # Global approach: register filter on template module import
    import importlib
    for mod_name in ['routes.admin', 'routes.moderation', 'routes.sub_projects',
                     'routes.replays', 'routes.ratings', 'routes.tickets_api',
                     'routes.dispatch', 'routes.discord_bot_api', 'routes.logs',
                     'routes.analytics']:
        try:
            mod = importlib.import_module(mod_name)
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, Jinja2Templates):
                    attr.env.filters['msk'] = _jinja_filter_msk
                    attr.env.globals['utc_to_msk'] = utc_to_msk
        except Exception:
            pass
    # Also the main templates object
    templates.env.filters['msk'] = _jinja_filter_msk
    templates.env.globals['utc_to_msk'] = utc_to_msk

    # Serve static files from './static' at '/static' with caching
    from starlette.staticfiles import StaticFiles as _StaticFiles
    from starlette.responses import Response as _Response

    class CachedStaticFiles(_StaticFiles):
        """StaticFiles with Cache-Control headers for browser caching.

        static/js/ — исключение: там лежат hand-written скрипты вроде
        replay_player.js, подключаемые по НЕВЕРСИОНИРОВАННОМУ <script src>
        (frontend/src/pages/replays/player.tsx), и меняются они прямо в ходе
        отладки. max-age=86400 + stale-while-revalidate=604800 (как у
        остального /static — картинок, иконок предметов, которые почти
        никогда не меняются) означает: браузер до СУТОК не пойдёт к серверу
        вообще, а после — ещё до НЕДЕЛИ будет отдавать заведомо устаревшую
        версию, попутно её фоново обновляя. На практике это уже воспроизвели:
        `update` выкатывал фикс за фиксом, а у пользователя стабильно
        исполнялся один и тот же кэшированный файл — новый код на диске был,
        но браузер к нему не обращался.
        no-cache (несмотря на название) НЕ отключает кэш — он обязывает
        браузер каждый раз слать условный запрос (If-None-Match/
        If-Modified-Since) и использовать кэш ТОЛЬКО если сервер подтвердит
        "не изменилось" (304, без пересылки тела). Свежий код подхватывается
        сразу после следующего запроса, а не после того как истечёт сутки/
        неделя кэша.
        """
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if response.status_code == 200:
                normalized = str(path).replace("\\", "/")
                if normalized == "js" or normalized.startswith("js/"):
                    response.headers["Cache-Control"] = "no-cache"
                else:
                    # Cache static assets for 1 day in browser, 7 days with revalidation
                    response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
            return response

    class ImmutableStaticFiles(_StaticFiles):
        """Serve content-hashed Vite assets with a permanent browser cache."""

        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if response.status_code == 200:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

    app.mount("/static", CachedStaticFiles(directory="static"), name="static")

    # Serve uploaded sub-project images at /sub_projects/images/
    sub_img_dir = os.path.join("uploads", "sub_projects", "images")
    os.makedirs(sub_img_dir, exist_ok=True)
    app.mount("/sub_projects/images", StaticFiles(directory=sub_img_dir), name="sub_project_images")

    # ── New React/shadcn SPA (Part 3 of the CRM rewrite), served under /admin ──
    # This is the only UI now — the legacy Jinja2 panel's routes are still
    # registered (untouched, under /adminold) but unreachable: the
    # security_and_csrf_middleware below bounces every /adminold/* request
    # here before routing. Registered last so it never shadows any API route.
    # Guarded so startup doesn't fail before the first `npm run build` has
    # produced frontend/dist.
    from fastapi.responses import FileResponse as _FileResponse

    _spa_dist_dir = os.path.join("frontend", "dist")
    _spa_assets_dir = os.path.join(_spa_dist_dir, "assets")
    _spa_index_file = os.path.join(_spa_dist_dir, "index.html")

    if os.path.isdir(_spa_assets_dir):
        app.mount("/admin/assets", ImmutableStaticFiles(directory=_spa_assets_dir), name="spa-assets")

    async def _spa_index_response():
        if os.path.isfile(_spa_index_file):
            # The shell filename is stable and must be revalidated so it can
            # point at the latest content-hashed immutable assets after deploy.
            return _FileResponse(_spa_index_file, headers={"Cache-Control": "no-cache"})
        raise StarletteHTTPException(status_code=503, detail="Frontend not built yet — run `npm run build` in frontend/")

    @app.get("/healthz")
    async def healthz():
        """Unauthenticated, DB-free liveness probe — unlike /api/v1/health
        (which requires the API key when require_auth is on), this is what
        the frontend's MaintenanceBanner and any external monitor can poll
        without credentials. Deliberately does not touch the database."""
        state = runtime_control.read_state()
        if state.maintenance_mode:
            status = "maintenance"
        elif not state.db_healthy or not state.api_enabled or not state.web_enabled:
            status = "degraded"
        else:
            status = "ok"
        return _JSONResponse({
            "status": status,
            "db_healthy": state.db_healthy,
            "api_enabled": state.api_enabled,
            "web_enabled": state.web_enabled,
            "workers_enabled": state.workers_enabled,
            "maintenance_mode": state.maintenance_mode,
            "maintenance_message": state.maintenance_message or None,
        })

    @app.get("/admin")
    async def spa_root():
        return await _spa_index_response()

    @app.get("/")
    async def root_redirect():
        """The React SPA is the only UI now — the legacy /adminold panel is disabled."""
        return RedirectResponse(url="/admin", status_code=302)

    @app.get("/admin/{full_path:path}")
    async def spa_catchall(full_path: str):
        return await _spa_index_response()

    @app.get("/v2")
    async def spa_v2_root():
        """Optional V2 shell; /admin remains the default and rollback target."""
        return await _spa_index_response()

    @app.get("/v2/{full_path:path}")
    async def spa_v2_catchall(full_path: str):
        return await _spa_index_response()

    # Back-compat: external links were shared as /tasks/... (без /admin префикса).
    # До фикса они отдавали 404 со статусом 404 (SPA через exception_handler) и
    # не матчились с basename /admin, поэтому React перекидывал на /. Редирект
    # сохраняет старые ссылки и внешние переходы (из Discord, браузера).
    @app.get("/tasks")
    async def spa_tasks_root(request: Request):
        qs = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"/admin/tasks{qs}", status_code=302)

    @app.get("/tasks/{full_path:path}")
    async def spa_tasks_catchall(full_path: str, request: Request):
        qs = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"/admin/tasks/{full_path}{qs}", status_code=302)

    # Startup event
    @app.on_event("startup")
    async def startup():
        logger.info(f"Starting {settings.app_name} v{settings.app_version}")
        logger.info(f"require_auth={settings.require_auth}, api_key={settings.api_key[:4]}*** (len={len(settings.api_key)})")

        # Ограничиваем пул потоков, в который FastAPI отправляет синхронные
        # обработчики и run_in_threadpool. По умолчанию их 40, и каждый
        # тянет за собой стек, соединение к MySQL из пула и собственное
        # thread-local подключение к SQLite логов со своим кэшем страниц —
        # на контейнере с жёстким лимитом 2 ГБ это лишние сотни мегабайт.
        # 12 потоков достаточно: параллелизм всё равно упирается в БД.
        try:
            import anyio.to_thread
            anyio.to_thread.current_default_thread_limiter().total_tokens = 12
        except Exception as e:
            logger.warning(f"Не удалось ограничить пул потоков: {e}")
        try:
            init_db()
            logger.info("Database initialized")
            runtime_control.set_db_healthy(True)
        except Exception:
            # A dead DB at boot must not take the whole process down with it —
            # the app still binds the port and serves /healthz, the SPA shell,
            # and the maintenance page; DB-dependent API routes degrade to a
            # clean 503 via maintenance_gate_middleware instead of every
            # request 500ing on a fresh connection attempt.
            logger.exception("Database initialization failed at startup — continuing in degraded mode")
            runtime_control.set_db_healthy(False)
        app.state.start_time = datetime.utcnow()

        # Event-loop lag watchdog (services_perf_monitor.py) — started
        # unconditionally, NOT gated by WORKER_ROLE like the background
        # scheduler below: it measures whether THIS process's own event loop
        # (the one uvicorn uses to serve every HTTP request) is responsive,
        # which matters for every process that ever serves a request,
        # single-process default or one of several `--workers`. See the
        # module docstring for why this is the most direct signal for
        # "сайт полностью завис на несколько секунд".
        try:
            import asyncio as _asyncio_perf
            import services_perf_monitor
            _asyncio_perf.create_task(services_perf_monitor.event_loop_watchdog())
        except Exception:
            logger.exception("Failed to start event-loop watchdog")

        # Apply any superuser-saved rating formula override (see the Рейтинг
        # page's config panel) before this process computes/serves any
        # rating — every worker process needs this, not just the one running
        # the scheduler, since compute_rating() is called inline wherever a
        # rating is displayed.
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

        # WORKER_ROLE=web marks one of several parallel uvicorn worker
        # PROCESSES (see api_server.py's multi-worker mode) — every process
        # spawned by `uvicorn --workers N` runs this exact same startup hook,
        # so without this gate the periodic scheduler (Discord role sync,
        # rating publish, etc.) and the log/playtime-stats/replay worker
        # threads would each run N times in parallel. Unset (single-process
        # deployment, the default/previous behavior) or "scheduler" (the
        # dedicated non-web process multi-worker mode spawns) both still run
        # them normally — only a "web" process skips this block.
        if os.environ.get("WORKER_ROLE") != "web":
            start_playtime_worker()  # now starts full scheduler with all tasks
            # rating_engine worker replaced by task_scheduler (registered in workers.py)
            from services_logs import start_log_worker
            start_log_worker()
            from services_playtime_stats import start_playtime_stats_worker
            start_playtime_stats_worker()
            from services_replays import start_replay_worker
            start_replay_worker()
            # Lets the "workers on/off/restart" console command (see console.py)
            # affect this process even though it never reads stdin itself —
            # the console only ever writes runtime_control's state file, this
            # thread is what actually reacts to it wherever the workers live.
            watch_runtime_state()

            # Discord bot — in-process now (see discord_bot/bot.py), started
            # alongside the other background workers in whichever process
            # actually owns them (this single-process startup hook normally;
            # scheduler_process.py in multi-worker mode — see its own
            # supervise_bot() call). Runs its own retry/backoff loop, so a
            # gateway drop or crash doesn't need this process to restart.
            import asyncio as _asyncio
            from discord_bot import bot as discord_bot
            _asyncio.create_task(discord_bot.supervise_bot())
        else:
            logger.info("WORKER_ROLE=web — skipping background workers/scheduler in this process")

    @app.on_event("shutdown")
    async def shutdown():
        if os.environ.get("WORKER_ROLE") != "web":
            # stop_all_workers() joins scheduler worker threads for up to 8s
            # (task_scheduler.stop). Run it via run_in_threadpool so the event
            # loop keeps servicing requests while shutdown waits — the perf
            # watchdog recorded a 7.6s "event loop lag" purely from this
            # synchronous join in the old shutdown handler.
            await run_in_threadpool(stop_all_workers)

    # HTTP exception handler — JSON for API clients, the SPA shell for
    # everything else except the /login redirect (see _error_response's
    # comment above for why: only /login itself stays a separate page).
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        path = str(request.url.path or "")
        if path.startswith("/api/"):
            return _JSONResponse(status_code=exc.status_code, content={"success": False, "error": str(exc.detail)})
        if exc.status_code == 401:
            # Unauthenticated — bounce to the actual login page (the one
            # page this whole SPA-ification deliberately leaves alone).
            if path.startswith("/logout") or path.startswith("/auth"):
                login_url = f"/login?next={path}"
                return RedirectResponse(url=login_url, status_code=302)
            return RedirectResponse(url=f"/login?next={path}", status_code=302)
        if exc.status_code in (403, 404):
            return _serve_spa_or_fallback(exc.status_code, str(exc.detail))
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    # Security headers and CSRF token middleware
    import secrets

    @app.middleware("http")
    async def security_and_csrf_middleware(request, call_next):
        # The legacy Jinja2 panel is disabled — every /adminold/* request bounces
        # to the React SPA before any of its 100 route handlers ever run.
        if request.url.path.startswith("/adminold"):
            return RedirectResponse(url="/admin", status_code=302)

        # Method+path of the current request, for services_error_log's DB
        # error handler to attach to any ERROR/CRITICAL log raised while
        # handling it (see "Журнал ошибок" — superusers only). A ContextVar,
        # not a global: isolated per-request via asyncio's per-task context
        # copy, so concurrent requests never see each other's path.
        from services_error_log import set_request_context
        set_request_context(request.method, request.url.path)

        # Ensure CSRF token exists in session
        try:
            if "csrf_token" not in request.session:
                request.session["csrf_token"] = secrets.token_urlsafe(16)
        except Exception:
            # Session may not be available for some requests
            pass

        # Slow-request detection (services_perf_monitor.py) — the
        # time.monotonic() calls and comparison cost microseconds on every
        # request; the only real work (a DB write) happens in the rare case
        # the threshold is actually exceeded, inside record_slow_request.
        import time as _time_perf
        _req_t0 = _time_perf.monotonic()

        response = await call_next(request)

        try:
            from services_perf_monitor import record_slow_request
            record_slow_request(request.method, request.url.path, _time_perf.monotonic() - _req_t0)
        except Exception:
            pass

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:; frame-src 'self' https://*.jit.si https://meet.jit.si https://*.rp-asper.ru https:"

        return response

    # Maintenance/degraded-mode gate — registered LAST so it's the OUTERMOST
    # middleware layer (Starlette wraps in add-order: the most-recently-added
    # @app.middleware("http") runs first on the way in), short-circuiting
    # before security_and_csrf_middleware and every route/DB call above when
    # the site is in maintenance mode, a subsystem was toggled off via the
    # console (see console.py), or the database failed to initialize.
    _MAINTENANCE_BYPASS_PREFIXES = ("/healthz", "/static", "/admin/assets")

    @app.middleware("http")
    async def maintenance_gate_middleware(request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _MAINTENANCE_BYPASS_PREFIXES):
            return await call_next(request)

        state = runtime_control.read_state()
        is_api = path.startswith("/api/")

        if state.maintenance_mode:
            return _maintenance_response(request, state.maintenance_message or "Ведутся технические работы. Пожалуйста, зайдите позже.")
        if is_api and not state.api_enabled:
            return _maintenance_response(request, "API временно отключено администратором.")
        if not is_api and not state.web_enabled:
            return _maintenance_response(request, "Веб-интерфейс временно отключён администратором.")
        if is_api and not state.db_healthy:
            return _maintenance_response(request, "База данных недоступна. Мы уже работаем над восстановлением.")

        return await call_next(request)

    # ── Injection-guard + rate-limit — регистрируются ПОСЛЕДНИМИ, поэтому
    # становятся САМЫМ внешним слоем (Starlette оборачивает в порядке
    # добавления: последний добавленный отрабатывает первым на входящем
    # запросе — тот же принцип, что и у maintenance_gate_middleware выше).
    # Проверяются раньше вообще всего остального в приложении, включая
    # maintenance-режим: заблокированный по IP или пойманный на инъекции
    # клиент не должен тратить ресурсы даже на страницу техработ.
    from middleware import InjectionGuardMiddleware, RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware, requests_per_window=settings.rate_limit_requests, window_seconds=settings.rate_limit_window)
    app.add_middleware(InjectionGuardMiddleware)

    return app


app = create_app()
