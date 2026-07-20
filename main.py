from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse, RedirectResponse
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
from routes import status_router, kits_router, players_router, stats_router, admin_router, events_router, moderation_api_router, moderation_panel_router, sub_projects_router, sub_projects_polygon_router, game_api_router, game_public_router, ratings_router, discord_bot_api_router, discord_bot_panel_router, tickets_bot_router, tickets_panel_router, logs_router, dispatch_api_router, dispatch_panel_router, replays_api_router, replays_panel_router, analytics_router, notifications_router, documents_router, onlyoffice_router, departments_router, tasks_router
from workers import start_playtime_worker
from datetime import datetime

settings = get_settings()
logger = logging.getLogger(__name__)

# Ensure fresh settings on startup (clear any cached old instances)
reset_settings_cache()
settings = get_settings()

# Jinja2 templates for global error pages
templates = Jinja2Templates(directory="templates")


def create_app() -> FastAPI:
    """Application factory"""
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
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
    
    # Setup CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=settings.cors_credentials,
        allow_methods=settings.cors_methods,
        allow_headers=settings.cors_headers,
    )

    # GZip compression for responses > 500 bytes
    app.add_middleware(GZipMiddleware, minimum_size=500)
    
    # Exception handlers
    from starlette.responses import JSONResponse as _JSONResponse

    def _is_api(request) -> bool:
        p = str(request.url.path or "")
        return p.startswith("/api/")

    def _error_response(request, status_code, title, message, details):
        """Return JSON for API clients, HTML page for browsers (with safe fallback)."""
        if _is_api(request):
            return _JSONResponse(status_code=status_code,
                                 content={"success": False, "error": message, "details": details})
        try:
            return templates.TemplateResponse(
                "error_generic.html",
                {
                    "request": request, "title": title, "message": message, "details": details,
                    "back_url": "/login" if str(request.url.path).startswith("/auth") else "/",
                    "back_text": "Вернуться",
                },
                status_code=status_code,
            )
        except Exception:
            return PlainTextResponse(message, status_code=status_code)

    @app.exception_handler(IntegrityError)
    async def integrity_exception_handler(request, exc):
        logger.error(f"Integrity error: {exc}")
        return _error_response(request, 409, "Ошибка целостности данных",
                               "Данный элемент уже существует или нарушены ограничения целостности.",
                               "integrity_error")

    @app.exception_handler(SQLAlchemyError)
    async def db_exception_handler(request, exc):
        logger.error(f"Database error: {exc}")
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
    app.include_router(events_router)
    app.include_router(moderation_api_router)
    app.include_router(moderation_panel_router)
    app.include_router(sub_projects_router)
    app.include_router(sub_projects_polygon_router)
    app.include_router(game_api_router)
    app.include_router(game_public_router)
    app.include_router(ratings_router)
    app.include_router(discord_bot_api_router)
    app.include_router(discord_bot_panel_router)
    app.include_router(tickets_bot_router)
    app.include_router(tickets_panel_router)
    app.include_router(logs_router)
    app.include_router(dispatch_api_router)
    app.include_router(dispatch_panel_router)
    app.include_router(replays_api_router)
    app.include_router(replays_panel_router)
    app.include_router(analytics_router)
    app.include_router(notifications_router)
    app.include_router(documents_router)
    app.include_router(onlyoffice_router)
    app.include_router(departments_router)
    app.include_router(tasks_router)

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
        """StaticFiles with Cache-Control headers for browser caching."""
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            # Cache static assets for 1 day in browser, 7 days with revalidation
            if response.status_code == 200:
                response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
            return response

    app.mount("/static", CachedStaticFiles(directory="static"), name="static")

    # Serve uploaded sub-project images at /sub_projects/images/
    import os
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
        app.mount("/admin/assets", _StaticFiles(directory=_spa_assets_dir), name="spa-assets")

    async def _spa_index_response():
        if os.path.isfile(_spa_index_file):
            return _FileResponse(_spa_index_file)
        raise StarletteHTTPException(status_code=503, detail="Frontend not built yet — run `npm run build` in frontend/")

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

    # Startup event
    @app.on_event("startup")
    async def startup():
        logger.info(f"Starting {settings.app_name} v{settings.app_version}")
        logger.info(f"require_auth={settings.require_auth}, api_key={settings.api_key[:4]}*** (len={len(settings.api_key)})")
        init_db()
        logger.info("Database initialized")
        app.state.start_time = datetime.utcnow()

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
        else:
            logger.info("WORKER_ROLE=web — skipping background workers/scheduler in this process")

    # HTTP exception handler to render friendly templates for common errors
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        path = str(request.url.path or "")
        if path.startswith("/api/"):
            return _JSONResponse(status_code=exc.status_code, content={"success": False, "error": str(exc.detail)})
        if exc.status_code == 404:
            return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
        if exc.status_code == 403:
            return templates.TemplateResponse("403.html", {"request": request}, status_code=403)
        if exc.status_code == 401:
            # For admin area requests, redirect unauthenticated users to the login page
            if path.startswith("/logout") or path.startswith("/auth"):
                login_url = f"/login?next={path}"
                return RedirectResponse(url=login_url, status_code=302)
            return templates.TemplateResponse("401.html", {"request": request}, status_code=401)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    # Security headers and CSRF token middleware
    import secrets

    @app.middleware("http")
    async def security_and_csrf_middleware(request, call_next):
        # The legacy Jinja2 panel is disabled — every /adminold/* request bounces
        # to the React SPA before any of its 100 route handlers ever run.
        if request.url.path.startswith("/adminold"):
            return RedirectResponse(url="/admin", status_code=302)

        # Ensure CSRF token exists in session
        try:
            if "csrf_token" not in request.session:
                request.session["csrf_token"] = secrets.token_urlsafe(16)
        except Exception:
            # Session may not be available for some requests
            pass

        response = await call_next(request)

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:; frame-src 'self' https://*.jit.si https://meet.jit.si https://*.rp-asper.ru https:"

        return response
    
    return app


app = create_app()
