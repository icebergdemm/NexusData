from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from routes import status_router, kits_router, players_router, stats_router, admin_router, events_router
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
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_credentials,
        allow_methods=settings.cors_methods,
        allow_headers=settings.cors_headers,
    )
    
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
    app.include_router(status_router)
    app.include_router(kits_router)
    app.include_router(players_router)
    app.include_router(stats_router)
    app.include_router(admin_router)
    app.include_router(events_router)

    # Serve static files from './static' at '/static'
    app.mount("/static", StaticFiles(directory="static"), name="static")
    
    # Startup event
    @app.on_event("startup")
    async def startup():
        logger.info(f"Starting {settings.app_name} v{settings.app_version}")
        logger.info(f"require_auth={settings.require_auth}, api_key={settings.api_key[:4]}*** (len={len(settings.api_key)})")
        init_db()
        logger.info("Database initialized")
        app.state.start_time = datetime.utcnow()

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
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:"

        return response
    
    return app


app = create_app()
