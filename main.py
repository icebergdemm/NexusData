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

from config import get_settings, reset_settings_cache
from database import init_db
import models  # ensure all models are registered before create_all
from routes import status_router, kits_router, players_router, stats_router, admin_router, events_router, moderation_api_router, moderation_panel_router, sub_projects_router, game_api_router, game_public_router, ratings_router, discord_bot_api_router, discord_bot_panel_router, tickets_bot_router, tickets_panel_router, logs_router, dispatch_api_router, dispatch_panel_router
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
    @app.exception_handler(IntegrityError)
    async def integrity_exception_handler(request, exc):
        logger.error(f"Integrity error: {exc}")
        return templates.TemplateResponse(
            "error_generic.html",
            {
                "request": request,
                "title": "Ошибка целостности данных",
                "message": "Данный элемент уже существует или нарушены ограничения целостности.",
                "details": "integrity_error",
                "back_url": "/login" if str(request.url.path).startswith("/auth") else "/",
                "back_text": "Вернуться",
            },
            status_code=409,
        )
    
    @app.exception_handler(SQLAlchemyError)
    async def db_exception_handler(request, exc):
        logger.error(f"Database error: {exc}")
        # Don't raise, return response directly to avoid middleware issues
        return templates.TemplateResponse(
            "error_generic.html",
            {
                "request": request,
                "title": "Ошибка базы данных",
                "message": "Не удалось выполнить операцию с базой данных. Пожалуйста, попробуйте позже.",
                "details": "database_error",
                "back_url": "/login" if str(request.url.path).startswith("/auth") else "/",
                "back_text": "Вернуться",
            },
            status_code=500,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        logger.warning(f"Validation error on {request.url.path}: {exc}")
        return templates.TemplateResponse(
            "error_generic.html",
            {
                "request": request,
                "title": "Некорректный запрос",
                "message": "Что-то пошло не так. Проверьте данные и попробуйте снова.",
                "details": "validation_error",
                "back_url": "/login" if str(request.url.path).startswith("/auth") else "/",
                "back_text": "Вернуться",
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        logger.exception(f"Unhandled error on {request.url.path}: {exc}")
        return templates.TemplateResponse(
            "error_generic.html",
            {
                "request": request,
                "title": "Внутренняя ошибка",
                "message": "Что-то пошло не так. Мы уже работаем над исправлением.",
                "details": "internal_error",
                "back_url": "/login" if str(request.url.path).startswith("/auth") else "/",
                "back_text": "Вернуться",
            },
            status_code=500,
        )
    
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
    
    # Startup event
    @app.on_event("startup")
    async def startup():
        logger.info(f"Starting {settings.app_name} v{settings.app_version}")
        logger.info(f"require_auth={settings.require_auth}, api_key={settings.api_key[:4]}*** (len={len(settings.api_key)})")
        init_db()
        logger.info("Database initialized")
        app.state.start_time = datetime.utcnow()
        start_playtime_worker()
        from rating_engine import start_rating_worker
        start_rating_worker()
        from services_logs import start_log_worker
        start_log_worker()
        from services_playtime_stats import start_playtime_stats_worker
        start_playtime_stats_worker()

    # HTTP exception handler to render friendly templates for common errors
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        if exc.status_code == 404:
            return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
        if exc.status_code == 403:
            return templates.TemplateResponse("403.html", {"request": request}, status_code=403)
        if exc.status_code == 401:
            # For admin area requests, redirect unauthenticated users to the login page
            path = str(request.url.path or "")
            if path.startswith("/admin") or path.startswith("/logout") or path.startswith("/auth"):
                login_url = f"/login?next={path}"
                return RedirectResponse(url=login_url, status_code=302)
            return templates.TemplateResponse("401.html", {"request": request}, status_code=401)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    # Security headers and CSRF token middleware
    import secrets

    @app.middleware("http")
    async def security_and_csrf_middleware(request, call_next):
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
