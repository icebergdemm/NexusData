from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session, joinedload
from typing import Optional
import secrets
import threading
import time
from datetime import datetime, timedelta
from database import get_db
from security import check_permission
from services import (
    AdminService, DiscordAuthService, PlayerService,
    PlayerKillService, PlayerRoleActionService, PlayerIPService,
    PlayerNameService, PlayerRoleStatService, PlayerSessionService,
    PlayerNoteService, PlayerLabelService, PlayerTrapService,
    LabelCategoryService, TwinkDetectionService, ServerModerationService,
    PunishmentService, PUNISHMENT_TYPES, PUNISHMENT_TYPE_LABELS,
    ModerationActionTemplateService, MODERATION_TEMPLATE_TYPES,
    MODERATION_TEMPLATE_TYPE_LABELS,
)
from models import (
    AdminUser, AdminSession, AdminActionLog, PlayerStats, PlayerData,
    LabelCategory, PlayerLabel, PlayerNote, PlayerTrap, Kit, PlayerSession,
    PlayerPunishment,
)
from config import get_settings, update_env_file
from utils import is_bogon_ip
import logging

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(max=max, min=min, check_permission=check_permission)
settings = get_settings()
logger = logging.getLogger(__name__)

discord_auth = DiscordAuthService()


def log_admin_action(db: Session, admin_id: int, action: str, details: Optional[dict] = None):
    log = AdminActionLog(admin_id=admin_id, action=action, details=details or {})
    db.add(log)
    db.commit()


def push_flash(request: Request, level: str, message: str):
    flashes = request.session.get("_flashes", [])
    flashes.append({"level": level, "message": message})
    request.session["_flashes"] = flashes


def pop_flashes(request: Request):
    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)
    return flashes


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """Get current admin strictly from server-side admin session token."""
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                request.session["username"] = admin.discord_username
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin

    raise HTTPException(status_code=401, detail="Not authenticated")


@router.get("/api/v1/admin/permissions/registry")
async def get_permissions_registry(current_admin: AdminUser = Depends(get_current_admin)):
    """Single source of truth for page/action definitions — the role editor,
    individual-permission editor and department access-level editor all
    render their checkbox grids from this instead of a hardcoded list."""
    from permissions_registry import PERMISSION_REGISTRY
    return {"pages": PERMISSION_REGISTRY}


@router.get("/api/v1/me")
async def get_me(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Identity + permissions for the React SPA — mirrors what base.html's
    per-nav-item check_permission() calls do for the Jinja2 admin panel."""
    from permissions_registry import PERMISSION_REGISTRY
    permissions = {
        page: {
            action["key"]: check_permission(current_admin, page, action["key"])
            for action in page_def["actions"]
        }
        for page, page_def in PERMISSION_REGISTRY.items()
    }

    from models import Department, DepartmentMember
    from routes.departments import get_member
    from security import check_department_permission
    _my_depts = (
        db.query(Department)
        .join(DepartmentMember, DepartmentMember.department_id == Department.id)
        .filter(DepartmentMember.admin_id == current_admin.id, Department.is_archived.is_(False))
        .order_by(Department.name)
        .all()
    )
    my_departments = []
    for d in _my_depts:
        member = get_member(db, d.id, current_admin.id)
        my_departments.append({
            "id": d.id, "name": d.name, "icon": d.icon, "color": d.color,
            # Drives whether the SPA shows the "Тестовый полигон ASPER" nav
            # item — Polygon access runs entirely through this department
            # module, never through the site-wide sub_projects permissions.
            "test_server_view": check_department_permission(current_admin, d, member, "test_server", "view"),
            "test_server_edit": check_department_permission(current_admin, d, member, "test_server", "edit"),
        })

    linked_player_row = db.query(PlayerData).filter(PlayerData.linked_admin_id == current_admin.id).first()
    linked_player = (
        {"player_id": linked_player_row.player_id, "forced_display_name": linked_player_row.forced_display_name}
        if linked_player_row else None
    )

    return {
        "id": current_admin.id,
        "discord_username": current_admin.discord_username,
        "discord_avatar": current_admin.discord_avatar,
        "is_superuser": bool(current_admin.is_superuser),
        "permissions": permissions,
        "my_departments": my_departments,
        "linked_player": linked_player,
        "csrf_token": request.session.get("csrf_token"),
    }


@router.get("/api/v1/me/player-lookup")
async def api_me_player_lookup(steamid: str = Query(...), db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Self-service SteamID64 lookup for the login onboarding flow (item 3) —
    any authenticated admin may look up a player by SteamID to confirm their
    own identity before linking. Never exposes other players' data beyond
    the last known in-game name."""
    steamid = (steamid or "").strip()
    if not steamid:
        raise HTTPException(status_code=400, detail="Укажите SteamID64")
    player = db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
    if not player:
        raise HTTPException(status_code=404, detail="Профиль с таким SteamID не найден")
    if player.linked_admin_id is not None and player.linked_admin_id != current_admin.id:
        raise HTTPException(status_code=409, detail="Этот профиль уже привязан к другому пользователю")
    return {"player_id": player.player_id, "name": player.player_last_time_name}


@router.post("/api/v1/me/link-player")
async def api_me_link_player(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Self-confirmed player link (item 3 — "Да, это я"). Re-validates
    server-side rather than trusting the client's earlier lookup response."""
    body = await request.json()
    player_id = body.get("player_id")
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    if player.linked_admin_id is not None and player.linked_admin_id != current_admin.id:
        raise HTTPException(status_code=409, detail="Этот профиль уже привязан к другому пользователю")
    player.linked_admin_id = current_admin.id
    # Self-confirmed (the user explicitly clicked "Да, это я") — same trust
    # tier as a superuser-made link, not the unconfirmed background match.
    player.linked_admin_auto = False
    db.commit()
    return {"status": "ok", "player_id": player.player_id}


@router.post("/api/v1/me/forced-nickname")
async def api_me_set_forced_nickname(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Self-service "Принудительный ник" set from the login onboarding
    modal (items 2 & 3)."""
    player = db.query(PlayerData).filter(PlayerData.linked_admin_id == current_admin.id).first()
    if not player:
        raise HTTPException(status_code=400, detail="Сначала привяжите игровой профиль")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите имя")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="Слишком длинное имя")
    player.forced_display_name = name
    db.commit()
    return {"status": "ok"}


async def ensure_csrf_or_valid_admin_session(request: Request, db: Session):
    """Validate CSRF token with a safe fallback to a valid server-side admin session.

    Primary protection is still CSRF token equality.
    Fallback is used only when a valid `admin_token` cookie maps to an active DB session.
    """
    form = await request.form()
    csrf_token = form.get("csrf_token")
    session_csrf = request.session.get("csrf_token")

    if csrf_token and session_csrf and secrets.compare_digest(str(csrf_token), str(session_csrf)):
        return

    token = request.cookies.get("admin_token")
    if token:
        now = datetime.utcnow()
        admin_session = db.query(AdminSession).filter(
            AdminSession.token == token,
            AdminSession.revoked == 0
        ).first()
        if admin_session and (admin_session.expires_at is None or admin_session.expires_at > now):
            logger.warning("CSRF mismatch accepted via valid admin session fallback for %s", request.url.path)
            return

    raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _safe_next_path(next_url: Optional[str], default: str = "/admin") -> str:
    """Only allow same-origin relative paths as a post-login redirect target."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return default
    return next_url


def _oauth_allowed_hosts() -> set:
    """Every hostname the site is reachable on and allowed to complete a
    Discord login round-trip through — the primary one baked into
    discord_redirect_uri, plus any extras from settings.extra_allowed_hosts.
    """
    from urllib.parse import urlparse
    primary = (urlparse(settings.discord_redirect_uri).hostname or "").lower()
    return {h.lower() for h in ([primary] + settings.extra_allowed_hosts_list) if h}


def _resolve_oauth_redirect_uri(request: Request) -> str:
    """Multiple entry-point domains (e.g. a second hostname reachable
    directly, bypassing the main proxy) each need their OWN
    /auth/discord/callback round-trip: Discord validates redirect_uri
    byte-for-byte against what's registered in the Developer Portal, so a
    login started on host A must also come back to host A, never a fixed
    default — otherwise Discord rejects it outright, or (if the default
    domain happens to also be registered) the session cookie ends up set on
    the WRONG host and the user looks logged-out back on the domain they
    actually started from. Falls back to the configured default if the
    request's own Host header isn't in the allowed set (unregistered/unknown
    hosts would fail at Discord's side anyway, since only registered exact
    URIs work — this is just about picking the right ALREADY-registered one)."""
    host = (request.url.hostname or "").lower()
    if host in _oauth_allowed_hosts():
        return f"{request.url.scheme}://{request.url.netloc}/auth/discord/callback"
    return settings.discord_redirect_uri


# ══════════════════════════════════════════════════════════════════════════
# Вход по ключу (/slogin) — запасной путь, когда Discord недоступен
# ══════════════════════════════════════════════════════════════════════════

def _issue_admin_session(db: Session, admin: AdminUser, response) -> None:
    """Создаёт серверную сессию и ставит куку — та же схема, что у входа
    через Discord (одна таблица сессий, один вид куки), чтобы способ входа
    дальше нигде не приходилось различать."""
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = now + timedelta(seconds=settings.session_max_age)
    db.add(AdminSession(admin_id=admin.id, token=token, created_at=now, expires_at=expires, revoked=0))
    admin.last_login = now
    db.commit()
    response.set_cookie(
        "admin_token", token,
        httponly=True, max_age=settings.session_max_age,
        samesite="lax", secure=not settings.debug,
    )


@router.get("/slogin", response_class=HTMLResponse)
async def key_login_page(request: Request, next: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Страница входа по ключу. Отдельный адрес, а не кнопка на /login:
    основной способ входа должен оставаться единственным очевидным, а этот —
    для тех, кому его выдали."""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    next_path = _safe_next_path(next)

    token = request.cookies.get("admin_token")
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
        if session and (session.expires_at is None or session.expires_at > datetime.utcnow()):
            return RedirectResponse(url=next_path, status_code=302)

    return templates.TemplateResponse("slogin.html", {
        "request": request,
        "csrf_token": request.session["csrf_token"],
        "next_path": next_path,
        "error": request.query_params.get("error"),
    })


@router.post("/slogin")
async def key_login_submit(
    request: Request,
    key: str = Form(""),
    next: str = Form("/admin"),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    import services_login_keys

    next_path = _safe_next_path(next)
    if not secrets.compare_digest(str(csrf_token or ""), str(request.session.get("csrf_token") or "")):
        return RedirectResponse(url="/slogin?error=csrf", status_code=302)

    client_ip = request.client.host if request.client else None
    if not services_login_keys.rate_limit_ok(client_ip):
        logger.warning(f"Слишком много попыток входа по ключу с {client_ip}")
        return RedirectResponse(url="/slogin?error=throttled", status_code=302)

    admin = services_login_keys.resolve_admin(db, key, client_ip)
    if not admin:
        logger.warning(f"Неудачная попытка входа по ключу с {client_ip}")
        return RedirectResponse(url="/slogin?error=invalid", status_code=302)

    response = RedirectResponse(url=next_path, status_code=302)
    _issue_admin_session(db, admin, response)
    request.session["username"] = admin.discord_username
    log_admin_action(db, admin.id, "login", {"method": "login_key", "ip": client_ip})
    logger.info(f"Вход по ключу: {admin.discord_username} (id={admin.id}) с {client_ip}")
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Discord login page"""
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    next_path = _safe_next_path(next)

    # OAuth CSRF (login-CSRF) protection: the `state` param carries a random
    # nonce we stash in the session and verify on the callback, NOT the raw
    # next-path (which an attacker could set). The desired post-login
    # destination is kept server-side in the session instead. SameSite=Lax
    # doesn't protect the OAuth callback (it's a top-level GET), so this nonce
    # is the actual guard against being logged into an account you didn't pick.
    oauth_state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = oauth_state
    request.session["oauth_next"] = next_path

    # Pinned to whichever entry-point domain this request actually came in
    # on (see _resolve_oauth_redirect_uri) — the callback must reuse this
    # SAME value, stashed here since it round-trips through the session.
    redirect_uri = _resolve_oauth_redirect_uri(request)
    request.session["oauth_redirect_uri"] = redirect_uri

    # Redirect only if server-side admin session exists and is valid.
    token = request.cookies.get("admin_token")
    if token:
        now = datetime.utcnow()
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            return RedirectResponse(url=next_path, status_code=302)

        # Stale/invalid token cookie: clear it to prevent redirect loops.
        request.session.pop("username", None)
        response = templates.TemplateResponse("login.html", {"request": request, "oauth_url": discord_auth.get_oauth_url(state=oauth_state, redirect_uri=redirect_uri)})
        response.delete_cookie("admin_token")
        return response

    oauth_url = discord_auth.get_oauth_url(state=oauth_state, redirect_uri=redirect_uri)
    return templates.TemplateResponse("login.html", {"request": request, "oauth_url": oauth_url})


@router.get("/auth/discord/callback")
async def discord_callback(
    request: Request,
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Discord OAuth callback"""
    if error:
        logger.info(f"Discord OAuth denied/canceled: error={error}, description={error_description}")
        return templates.TemplateResponse(
            "error_generic.html",
            {
                "request": request,
                "title": "Авторизация отменена",
                "message": "Что-то пошло не так. Авторизация через Discord была отменена или отклонена.",
                "details": error_description or error,
                "back_url": "/login",
                "back_text": "Вернуться к входу",
            },
            status_code=400,
        )

    if not code:
        logger.warning("Discord callback without code and without explicit error")
        return templates.TemplateResponse(
            "error_generic.html",
            {
                "request": request,
                "title": "Ошибка авторизации",
                "message": "Что-то пошло не так. Сервер не получил код авторизации от Discord.",
                "details": "missing_code",
                "back_url": "/login",
                "back_text": "Попробовать снова",
            },
            status_code=400,
        )

    # Verify the OAuth state nonce set by login_page (login-CSRF protection).
    # Consume it from the session regardless of outcome so it can't be replayed.
    expected_state = request.session.pop("oauth_state", None)
    next_path = _safe_next_path(request.session.pop("oauth_next", None))
    # Same redirect_uri login_page used when it sent the user to Discord —
    # must match exactly or Discord rejects the exchange (see
    # _resolve_oauth_redirect_uri). Falls back to resolving it fresh from
    # this request if the session value is somehow missing (e.g. an old
    # session predating this field) rather than failing outright.
    redirect_uri = request.session.pop("oauth_redirect_uri", None) or _resolve_oauth_redirect_uri(request)
    if not expected_state or not state or not secrets.compare_digest(str(state), str(expected_state)):
        logger.warning("Discord callback with invalid/missing OAuth state — possible CSRF or expired login")
        return RedirectResponse(url="/login?error=state_mismatch", status_code=302)

    try:
        # Exchange code for token
        token_data = await discord_auth.exchange_code(code, redirect_uri=redirect_uri)
        access_token = token_data["access_token"]
        
        # Get user info
        user_info = await discord_auth.get_user_info(access_token)
        discord_id = user_info["id"]
        
        # Check if admin exists — do NOT create admins automatically
        admin_service = AdminService(db)
        admin = admin_service.get_admin_by_discord_id(discord_id)

        if not admin:
            logger.warning(f"Unauthorized login attempt for Discord ID {discord_id}")
            return RedirectResponse(url="/login?error=not_authorized", status_code=302)

        # Block deleted admins
        if getattr(admin, 'is_deleted', 0) == 1:
            logger.warning(f"Deleted admin login attempt: Discord ID {discord_id}")
            return RedirectResponse(url="/login?error=not_authorized", status_code=302)

        # Refresh Discord display name (global_name = display name, username = unique handle)
        fresh_display_name = user_info.get("global_name") or user_info.get("username", "")
        fresh_avatar = user_info.get("avatar")
        if admin.discord_username != fresh_display_name or admin.discord_avatar != fresh_avatar:
            admin.discord_username = fresh_display_name
            admin.discord_avatar = fresh_avatar

        # Update last login
        admin.last_login = datetime.utcnow()
        db.commit()

        # Create server-side admin session and set cookie
        token = secrets.token_urlsafe(32)
        now = datetime.utcnow()
        expires = now + timedelta(seconds=settings.session_max_age)
        admin_session = AdminSession(admin_id=admin.id, token=token, created_at=now, expires_at=expires, revoked=0)
        db.add(admin_session)
        db.commit()

        response = RedirectResponse(url=next_path, status_code=302)
        response.set_cookie(
            "admin_token",
            token,
            httponly=True,
            max_age=settings.session_max_age,
            samesite="lax",
            secure=not settings.debug,
        )
        # Session keeps only non-authoritative display data; auth is cookie+DB session.
        request.session["username"] = fresh_display_name or admin.discord_username

        # Log action
        log_admin_action(db, admin.id, "login", {"method": "discord_oauth"})

        return response
        
    except Exception as e:
        logger.exception(f"Discord auth error: {e}")
        return templates.TemplateResponse(
            "error_generic.html",
            {
                "request": request,
                "title": "Ошибка авторизации",
                "message": "Что-то пошло не так при обработке входа через Discord.",
                "details": "auth_failed",
                "back_url": "/login",
                "back_text": "Попробовать снова",
            },
            status_code=500,
        )


@router.post("/logout")
async def logout(request: Request, csrf_token: Optional[str] = Form(None), db: Session = Depends(get_db)):
    """Logout (POST) - revoke server-side session if exists.

    CSRF is required normally, but allow a safe fallback: if the `admin_token` cookie
    is present and corresponds to a valid server-side session, allow logout without CSRF.
    This makes logout more reliable when session cookies or CSRF fields are missing
    (e.g., cross-origin or client issues) while keeping other POST endpoints protected.
    """
    # Verify CSRF first
    session_csrf = request.session.get("csrf_token")
    csrf_valid = session_csrf and csrf_token and session_csrf == csrf_token

    token = request.cookies.get("admin_token")

    # If CSRF invalid, allow fallback only when admin_token cookie is present and valid
    if not csrf_valid:
        if not token:
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

        s_check = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if not s_check:
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

    # At this point either CSRF is valid or we have a valid admin_token session
    if token:
        s = db.query(AdminSession).filter(AdminSession.token == token).first()
        if s:
            s.revoked = 1
            db.commit()
            # log
            log_admin_action(db, s.admin_id, "logout", {"revoked": True})

    request.session.clear()
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("admin_token")
    return response


_DASHBOARD_CACHE_TTL_SECONDS = 30.0
_dashboard_cache: Optional[dict] = None
_dashboard_cache_at = 0.0
_dashboard_cache_lock = threading.Lock()


def _compute_dashboard_chart(db: Session) -> dict:
    """The actual heavy lifting behind /adminold — a full 24h session scan
    plus a 24-slot x 6-sample nested Python loop. Deliberately a plain
    (non-async) function so it can run via run_in_threadpool (see caller)
    instead of blocking the shared event loop for its whole duration, and
    its result is cached for _DASHBOARD_CACHE_TTL_SECONDS since this is the
    single most-visited admin page and the underlying data doesn't need
    sub-30s freshness."""
    # Use Moscow time for chart
    from datetime import timezone
    MSK = timezone(timedelta(hours=3))
    now_msk = datetime.now(tz=MSK).replace(tzinfo=None)
    since = now_msk - timedelta(days=1)

    # Sessions use UTC in DB, convert boundaries to UTC for query.
    # Only load the columns we need (not full ORM objects) to cut memory.
    utc_since = since - timedelta(hours=3)
    sessions = db.query(
        PlayerSession.login_time, PlayerSession.logout_time, PlayerSession.player_id
    ).filter(PlayerSession.login_time >= utc_since).all()

    # Build 24-slot chart (1h each) in MSK
    chart_labels = []
    peak_values = []
    avg_values = []

    slot_delta = timedelta(hours=1)
    for i in range(24):
        sl_start = since + slot_delta * i
        sl_end = sl_start + slot_delta
        # Convert slot boundaries to UTC for comparison with DB times
        sl_start_utc = sl_start - timedelta(hours=3)
        sl_end_utc = sl_end - timedelta(hours=3)
        active = [
            s for s in sessions
            if s.login_time < sl_end_utc and (s.logout_time is None or s.logout_time > sl_start_utc)
        ]
        chart_labels.append(sl_start.strftime("%H:%M"))
        if not active:
            peak_values.append(0); avg_values.append(0)
            continue
        samples = []
        for j in range(6):
            t = sl_start_utc + timedelta(minutes=10 * j)
            cnt = sum(1 for s in active if s.login_time <= t and (s.logout_time is None or s.logout_time >= t))
            samples.append(cnt)
        peak_values.append(max(samples))
        avg_values.append(round(sum(samples) / len(samples), 1))

    unique_today = len({s.player_id for s in sessions})
    total_players = db.query(PlayerData).count()

    return {
        "chart_labels": chart_labels,
        "peak_values": peak_values,
        "avg_values": avg_values,
        "unique_today": unique_today,
        "total_players": total_players,
    }


def _cached_dashboard_chart(db: Session) -> dict:
    global _dashboard_cache, _dashboard_cache_at
    now = time.monotonic()
    with _dashboard_cache_lock:
        if _dashboard_cache is not None and (now - _dashboard_cache_at) < _DASHBOARD_CACHE_TTL_SECONDS:
            return _dashboard_cache
        data = _compute_dashboard_chart(db)
        _dashboard_cache = data
        _dashboard_cache_at = now
        return data


@router.get("/adminold", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Admin dashboard with online chart"""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    # Off the event loop (run_in_threadpool) — this used to run the full
    # scan + nested-loop computation inline in this async def, blocking the
    # shared event loop (and therefore every other request, including
    # game-server ones dispatched to the same loop) for its whole duration.
    chart_data = await run_in_threadpool(_cached_dashboard_chart, db)

    # Current online (combined from all servers, pruned of stale entries) —
    # in-memory only, cheap, deliberately NOT part of the cached snapshot
    # above so it's always fresh.
    from services import _online_players, _online_players_lock, _prune_stale_online
    with _online_players_lock:
        _prune_stale_online()
        current_online = len(_online_players)

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "admin": current_admin,
        "flashes": pop_flashes(request),
        "current_online": current_online,
        **chart_data,
    })


@router.get("/adminold/users", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Combined users + privilege groups page"""
    if not check_permission(current_admin, "admin", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    admin_service = AdminService(db)
    admins = admin_service.get_all_admins()

    from models import AdminPrivilegeGroup
    from sqlalchemy.orm import joinedload
    groups = db.query(AdminPrivilegeGroup).order_by(AdminPrivilegeGroup.id).all()
    groups_data = []
    for g in groups:
        members = db.query(AdminUser).filter(AdminUser.privilege_group_id == g.id).count()
        groups_data.append({"group": g, "member_count": members})

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "admin": current_admin,
        "admins": admins,
        "roles": list(settings.roles_mapping.keys()),
        "groups_data": groups_data,
        "flashes": pop_flashes(request),
    })


def _serialize_admin_user(a: AdminUser, linked_player=None) -> dict:
    return {
        "id": a.id, "discord_id": a.discord_id, "discord_username": a.discord_username,
        "discord_avatar": a.discord_avatar, "role": a.role, "is_superuser": bool(a.is_superuser),
        "privilege_group_id": a.privilege_group_id,
        "privilege_group_name": a.privilege_group.name if a.privilege_group else None,
        "individual_permissions": a.individual_permissions,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "last_login": a.last_login.isoformat() if a.last_login else None,
        "linked_player": (
            {
                "player_id": linked_player.player_id,
                "player_name": linked_player.forced_display_name or linked_player.player_last_time_name,
                "auto": bool(linked_player.linked_admin_auto),
            }
            if linked_player else None
        ),
    }


def _serialize_privilege_group(g, member_count: int) -> dict:
    return {
        "id": g.id, "name": g.name, "description": g.description, "permissions": g.permissions,
        "rate_limit_count": g.rate_limit_count, "rate_limit_window_secs": g.rate_limit_window_secs,
        "member_count": member_count,
    }


@router.get("/api/v1/admin/users")
async def api_admin_users_list(db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "admin", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    admin_service = AdminService(db)
    admins = admin_service.get_all_admins()

    from models import AdminPrivilegeGroup
    groups = db.query(AdminPrivilegeGroup).order_by(AdminPrivilegeGroup.id).all()
    groups_data = [_serialize_privilege_group(g, db.query(AdminUser).filter(AdminUser.privilege_group_id == g.id).count()) for g in groups]

    linked_players = db.query(PlayerData).filter(PlayerData.linked_admin_id.in_([a.id for a in admins])).all()
    linked_by_admin_id = {p.linked_admin_id: p for p in linked_players}

    return {
        "admins": [_serialize_admin_user(a, linked_by_admin_id.get(a.id)) for a in admins],
        "roles": list(settings.roles_mapping.keys()),
        "groups": groups_data,
        "can_edit": check_permission(current_admin, "admin", "edit"),
        "can_delete": check_permission(current_admin, "admin", "delete"),
        "is_superuser": current_admin.is_superuser,
    }


@router.post("/api/v1/admin/users")
async def api_create_admin_user(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    body = await request.json()
    discord_id = (body.get("discord_id") or "").strip()
    role = body.get("role") or "admin"
    if not discord_id:
        raise HTTPException(status_code=400, detail="discord_id required")

    admin_service = AdminService(db)
    existing = admin_service.get_admin_by_discord_id(discord_id)
    if existing:
        if not existing.is_deleted:
            raise HTTPException(status_code=409, detail="Администратор с таким Discord ID уже существует")
        # Previously soft-deleted — restore the account instead of failing.
        # Privileges stay wiped (delete_admin cleared them on delete); an
        # admin has to re-grant a group/permissions explicitly.
        existing.is_deleted = 0
        existing.role = role
        db.commit()
        log_admin_action(db, current_admin.id, "restore_admin", {
            "target": {"admin_id": existing.id, "discord_id": existing.discord_id, "username": existing.discord_username},
        })
        return {"status": "ok", "admin": _serialize_admin_user(existing)}

    admin = AdminUser(discord_id=discord_id, discord_username=f"User_{discord_id}", role=role)
    db.add(admin)
    db.commit()
    log_admin_action(db, current_admin.id, "create_admin", {
        "target": {"admin_id": admin.id, "discord_id": admin.discord_id, "username": admin.discord_username},
        "changes": [{"field": "role", "from": None, "to": role}],
    })
    return {"status": "ok", "admin": _serialize_admin_user(admin)}


@router.post("/api/v1/admin/users/{admin_id}")
async def api_update_admin_user(admin_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    body = await request.json()
    role = body.get("role")

    target_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if not target_admin:
        raise HTTPException(status_code=404, detail="Администратор не найден")

    old_role = target_admin.role
    if role and role != old_role:
        AdminService(db).update_admin_role(admin_id, role)
        log_admin_action(db, current_admin.id, "update_admin_role", {
            "target": {"admin_id": target_admin.id, "discord_id": target_admin.discord_id, "username": target_admin.discord_username},
            "changes": [{"field": "role", "from": old_role, "to": role}],
        })
    return {"status": "ok"}


@router.delete("/api/v1/admin/users/{admin_id}")
async def api_delete_admin_user(admin_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "admin", "delete"):
        raise HTTPException(status_code=403)
    if current_admin.id == admin_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить собственную учетную запись")

    target_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if not target_admin:
        raise HTTPException(status_code=404, detail="Администратор не найден")
    if target_admin.is_superuser and not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может удалить суперпользователя")

    AdminService(db).delete_admin(admin_id)
    log_admin_action(db, current_admin.id, "delete_admin", {
        "target": {"admin_id": target_admin.id, "discord_id": target_admin.discord_id, "username": target_admin.discord_username},
    })
    return {"status": "ok"}


# ── Ключи входа без Discord (/slogin) ────────────────────────────────────
# Только суперпользователь: ключ — это полноценный вход в чужой аккаунт со
# всеми его правами, и раздача такого не должна зависеть от того, кому выдали
# admin:edit.

def _require_superuser(admin: AdminUser):
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")


@router.get("/api/v1/admin/users/{admin_id}/login-keys")
async def api_list_login_keys(
    admin_id: int, db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    import services_login_keys
    from models import AdminLoginKey

    _require_superuser(current_admin)
    rows = (
        db.query(AdminLoginKey)
        .options(joinedload(AdminLoginKey.created_by))
        .filter(AdminLoginKey.admin_id == admin_id)
        .order_by(AdminLoginKey.created_at.desc())
        .all()
    )
    return {"keys": [services_login_keys.serialize(r) for r in rows]}


@router.post("/api/v1/admin/users/{admin_id}/login-keys")
async def api_create_login_key(
    admin_id: int, request: Request, db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Создаёт ключ. Сам ключ возвращается ЕДИНСТВЕННЫЙ раз — в базе только
    хеш, восстановить его потом нельзя даже суперпользователю."""
    import services_login_keys

    _require_superuser(current_admin)
    await ensure_csrf_or_valid_admin_session(request, db)

    target = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if getattr(target, "is_deleted", 0) == 1:
        raise HTTPException(status_code=400, detail="Пользователь удалён — ключ ему не нужен")

    body = await request.json() if await request.body() else {}
    expires_days = body.get("expires_days")
    if expires_days is not None and (not isinstance(expires_days, int) or expires_days < 0):
        raise HTTPException(status_code=400, detail="Срок действия — целое число дней")

    row, raw_key = services_login_keys.create_key(
        db, admin_id, current_admin.id,
        label=body.get("label"), expires_days=expires_days or None,
    )
    log_admin_action(db, current_admin.id, "login_key_create",
                     {"target_admin_id": admin_id, "key_id": row.id})
    return {"key": services_login_keys.serialize(row), "secret": raw_key}


@router.delete("/api/v1/admin/users/{admin_id}/login-keys/{key_id}")
async def api_revoke_login_key(
    admin_id: int, key_id: int, db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Отзыв, а не удаление: строка остаётся, чтобы в аудите было видно, что
    ключ существовал и когда им пользовались в последний раз."""
    import services_login_keys
    from models import AdminLoginKey

    _require_superuser(current_admin)
    row = db.query(AdminLoginKey).filter(
        AdminLoginKey.id == key_id, AdminLoginKey.admin_id == admin_id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Ключ не найден")

    row.revoked = True
    db.commit()
    log_admin_action(db, current_admin.id, "login_key_revoke",
                     {"target_admin_id": admin_id, "key_id": key_id})
    return {"key": services_login_keys.serialize(row)}


@router.post("/api/v1/admin/users/{user_id}/assign-group")
async def api_admin_user_assign_group(user_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    from security import enforce_rate_limit
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    enforce_rate_limit(db, current_admin, "user_assign_group")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.is_superuser and not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Нельзя изменить суперпользователя")

    body = await request.json()
    # Only touch fields the caller actually sent — previously this always
    # overwrote individual_permissions (defaulting to None), so switching an
    # admin's group or toggling superuser from the React UI (which never
    # sends individual_permissions) silently wiped any individual override.
    if "privilege_group_id" in body:
        target.privilege_group_id = body.get("privilege_group_id") or None
    if "individual_permissions" in body:
        target.individual_permissions = body.get("individual_permissions") or None
    if current_admin.is_superuser and "is_superuser" in body:
        target.is_superuser = bool(body.get("is_superuser"))

    db.commit()
    log_admin_action(db, current_admin.id, "user_assign_group", {"target_id": user_id, "group_id": target.privilege_group_id})
    return {"status": "ok", "admin": _serialize_admin_user(target)}


@router.post("/api/v1/admin/privilege-groups")
async def api_privilege_group_create(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    from security import enforce_rate_limit
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    enforce_rate_limit(db, current_admin, "group_create")

    from models import AdminPrivilegeGroup
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название группы не может быть пустым")
    if db.query(AdminPrivilegeGroup).filter(AdminPrivilegeGroup.name == name).first():
        raise HTTPException(status_code=409, detail=f"Группа '{name}' уже существует")

    grp = AdminPrivilegeGroup(
        name=name, description=(body.get("description") or "").strip() or None,
        permissions=body.get("permissions") or {},
        rate_limit_count=int(body.get("rate_limit_count") or 0),
        rate_limit_window_secs=int(body.get("rate_limit_window_secs") or 3600),
    )
    db.add(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "privilege_group_create", {"name": name})
    return {"status": "ok", "group": _serialize_privilege_group(grp, 0)}


@router.post("/api/v1/admin/privilege-groups/{group_id}")
async def api_privilege_group_edit(group_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    from security import enforce_rate_limit
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    enforce_rate_limit(db, current_admin, "group_edit")

    from models import AdminPrivilegeGroup
    grp = db.query(AdminPrivilegeGroup).filter(AdminPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    body = await request.json()
    name = (body.get("name") or "").strip()
    if name:
        grp.name = name
    grp.description = (body.get("description") or "").strip() or None
    if "permissions" in body:
        grp.permissions = body.get("permissions") or {}
    grp.rate_limit_count = int(body.get("rate_limit_count") or 0)
    grp.rate_limit_window_secs = int(body.get("rate_limit_window_secs") or 3600)
    db.commit()
    log_admin_action(db, current_admin.id, "privilege_group_edit", {"id": group_id, "name": grp.name})
    return {"status": "ok"}


@router.delete("/api/v1/admin/privilege-groups/{group_id}")
async def api_privilege_group_delete(group_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)

    from models import AdminPrivilegeGroup
    grp = db.query(AdminPrivilegeGroup).filter(AdminPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    db.query(AdminUser).filter(AdminUser.privilege_group_id == group_id).update({"privilege_group_id": None})
    db.delete(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "privilege_group_delete", {"id": group_id, "name": grp.name})
    return {"status": "ok"}


@router.post("/adminold/users")
async def create_admin_user(
    request: Request,
    discord_id: str = Form(...),
    role: str = Form("admin"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Create new admin user (manual)"""
    if not check_permission(current_admin, "admin", "edit"):
        push_flash(request, "error", "Недостаточно прав для создания администратора")
        return RedirectResponse(url="/adminold/users", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        admin_service = AdminService(db)

        # Check if already exists
        existing = admin_service.get_admin_by_discord_id(discord_id)
        if existing:
            push_flash(request, "error", "Администратор с таким Discord ID уже существует")
            return RedirectResponse(url="/adminold/users", status_code=302)

        # Create with minimal info (no flat permissions; will use privilege_group only)
        admin = AdminUser(
            discord_id=discord_id,
            discord_username=f"User_{discord_id}",
            role=role
        )

        db.add(admin)
        db.commit()
        # log action
        log_admin_action(
            db,
            current_admin.id,
            "create_admin",
            {
                "target": {
                    "admin_id": admin.id,
                    "discord_id": admin.discord_id,
                    "username": admin.discord_username,
                },
                "changes": [
                    {"field": "role", "from": None, "to": role}
                ],
            },
        )
        push_flash(request, "success", "Администратор успешно создан")

    except HTTPException as exc:
        push_flash(request, "error", f"Не удалось создать администратора: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Create admin failed: %s", exc)
        push_flash(request, "error", "Не удалось создать администратора из-за внутренней ошибки")

    return RedirectResponse(url="/adminold/users", status_code=302)


@router.post("/adminold/users/{admin_id}/update")
async def update_admin_user(
    request: Request,
    admin_id: int,
    role: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update admin user"""
    if not check_permission(current_admin, "admin", "edit"):
        push_flash(request, "error", "Недостаточно прав для изменения администратора")
        return RedirectResponse(url="/adminold/users", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        target_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not target_admin:
            push_flash(request, "error", "Администратор не найден")
            return RedirectResponse(url="/adminold/users", status_code=302)

        old_role = target_admin.role
        if old_role == role:
            push_flash(request, "info", "Роль не изменилась")
            return RedirectResponse(url="/adminold/users", status_code=302)

        admin_service = AdminService(db)
        admin_service.update_admin_role(admin_id, role)
        log_admin_action(
            db,
            current_admin.id,
            "update_admin_role",
            {
                "target": {
                    "admin_id": target_admin.id,
                    "discord_id": target_admin.discord_id,
                    "username": target_admin.discord_username,
                },
                "changes": [
                    {"field": "role", "from": old_role, "to": role}
                ],
            },
        )
        push_flash(request, "success", "Роль администратора обновлена")
    except HTTPException as exc:
        push_flash(request, "error", f"Не удалось обновить роль: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Update admin role failed: %s", exc)
        push_flash(request, "error", "Не удалось обновить роль из-за внутренней ошибки")
    
    return RedirectResponse(url="/adminold/users", status_code=302)


@router.post("/adminold/users/{admin_id}/delete")
async def delete_admin_user(
    request: Request,
    admin_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Delete admin user"""
    if not check_permission(current_admin, "admin", "delete"):
        push_flash(request, "error", "Недостаточно прав для удаления администратора")
        return RedirectResponse(url="/adminold/users", status_code=302)

    try:
        if current_admin.id == admin_id:
            push_flash(request, "error", "Нельзя удалить собственную учетную запись")
            return RedirectResponse(url="/adminold/users", status_code=302)

        await ensure_csrf_or_valid_admin_session(request, db)

        target_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not target_admin:
            push_flash(request, "error", "Администратор не найден")
            return RedirectResponse(url="/adminold/users", status_code=302)

        # Guard: only superuser can delete another superuser
        if target_admin.is_superuser and not current_admin.is_superuser:
            push_flash(request, "error", "Только суперпользователь может удалить суперпользователя")
            return RedirectResponse(url="/adminold/users", status_code=302)

        admin_service = AdminService(db)
        admin_service.delete_admin(admin_id)
        log_admin_action(
            db,
            current_admin.id,
            "delete_admin",
            {
                "target": {
                    "admin_id": target_admin.id,
                    "discord_id": target_admin.discord_id,
                    "username": target_admin.discord_username,
                }
            },
        )
        push_flash(request, "success", "Администратор удален")
    except HTTPException as exc:
        push_flash(request, "error", f"Не удалось удалить администратора: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Delete admin failed: %s", exc)
        push_flash(request, "error", "Не удалось удалить администратора из-за внутренней ошибки")
    
    return RedirectResponse(url="/adminold/users", status_code=302)


@router.get("/adminold/players", response_class=HTMLResponse)
async def admin_players_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None, min_length=2),
    label_id: Optional[int] = Query(None, description="Фильтр по категории метки"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """List players with optional search and label filter"""
    if not (check_permission(current_admin, "players", "view") or check_permission(current_admin, "players_special", "view")):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    player_service = PlayerService(db)

    # Load all label categories for filter UI
    from models import LabelCategory, PlayerLabel
    from services import LabelCategoryService
    label_categories = LabelCategoryService(db).get_all()
    selected_label = None
    if label_id:
        selected_label = db.query(LabelCategory).filter(LabelCategory.id == label_id).first()

    # Label filter: join directly instead of materializing all ids + unbounded IN()
    if label_id:
        from models import PlayerData as PD
        from sqlalchemy import or_
        q = db.query(PD).join(PlayerLabel, PlayerLabel.player_id == PD.player_id).filter(
            PlayerLabel.category_id == label_id
        )
        if search:
            q = q.filter(
                or_(
                    PD.player_last_time_name.ilike(f"%{search}%"),
                    PD.player_steamid.ilike(f"%{search}%"),
                )
            )
        total = q.count()
        raw = q.offset((page - 1) * page_size).limit(page_size).all()
        from schemas import PlayerDataOut
        players = [PlayerDataOut.from_orm_with_mappings(p, settings.ranks_mapping, settings.kits_mapping) for p in raw]
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        search_query = search
    elif search:
        from models import PlayerName, PlayerIP
        from sqlalchemy import or_
        # search_players returns up to `limit` ranked matches; we paginate that list.
        # Cap the candidate set, then slice the requested page correctly.
        player_results = player_service.search_players(search, limit=1000)
        total = len(player_results)
        start = (page - 1) * page_size
        players = player_results[start:start + page_size]
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        search_query = search
    else:
        players, total = player_service.get_all_players(page, page_size)
        total_pages = (total + page_size - 1) // page_size
        search_query = None

    return templates.TemplateResponse("admin/players.html", {
        "request": request,
        "admin": current_admin,
        "players": players,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "search_query": search_query,
        "label_categories": label_categories,
        "selected_label_id": label_id,
        "selected_label": selected_label,
        "flashes": pop_flashes(request),
    })


@router.get("/adminold/players/{player_id}", response_class=HTMLResponse)
async def admin_player_detail(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Player detail page with extended data"""
    # Only users with read or write permissions can view players
    if not (check_permission(current_admin, "players", "view") or check_permission(current_admin, "players_special", "view")):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    
    player_service = PlayerService(db)
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # ── Security: "Секретно" status and superuser profile protection ──
    is_secret = getattr(player, 'is_secret', 0) == 1
    # Check if this player is linked to a superuser admin account
    linked_admin = db.query(AdminUser).filter(
        AdminUser.discord_id == player.discord_id,
        AdminUser.is_superuser == True
    ).first() if player.discord_id else None

    # Профиль, привязанный к суперпользователю, и is_secret открываются
    # правом players_special:view (или статусом суперпользователя).
    if linked_admin and not (current_admin.is_superuser or check_permission(current_admin, "players_special", "view")):
        return templates.TemplateResponse("admin/player_classified.html", {
            "request": request,
            "admin": current_admin,
        })
    if is_secret and not (current_admin.is_superuser or check_permission(current_admin, "players_special", "view")):
        return templates.TemplateResponse("admin/player_classified.html", {
            "request": request,
            "admin": current_admin,
        })

    stats = db.query(PlayerStats).filter(PlayerStats.player_id == player_id).first()

    # Get extended player data
    kill_service = PlayerKillService(db)
    role_action_service = PlayerRoleActionService(db)
    ip_service = PlayerIPService(db)
    name_service = PlayerNameService(db)
    role_stat_service = PlayerRoleStatService(db)
    session_service = PlayerSessionService(db)
    
    try:
        kills = kill_service.get_player_kills(player_id)
    except HTTPException:
        logger.warning(f"Failed to get player kills for {player_id}")
        kills = []
    except Exception as e:
        logger.warning(f"Failed to get player kills: {e}")
        kills = []
    
    try:
        role_actions = role_action_service.get_player_actions(player_id)
    except HTTPException:
        logger.warning(f"Failed to get player actions for {player_id}")
        role_actions = []
    except Exception as e:
        logger.warning(f"Failed to get player actions: {e}")
        role_actions = []
    
    try:
        ips = ip_service.get_player_ips(player_id)
    except HTTPException:
        logger.warning(f"Failed to get player IPs for {player_id}")
        ips = []
    except Exception as e:
        logger.warning(f"Failed to get player IPs: {e}")
        ips = []
    
    try:
        names = name_service.get_player_names(player_id)
    except HTTPException:
        logger.warning(f"Failed to get player names for {player_id}")
        names = []
    except Exception as e:
        logger.warning(f"Failed to get player names: {e}")
        names = []
    
    try:
        role_stats = role_stat_service.get_player_role_stats(player_id)
    except HTTPException:
        logger.warning(f"Failed to get player role stats for {player_id}")
        role_stats = []
    except Exception as e:
        logger.warning(f"Failed to get player role stats: {e}")
        role_stats = []
    
    try:
        sessions = session_service.get_player_sessions(player_id)
    except HTTPException:
        logger.warning(f"Failed to get player sessions for {player_id}")
        sessions = []
    except Exception as e:
        logger.warning(f"Failed to get player sessions: {e}")
        sessions = []

    log_admin_action(
        db,
        current_admin.id,
        "view_player_profile",
        {
            "target": {
                "player_id": player.player_id,
                "steamid": player.player_steamid,
                "name": player.player_last_time_name,
            }
        },
    )

    # Notes, labels, traps, twink analysis, qual-kits
    note_svc = PlayerNoteService(db)
    label_svc = PlayerLabelService(db)
    trap_svc = PlayerTrapService(db)
    cat_svc = LabelCategoryService(db)
    punishment_svc = PunishmentService(db)

    notes = note_svc.get_notes(player_id)
    label_pairs = label_svc.get_labels(player_id)  # list of (PlayerLabel, LabelCategory)
    traps = trap_svc.get_traps(player_id)
    label_categories = cat_svc.get_all()
    punishments_raw = punishment_svc.get_for_player(player_id)
    punishments = [{"p": p, "status": punishment_svc.effective_status(p)} for p in punishments_raw]

    # Parse achievements JSON into structured reward categories
    raw_achievements = player.achievements or {}
    if isinstance(raw_achievements, list):
        raw_achievements = {}  # migrate from old list format
    kval_kits = raw_achievements.get("kval_kits", {})
    custom_items = raw_achievements.get("items", {})
    custom_stats = raw_achievements.get("stats", {})
    custom_talents = raw_achievements.get("talents", {})

    # Available qual-kits from Kit table (job_id ending with _k)
    _raw_kval_kits = (
        db.query(Kit)
        .filter(Kit.job_id.like("%_k"))
        .order_by(Kit.job_id, Kit.kit_level)
        .all()
    )
    available_kval_kits = [
        {"job": k.job_id[:-2], "level": k.kit_level, "label": f"{k.job_id[:-2]} | ур.{k.kit_level}"}
        for k in _raw_kval_kits
    ]

    from services import _online_players, _online_players_lock, _prune_stale_online
    with _online_players_lock:
        _prune_stale_online()
        is_online = player.player_steamid in _online_players

    # Permission flags for template
    has_view = check_permission(current_admin, "players", "view")
    has_edit = check_permission(current_admin, "players", "edit")
    has_delete = check_permission(current_admin, "players", "delete")
    has_special = check_permission(current_admin, "players_special", "view")
    display_last_ip = player.last_ip if player.last_ip and not is_bogon_ip(player.last_ip) else None

    return templates.TemplateResponse("admin/player_detail.html", {
        "request": request,
        "admin": current_admin,
        "player": player,
        "display_last_ip": display_last_ip,
        "stats": stats,
        "kills": kills,
        "role_actions": role_actions,
        "ips": ips,
        "names": names,
        "role_stats": role_stats,
        "sessions": sessions,
        "ranks": settings.ranks_mapping,
        "kits": settings.kits_mapping,
        "flashes": pop_flashes(request),
        "notes": notes,
        "label_pairs": label_pairs,
        "traps": traps,
        "label_categories": label_categories,
        "punishments": punishments,
        "type_labels": PUNISHMENT_TYPE_LABELS,
        "kval_kits": kval_kits,
        "custom_items": custom_items,
        "custom_stats": custom_stats,
        "custom_talents": custom_talents,
        "available_kval_kits": available_kval_kits,
        "is_online": is_online,
        "has_edit": has_edit,
        "has_delete": has_delete,
        "has_special": has_special,
    })



# ══════════════════════════════════════════════════════════════════════════════
# PLAYERS JSON API (for the React SPA — mirrors admin_players_list / admin_player_detail
# above exactly, same permission checks, same service calls, JSON in/out)
# ══════════════════════════════════════════════════════════════════════════════

PLAYER_SORTS = (
    "playtime_desc", "playtime_asc", "last_login_desc", "last_login_asc",
    "last_activity_desc", "last_activity_asc",
    "active_sanctions_desc", "active_sanctions_asc",
    "open_anomalies_desc", "open_anomalies_asc",
    "discord_link_desc", "discord_link_asc",
)


def _sort_player_outs(items: list, sort: Optional[str]) -> list:
    """In-memory equivalent of PlayerRepository.SORT_COLUMNS, for branches that
    already materialized results (search, label_id + search) instead of a
    lazy SQLAlchemy query."""
    if sort == "playtime_desc":
        return sorted(items, key=lambda p: p.play_time or 0, reverse=True)
    if sort == "playtime_asc":
        return sorted(items, key=lambda p: p.play_time or 0)
    if sort == "last_login_desc":
        return sorted(items, key=lambda p: p.last_login_time or datetime.min, reverse=True)
    if sort == "last_login_asc":
        return sorted(items, key=lambda p: p.last_login_time or datetime.min)
    if sort == "last_activity_desc":
        return sorted(items, key=lambda p: p.last_login_time or datetime.min, reverse=True)
    if sort == "last_activity_asc":
        return sorted(items, key=lambda p: p.last_login_time or datetime.min)
    if sort in ("active_sanctions_desc", "active_sanctions_asc"):
        return sorted(items, key=lambda p: bool(p.has_active_punishments), reverse=sort.endswith("_desc"))
    if sort in ("open_anomalies_desc", "open_anomalies_asc"):
        return sorted(items, key=lambda p: bool(p.has_open_anomalies), reverse=sort.endswith("_desc"))
    if sort in ("discord_link_desc", "discord_link_asc"):
        return sorted(items, key=lambda p: bool(p.has_discord_link), reverse=sort.endswith("_desc"))
    return items


def _parse_label_ids(raw: Optional[str], fallback: Optional[int] = None) -> list[int]:
    values = [raw] if raw else []
    if fallback is not None:
        values.append(str(fallback))
    result = []
    for value in values:
        for item in value.split(","):
            if item.strip().isdigit():
                result.append(int(item.strip()))
    return list(dict.fromkeys(result))


@router.get("/api/v1/admin/players/search-suggestions")
async def api_players_search_suggestions(
    q: str = Query(..., min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Live-search dropdown for the Players page — top-5 best matches,
    same cascading name/steamid/previous-nickname/IP search as the main
    list (PlayerRepository.search), just capped small and fast for
    on-keystroke use instead of the full paginated list endpoint."""
    if not (check_permission(current_admin, "players", "view") or check_permission(current_admin, "players_special", "view")):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    player_service = PlayerService(db)
    results = player_service.search_players(q, limit=5)
    return {
        "players": [
            {
                "player_id": p.player_id,
                "player_last_time_name": p.player_last_time_name,
                "forced_display_name": p.forced_display_name,
                "player_steamid": p.player_steamid,
                "steam_avatar_url": p.steam_avatar_url,
            }
            for p in results
        ]
    }


# Seed defaults for the rank badge background colors (item: раздел "Игроки" —
# выделение звания цветом) — grouped by rank tier per config/ranks.json's
# current id->name mapping (0-1 Матрос, 2-4 Старшина, 5-6 Мичман, 7-9
# Офицеры). Superuser can override any individual rank_id's color via
# PATCH /api/v1/admin/players/rank-colors below — persisted in AppSetting,
# this dict is only the fallback for whatever isn't overridden yet.
_DEFAULT_RANK_COLORS = {
    "0": "#4e379f", "1": "#4e379f",
    "2": "#19742e", "3": "#19742e", "4": "#19742e",
    "5": "#9f377c", "6": "#9f377c",
    "7": "#351010", "8": "#351010", "9": "#351010",
}


def _get_rank_colors(db: Session) -> dict:
    import json
    from models import AppSetting
    row = db.query(AppSetting).filter(AppSetting.key == "rank_colors").first()
    overrides = {}
    if row and row.value:
        try:
            overrides = json.loads(row.value)
        except (ValueError, TypeError):
            overrides = {}
    return {**_DEFAULT_RANK_COLORS, **overrides}


@router.patch("/api/v1/admin/players/rank-colors")
async def api_set_rank_colors(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Superuser-only: override the background color shown for one or more
    ranks in the Игроки table/badges. Body: {"colors": {"<rank_id>": "#hex"}}
    — merges into whatever's already stored, doesn't require sending every
    rank_id every time."""
    import json
    import re
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")
    body = await request.json()
    colors = body.get("colors")
    if not isinstance(colors, dict):
        raise HTTPException(status_code=400, detail="colors должен быть объектом {rank_id: hex}")
    for rank_id, color in colors.items():
        if not isinstance(color, str) or not re.match(r"^#[0-9a-fA-F]{6}$", color):
            raise HTTPException(status_code=400, detail=f"Некорректный цвет для звания {rank_id}: {color!r}")

    from models import AppSetting
    row = db.query(AppSetting).filter(AppSetting.key == "rank_colors").first()
    current = {}
    if row and row.value:
        try:
            current = json.loads(row.value)
        except (ValueError, TypeError):
            current = {}
    current.update(colors)
    if row:
        row.value = json.dumps(current)
    else:
        db.add(AppSetting(key="rank_colors", value=json.dumps(current)))
    db.commit()
    return {**_DEFAULT_RANK_COLORS, **current}


@router.get("/api/v1/admin/players")
async def api_players_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None, min_length=2),
    label_id: Optional[int] = Query(None),
    label_ids: Optional[str] = Query(None, description="Comma-separated label category IDs"),
    label_mode: Optional[str] = Query(None, pattern="^(all|with|without|any)$"),
    rank_id: Optional[int] = Query(None),
    discord_link: Optional[str] = Query(None, pattern="^(all|linked|unlinked)$"),
    notes: Optional[str] = Query(None, pattern="^(all|with|without)$"),
    sanctions: Optional[str] = Query(None, pattern="^(all|with|without)$"),
    anomalies: Optional[str] = Query(None, pattern="^(all|with|without)$"),
    name_history: Optional[str] = Query(None, pattern="^(all|with|without)$"),
    ip_history: Optional[str] = Query(None, pattern="^(all|with|without)$"),
    activity: Optional[str] = Query(None, pattern="^(all|new|inactive_7|inactive_30|inactive_90)$"),
    sort: Optional[str] = Query(None, pattern="^(playtime_desc|playtime_asc|last_login_desc|last_login_asc|last_activity_desc|last_activity_asc|active_sanctions_desc|active_sanctions_asc|open_anomalies_desc|open_anomalies_asc|discord_link_desc|discord_link_asc)$"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not (check_permission(current_admin, "players", "view") or check_permission(current_admin, "players_special", "view")):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    from schemas import PlayerDataOut
    from services import LabelCategoryService

    player_service = PlayerService(db)
    label_categories = LabelCategoryService(db).get_all()
    selected_label_ids = _parse_label_ids(label_ids, label_id)
    effective_label_mode = "any" if selected_label_ids else label_mode
    if search:
        raw_players, total = player_service.player_repo.search_paginated(
            search, page=page, page_size=page_size, rank_id=rank_id, sort=sort,
            discord_link=discord_link,
            label_mode=effective_label_mode, label_ids=selected_label_ids,
            notes=notes, sanctions=sanctions, anomalies=anomalies,
            name_history=name_history, ip_history=ip_history, activity=activity,
        )
        outs = [PlayerDataOut.from_orm_with_mappings(p, settings.ranks_mapping, settings.kits_mapping) for p in raw_players]
        flags = player_service.player_repo.get_player_flags([player.player_id for player in raw_players])
        for player_out in outs:
            for key, value in flags.get(player_out.player_id, {}).items():
                setattr(player_out, key, value)
        players = outs
    else:
        players, total = player_service.get_all_players(
            page, page_size, rank_id=rank_id, sort=sort, discord_link=discord_link,
            label_mode=effective_label_mode, label_ids=selected_label_ids,
            notes=notes, sanctions=sanctions, anomalies=anomalies,
            name_history=name_history, ip_history=ip_history, activity=activity,
        )

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return {
        "players": players,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "label_categories": [{"id": c.id, "name": c.name, "color": c.color, "icon": c.icon} for c in label_categories],
        "selected_label_id": label_id,
        "selected_label_ids": selected_label_ids,
        "selected_label_mode": effective_label_mode or "all",
        "selected_rank_id": rank_id,
        "selected_discord_link": discord_link or "all",
        "selected_sort": sort,
        "ranks": settings.ranks_mapping,
        "rank_colors": _get_rank_colors(db),
    }


def _serialize_kill(k) -> dict:
    return {"id": k.id, "monster_type": k.monster_type, "kills_count": k.kills_count}


def _serialize_role_action(a) -> dict:
    return {"id": a.id, "role": a.role, "action_type": a.action_type, "value": a.value}


def _serialize_ip(ip) -> dict:
    return {
        "id": ip.id, "ip": ip.ip, "subnet": ip.subnet,
        "first_seen": ip.first_seen.isoformat() if ip.first_seen else None,
        "last_seen": ip.last_seen.isoformat() if ip.last_seen else None,
    }


def _serialize_name(n) -> dict:
    return {
        "id": n.id, "nickname": n.nickname,
        "first_seen": n.first_seen.isoformat() if n.first_seen else None,
        "last_seen": n.last_seen.isoformat() if n.last_seen else None,
    }


def _serialize_role_stat(s) -> dict:
    return {"id": s.id, "role": s.role, "rounds_played": s.rounds_played, "mvp_score": s.mvp_score}


def _serialize_session(s) -> dict:
    return {
        "id": s.id, "session_number": s.session_number,
        "login_time": s.login_time.isoformat() if s.login_time else None,
        "logout_time": s.logout_time.isoformat() if s.logout_time else None,
        "session_duration": s.session_duration, "avg_ping": s.avg_ping,
    }


def _serialize_note(n, admin_name: Optional[str] = None) -> dict:
    return {
        "id": n.id, "admin_id": n.admin_id, "content": n.content,
        "admin_name": admin_name,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


def _serialize_label_pair(pair) -> dict:
    label, category = pair
    return {
        "id": label.id,
        "note": label.note,
        "assigned_at": label.assigned_at.isoformat() if label.assigned_at else None,
        "category": {"id": category.id, "name": category.name, "color": category.color, "icon": category.icon},
    }


def _serialize_moderation_template(row) -> dict:
    return {
        "id": row.id,
        "action_type": row.action_type,
        "action_label": MODERATION_TEMPLATE_TYPE_LABELS.get(row.action_type, row.action_type),
        "name": row.name,
        "content": row.content,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_trap(t) -> dict:
    return {
        "id": t.id, "target_steamid": t.target_steamid, "reason": t.reason,
        "set_at": t.set_at.isoformat() if t.set_at else None,
        "active": bool(t.active),
        "triggered_at": t.triggered_at.isoformat() if t.triggered_at else None,
    }


def _serialize_punishment_row(row) -> dict:
    p = row["p"]
    return {
        "id": p.id, "type": p.type, "reason": p.reason,
        "issued_by_name": p.issued_by_name,
        "issued_by_was_superuser": bool(p.issued_by_was_superuser),
        "issued_at": p.issued_at.isoformat() if p.issued_at else None,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "is_active": bool(p.is_active),
        "notes": p.notes,
        "status": row["status"],
    }


@router.get("/api/v1/admin/players/{player_id}")
async def api_player_detail(
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not (check_permission(current_admin, "players", "view") or check_permission(current_admin, "players_special", "view")):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    is_secret = getattr(player, "is_secret", 0) == 1
    linked_admin = db.query(AdminUser).filter(
        AdminUser.discord_id == player.discord_id,
        AdminUser.is_superuser == True
    ).first() if player.discord_id else None
    # Профиль, привязанный к аккаунту СУПЕРПОЛЬЗОВАТЕЛЯ, и is_secret
    # открываются правом players_special:view (или статусом суперпользователя).
    if linked_admin and not (current_admin.is_superuser or check_permission(current_admin, "players_special", "view")):
        return {"classified": True}
    if is_secret and not (current_admin.is_superuser or check_permission(current_admin, "players_special", "view")):
        return {"classified": True}

    stats = db.query(PlayerStats).filter(PlayerStats.player_id == player_id).first()

    def _safe(fn, label):
        try:
            return fn(player_id)
        except Exception as e:
            logger.warning(f"Failed to get player {label} for {player_id}: {e}")
            return []

    kills = _safe(PlayerKillService(db).get_player_kills, "kills")
    role_actions = _safe(PlayerRoleActionService(db).get_player_actions, "role actions")
    ips = _safe(PlayerIPService(db).get_player_ips, "IPs")
    names = _safe(PlayerNameService(db).get_player_names, "names")
    role_stats = _safe(PlayerRoleStatService(db).get_player_role_stats, "role stats")
    sessions = _safe(PlayerSessionService(db).get_player_sessions, "sessions")

    log_admin_action(db, current_admin.id, "view_player_profile", {
        "target": {"player_id": player.player_id, "steamid": player.player_steamid, "name": player.player_last_time_name}
    })

    notes = PlayerNoteService(db).get_notes(player_id)
    note_admin_ids = {n.admin_id for n in notes if n.admin_id}
    note_admin_names = {}
    if note_admin_ids:
        note_admin_names = {
            a.id: a.discord_username for a in db.query(AdminUser).filter(AdminUser.id.in_(note_admin_ids)).all()
        }
    label_pairs = PlayerLabelService(db).get_labels(player_id)
    traps = PlayerTrapService(db).get_traps(player_id)
    label_categories = LabelCategoryService(db).get_all()
    punishment_svc = PunishmentService(db)
    punishments_raw = punishment_svc.get_for_player(player_id)
    punishments = [{"p": p, "status": punishment_svc.effective_status(p)} for p in punishments_raw]

    raw_achievements = player.achievements or {}
    if isinstance(raw_achievements, list):
        raw_achievements = {}

    _raw_kval_kits = db.query(Kit).filter(Kit.job_id.like("%_k")).order_by(Kit.job_id, Kit.kit_level).all()
    available_kval_kits = [
        {"job": k.job_id[:-2], "level": k.kit_level, "label": f"{k.job_id[:-2]} | ур.{k.kit_level}"}
        for k in _raw_kval_kits
    ]

    from services import _online_players, _online_players_lock, _prune_stale_online
    with _online_players_lock:
        _prune_stale_online()
        is_online = player.player_steamid in _online_players

    # A player can now hold several privilege rows at once (one manual grant
    # per server_scope + one department-derived grant) — list every one of
    # them rather than assuming there's only ever a single row.
    server_privileges = []
    if player.player_steamid and check_permission(current_admin, "privileges", "view"):
        from models import ServerPlayerPrivilege, ServerPrivilegeGroup, Department
        privs = db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.steamid == player.player_steamid).all()
        for priv in privs:
            group_name = None
            if priv.server_group_id:
                group = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == priv.server_group_id).first()
                group_name = group.name if group else None
            auto_department_name = None
            if priv.is_auto and priv.auto_source_department_id:
                dept = db.query(Department).filter(Department.id == priv.auto_source_department_id).first()
                auto_department_name = dept.name if dept else None
            server_privileges.append({
                "id": priv.id,
                "server_group_id": priv.server_group_id,
                "server_group_name": group_name,
                "server_scope": priv.server_scope,
                # Auto-assigned (task_sync_auto_server_privileges) vs a
                # human's manual grant — shown as "От отдела: X" when auto.
                "is_auto": bool(priv.is_auto),
                "auto_source_department_name": auto_department_name,
            })

    discord_profile_id = player.discord_id or (
        player.linked_admin_user.discord_id if player.linked_admin_user else None
    )

    return {
        "player": {
            "player_id": player.player_id,
            "player_last_time_name": player.player_last_time_name,
            "player_steamid": player.player_steamid,
            "last_login_time": player.last_login_time.isoformat() if player.last_login_time else None,
            "play_time": player.play_time,
            "play_time_hours": player.play_time_hours,
            "rank_id": player.rank_id,
            "kit_level": player.kit_level,
            "last_ip": player.last_ip if player.last_ip and not is_bogon_ip(player.last_ip) else None,
            "discord_id": player.discord_id,
            "discord_profile_id": discord_profile_id,
            "discord_id_confirmed": bool(player.discord_id_confirmed),
            "is_secret": bool(player.is_secret),
            "linked_admin_id": player.linked_admin_id,
            "linked_admin_name": player.linked_admin_user.discord_username if player.linked_admin_user else None,
            "forced_display_name": player.forced_display_name,
            "steam_avatar_url": player.steam_avatar_url,
        },
        "stats": {
            "successful_missions_captain": stats.successful_missions_captain,
            "total_missions_captain": stats.total_missions_captain,
            "saved_players": stats.saved_players,
            "killed_monsters": stats.killed_monsters,
            "killed_players": stats.killed_players,
            "killed_npc": stats.killed_npc,
        } if stats else None,
        "kills": [_serialize_kill(k) for k in kills],
        "role_actions": [_serialize_role_action(a) for a in role_actions],
        "ips": [_serialize_ip(ip) for ip in ips],
        "names": [_serialize_name(n) for n in names],
        "role_stats": [_serialize_role_stat(s) for s in role_stats],
        "sessions": [_serialize_session(s) for s in sessions],
        "notes": [_serialize_note(n, note_admin_names.get(n.admin_id)) for n in notes],
        "label_pairs": [_serialize_label_pair(p) for p in label_pairs],
        "traps": [_serialize_trap(t) for t in traps],
        "label_categories": [{"id": c.id, "name": c.name, "color": c.color, "icon": c.icon} for c in label_categories],
        "punishments": [_serialize_punishment_row(row) for row in punishments],
        "ranks": settings.ranks_mapping,
        "kits": settings.kits_mapping,
        "kval_kits": raw_achievements.get("kval_kits", {}),
        "custom_items": raw_achievements.get("items", {}),
        "custom_job_items": raw_achievements.get("job_items", {}),
        # Предметы по ролям с вариациями снаряжения, выданные группами наград
        # (см. services_playtime_stats._apply_job_item_variations) — только для
        # просмотра здесь, редактируются через настройку групп наград, не тут.
        "custom_job_item_variations": raw_achievements.get("job_item_variations", {}),
        "custom_stats": raw_achievements.get("stats", {}),
        "custom_talents": raw_achievements.get("talents", {}),
        "available_kval_kits": available_kval_kits,
        "reward_reference": _rewards_reference(db),
        "is_online": is_online,
        "server_privileges": server_privileges,
        "has_view": check_permission(current_admin, "players", "view"),
        "has_edit": check_permission(current_admin, "players", "edit"),
        "has_delete": check_permission(current_admin, "players", "delete"),
        "has_special": check_permission(current_admin, "players_special", "view"),
        # Гранулярные права (см. permissions_registry) — фронтенд по ним
        # прячет конкретные блоки профиля, чтобы не показывать действия,
        # которые всё равно вернут 403.
        "can_sync": check_permission(current_admin, "players", "sync"),
        "can_edit_rank_kit": check_permission(current_admin, "players", "edit_rank_kit"),
        "can_edit_discord_id": check_permission(current_admin, "players", "edit_discord_id"),
        "can_manage_notes": check_permission(current_admin, "players", "manage_notes"),
        "can_manage_labels": check_permission(current_admin, "players", "manage_labels"),
        "can_send_message": check_permission(current_admin, "moderation", "message"),
        "can_manage_traps": check_permission(current_admin, "players", "manage_traps"),
        "can_manage_achievements": check_permission(current_admin, "players", "manage_achievements"),
        "can_manage_rewards": check_permission(current_admin, "players", "manage_rewards"),
        "can_set_forced_name": check_permission(current_admin, "players", "set_forced_name"),
        "can_issue_punishment": check_permission(current_admin, "punishments", "issue"),
        # Отмена наказания и разбан доступны прямо в профиле игрока (вкладка
        # «Наказания»), а не только на общей странице наказаний — право то же
        # самое, что и там.
        "can_revoke_punishment": check_permission(current_admin, "punishments", "revoke"),
        "punishment_types": PUNISHMENT_TYPES,
        "punishment_type_labels": PUNISHMENT_TYPE_LABELS,
    }


@router.get("/api/v1/admin/discord-profiles/{discord_id}")
async def api_discord_profile(
    discord_id: int,
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Return live Discord profile media without persisting CDN files in SQL."""
    can_view = any((
        check_permission(current_admin, "players", "view"),
        check_permission(current_admin, "players_special", "view"),
        check_permission(current_admin, "tickets", "view"),
        check_permission(current_admin, "discord_bot", "view"),
    ))
    if not can_view:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    from discord_bot import bot as discord_bot
    from routes.discord_viewer_api import _call_bot

    try:
        return await _call_bot(
            discord_bot.get_discord_user_profile(discord_id),
            timeout=12.0,
        )
    except HTTPException as exc:
        if exc.status_code == 502 and "NotFound" in str(exc.detail):
            raise HTTPException(status_code=404, detail="Discord user not found") from exc
        raise


@router.post("/api/v1/admin/players/{player_id}/sync")
async def api_player_force_sync(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Queue a forced cache resync for this one player — the game server
    plugin skips its usual hash check and re-pulls the profile+privileges
    straight from the API, overwriting its local SQLite cache. See
    RoundManager_ND.cs's 'sync_player' case in ExecuteActionAsync."""
    if not check_permission(current_admin, "players", "sync"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    body = await request.json() if await request.body() else {}
    server_name = body.get("server_name") or None

    action = ServerModerationService(db).enqueue_action(
        {"action_type": "sync_player", "target_steamid": player.player_steamid},
        admin_id=current_admin.id,
        server_name=server_name,
        payload={"scope": "player"},
    )
    log_admin_action(db, current_admin.id, "force_sync_player", {
        "target": {"player_id": player.player_id, "steamid": player.player_steamid},
    })
    return {"status": "queued", "action_id": action.id}


@router.post("/api/v1/admin/players/sync-all")
async def api_players_force_sync_all(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Queue a forced cache resync for every player currently known to the
    game server (its connected/cached set) — see 'sync_player' with
    scope=all in RoundManager_ND.cs."""
    if not check_permission(current_admin, "players", "sync"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    body = await request.json() if await request.body() else {}
    server_name = body.get("server_name") or None

    action = ServerModerationService(db).enqueue_action(
        {"action_type": "sync_player", "target_steamid": "all"},
        admin_id=current_admin.id,
        server_name=server_name,
        payload={"scope": "all"},
    )
    log_admin_action(db, current_admin.id, "force_sync_all_players", {"server_name": server_name})
    return {"status": "queued", "action_id": action.id}


@router.post("/api/v1/admin/players/{player_id}/link-admin")
async def api_link_player_admin(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Superuser-only: link this player character to an ND admin account and/or
    set a forced display name, shown everywhere in ratings/punishments."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    await ensure_csrf_or_valid_admin_session(request, db)

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    body = await request.json()
    admin_id = body.get("admin_id")
    if admin_id is not None:
        target_admin = db.query(AdminUser).filter(AdminUser.id == int(admin_id)).first()
        if not target_admin:
            raise HTTPException(status_code=404, detail="Администратор не найден")
        player.linked_admin_id = target_admin.id
    else:
        player.linked_admin_id = None
    # Any manual edit through this endpoint takes precedence over the
    # auto-match worker — it will no longer touch this player's link.
    player.linked_admin_auto = False

    forced_name = (body.get("forced_display_name") or "").strip()
    player.forced_display_name = forced_name or None

    db.commit()
    log_admin_action(db, current_admin.id, "player_link_admin", {
        "player_id": player_id, "admin_id": player.linked_admin_id, "forced_display_name": player.forced_display_name,
    })
    return {"status": "ok"}


@router.post("/api/v1/admin/players/{player_id}/forced-name")
async def api_set_player_forced_name(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Установка принудительного отображаемого имени БЕЗ привязки к аккаунту
    ND — раньше это было возможно только суперпользователю через /link-admin
    заодно с привязкой аккаунта (которая выдаёт сайтовые права и поэтому
    справедливо остаётся только для суперов). Здесь — отдельное действие
    players:set_forced_name, ничего кроме forced_display_name не трогает."""
    if not check_permission(current_admin, "players", "set_forced_name"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    body = await request.json()
    forced_name = (body.get("forced_display_name") or "").strip()
    if len(forced_name) > 255:
        raise HTTPException(status_code=400, detail="Слишком длинное имя")
    player.forced_display_name = forced_name or None
    db.commit()

    log_admin_action(db, current_admin.id, "player_set_forced_name", {
        "player_id": player_id, "forced_display_name": player.forced_display_name,
    })
    return {"status": "ok", "forced_display_name": player.forced_display_name}


@router.post("/api/v1/admin/players/{player_id}/rank-kit")
async def api_update_player_rank_kit(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not (check_permission(current_admin, "players", "delete") or check_permission(current_admin, "players", "edit_rank_kit") or check_permission(current_admin, "players_special", "view")):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    body = await request.json()
    old_rank_id = player.rank_id
    old_kit_level = player.kit_level
    new_rank_id = int(body["rank_id"]) if body.get("rank_id") is not None else old_rank_id
    new_kit_level = int(body["kit_level"]) if body.get("kit_level") is not None else old_kit_level

    if new_rank_id == old_rank_id and new_kit_level == old_kit_level:
        return {"status": "ok", "changed": False}

    player.rank_id = new_rank_id
    player.kit_level = new_kit_level
    db.commit()
    db.refresh(player)

    if old_rank_id != new_rank_id and player.discord_id and player.discord_id_confirmed:
        from discord_bot import bot as discord_bot
        rank_roles = settings.rank_discord_roles or {}
        old_discord_role = rank_roles.get(str(old_rank_id), "") if old_rank_id is not None else ""
        new_discord_role = rank_roles.get(str(new_rank_id), "") if new_rank_id is not None else ""
        discord_bot.schedule(discord_bot.execute_bot_action("update_rank_role", {
            "discord_id": player.discord_id,
            "remove_role_id": old_discord_role or None,
            "add_role_id": new_discord_role or None,
            "player_name": player.player_last_time_name,
            "old_rank": settings.ranks_mapping.get(str(old_rank_id), ""),
            "new_rank": settings.ranks_mapping.get(str(new_rank_id), ""),
        }))

    log_admin_action(db, current_admin.id, "update_player", {
        "target": {"player_id": player.player_id, "steamid": player.player_steamid, "name": player.player_last_time_name},
        "changes": [
            *([{"field": "rank_id", "from": old_rank_id, "to": new_rank_id}] if old_rank_id != new_rank_id else []),
            *([{"field": "kit_level", "from": old_kit_level, "to": new_kit_level}] if old_kit_level != new_kit_level else []),
        ],
    })
    return {"status": "ok", "changed": True}


@router.post("/api/v1/admin/players/{player_id}/toggle-secret")
async def api_toggle_player_secret(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404)
    player.is_secret = 0 if player.is_secret else 1
    db.commit()
    log_admin_action(db, current_admin.id, "toggle_secret", {"player_id": player_id, "is_secret": player.is_secret})
    return {"status": "ok", "is_secret": bool(player.is_secret)}


@router.post("/api/v1/admin/players/{player_id}/discord-id")
async def api_update_player_discord_id(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not (check_permission(current_admin, "players", "edit_discord_id") or check_permission(current_admin, "players", "delete") or check_permission(current_admin, "players_special", "view")):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404)

    body = await request.json()
    discord_id = (body.get("discord_id") or "").strip()
    player.discord_id = discord_id or None
    player.discord_id_confirmed = 1 if discord_id else 0
    db.commit()
    log_admin_action(db, current_admin.id, "update_discord_id", {"player_id": player_id, "discord_id": discord_id})
    return {"status": "ok", "discord_id": player.discord_id}


@router.post("/api/v1/admin/players/{player_id}/notes")
async def api_add_player_note(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_notes"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    note = PlayerNoteService(db).save_note(player_id, current_admin.id, content)
    return {"status": "ok", "note": _serialize_note(note)}


@router.post("/api/v1/admin/players/{player_id}/notes/{note_id}")
async def api_update_player_note(
    request: Request,
    player_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_notes"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    note = PlayerNoteService(db).update_note(note_id, player_id, content)
    if not note:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"status": "ok", "note": _serialize_note(note)}


@router.delete("/api/v1/admin/players/{player_id}/notes/{note_id}")
async def api_delete_player_note(
    request: Request,
    player_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_notes"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    ok = PlayerNoteService(db).delete_note(note_id, player_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Заметка не найдена")

    log_admin_action(db, current_admin.id, "player_note_delete", {"player_id": player_id, "note_id": note_id})
    return {"status": "ok"}


@router.post("/api/v1/admin/players/{player_id}/message")
async def api_send_player_message(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "moderation", "message"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    body = await request.json()
    message_text = (body.get("message_text") or "").strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="message_text required")
    if len(message_text) > 2000:
        raise HTTPException(status_code=400, detail="message_text too long")

    from routes.moderation import _effective_server_name, _prefix_admin_message, _queued_action_type
    from schemas import QueueActionIn

    action_in = QueueActionIn(
        action_type=_queued_action_type("message"),
        target_steamid=player.player_steamid,
        message_text=_prefix_admin_message(message_text),
        server_name=_effective_server_name(db, "message", player.player_steamid, body.get("server_name")),
    )
    action = ServerModerationService(db).enqueue_action(action_in, admin_id=current_admin.id)
    log_admin_action(db, current_admin.id, "player_message_queue", {
        "player_id": player_id, "action_id": action.id,
    })
    return {"status": "queued", "action_id": action.id}


@router.post("/api/v1/admin/players/{player_id}/labels")
async def api_assign_player_label(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_labels"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    category_id = body.get("category_id")
    if not category_id:
        raise HTTPException(status_code=400, detail="category_id required")
    try:
        label = PlayerLabelService(db).assign(player_id, int(category_id), body.get("note"), current_admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    category = db.query(LabelCategory).filter(LabelCategory.id == label.category_id).first()
    return {"status": "ok", "label": _serialize_label_pair((label, category))}


@router.patch("/api/v1/admin/players/{player_id}/labels/{label_id}")
async def api_update_player_label(
    request: Request,
    player_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_labels"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    try:
        label = PlayerLabelService(db).update_note(label_id, player_id, body.get("note"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not label:
        raise HTTPException(status_code=404, detail="Метка не найдена")
    category = db.query(LabelCategory).filter(LabelCategory.id == label.category_id).first()
    log_admin_action(db, current_admin.id, "update_player_label", {
        "player_id": player_id, "label_id": label_id,
    })
    return {"status": "ok", "label": _serialize_label_pair((label, category))}


@router.delete("/api/v1/admin/players/{player_id}/labels/{label_id}")
async def api_remove_player_label(
    request: Request,
    player_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_labels"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    if not PlayerLabelService(db).remove(label_id, player_id=player_id):
        raise HTTPException(status_code=404, detail="Метка не найдена")
    log_admin_action(db, current_admin.id, "remove_label", {"player_id": player_id, "label_id": label_id})
    return {"status": "ok"}


@router.post("/api/v1/admin/players/{player_id}/traps")
async def api_set_player_trap(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_traps"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404)
    body = await request.json()
    trap = PlayerTrapService(db).set_trap(player_id, player.player_steamid, body.get("reason"), current_admin.id)
    return {"status": "ok", "trap": _serialize_trap(trap)}


@router.delete("/api/v1/admin/players/{player_id}/traps/{trap_id}")
async def api_deactivate_player_trap(
    request: Request,
    player_id: int,
    trap_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_traps"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerTrapService(db).deactivate(trap_id)
    return {"status": "ok"}


@router.post("/adminold/players/{player_id}/update")
async def update_player(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update player data (rank/kit) - requires 'delete' or 'special' permission"""
    if not (check_permission(current_admin, "players", "delete") or check_permission(current_admin, "players", "edit_rank_kit") or check_permission(current_admin, "players_special", "view")):
        push_flash(request, "error", "Недостаточно прав для редактирования игрока")
        return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        form = await request.form()

        player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
        if not player:
            push_flash(request, "error", "Игрок не найден")
            return RedirectResponse(url="/adminold/players", status_code=302)

        rank_raw = form.get("rank_id")
        kit_raw = form.get("kit_level")

        try:
            new_rank_id = None if rank_raw in (None, "") else int(rank_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение ранга")
            return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)

        try:
            new_kit_level = None if kit_raw in (None, "") else int(kit_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение набора")
            return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)

        old_rank_id = player.rank_id
        old_kit_level = player.kit_level

        changes = []
        if old_rank_id != new_rank_id:
            changes.append({"field": "rank_id", "from": old_rank_id, "to": new_rank_id})
        if old_kit_level != new_kit_level:
            changes.append({"field": "kit_level", "from": old_kit_level, "to": new_kit_level})

        if not changes:
            push_flash(request, "info", "Изменений не обнаружено")
            return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)

        player.rank_id = new_rank_id
        player.kit_level = new_kit_level

        db.commit()
        db.refresh(player)

        # If rank changed and player has confirmed discord_id, queue role update for bot
        if old_rank_id != new_rank_id and player.discord_id and player.discord_id_confirmed:
            from discord_bot import bot as discord_bot
            rank_roles = settings.rank_discord_roles or {}
            old_discord_role = rank_roles.get(str(old_rank_id), "") if old_rank_id is not None else ""
            new_discord_role = rank_roles.get(str(new_rank_id), "") if new_rank_id is not None else ""

            logger.info(f"[RankChange] discord_id={player.discord_id}, old_rank={old_rank_id}->role={old_discord_role}, new_rank={new_rank_id}->role={new_discord_role}")

            discord_bot.schedule(discord_bot.execute_bot_action("update_rank_role", {
                "discord_id": player.discord_id,
                "remove_role_id": old_discord_role or None,
                "add_role_id": new_discord_role or None,
                "player_name": player.player_last_time_name,
                "old_rank": settings.ranks_mapping.get(str(old_rank_id), ""),
                "new_rank": settings.ranks_mapping.get(str(new_rank_id), ""),
            }))
            logger.info("[RankChange] Bot action dispatched")
        elif old_rank_id != new_rank_id:
            logger.info(f"[RankChange] Skipped: discord_id={player.discord_id}, confirmed={getattr(player, 'discord_id_confirmed', None)}")

        log_admin_action(
            db,
            current_admin.id,
            "update_player",
            {
                "target": {
                    "player_id": player.player_id,
                    "steamid": player.player_steamid,
                    "name": player.player_last_time_name,
                },
                "changes": changes,
            },
        )
        push_flash(request, "success", "Данные игрока успешно обновлены")
    except HTTPException as exc:
        push_flash(request, "error", f"Не удалось обновить игрока: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Update player failed: %s", exc)
        push_flash(request, "error", "Не удалось обновить данные игрока из-за внутренней ошибки")

    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


@router.post("/adminold/players/{player_id}/toggle-secret")
async def toggle_player_secret(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Toggle 'is_secret' flag on a player profile. Superuser only."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404)

    player.is_secret = 0 if player.is_secret else 1
    db.commit()

    status = "засекречен" if player.is_secret else "рассекречен"
    log_admin_action(db, current_admin.id, "toggle_secret", {"player_id": player_id, "is_secret": player.is_secret})
    push_flash(request, "success", f"Профиль {status}")
    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


@router.post("/adminold/players/{player_id}/discord-id")
async def update_player_discord_id(
    request: Request,
    player_id: int,
    discord_id: str = Form(""),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update player's Discord ID (confirmed by admin)."""
    if not (check_permission(current_admin, "players", "edit_discord_id") or check_permission(current_admin, "players", "delete") or check_permission(current_admin, "players_special", "view")):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
        if not player:
            push_flash(request, "error", "Игрок не найден")
            return RedirectResponse(url="/adminold/players", status_code=302)

        new_discord_id = discord_id.strip() or None
        player.discord_id = new_discord_id
        player.discord_id_confirmed = 1 if new_discord_id else 0  # Admin-set = confirmed
        db.commit()

        log_admin_action(db, current_admin.id, "update_discord_id", {
            "player_id": player_id, "discord_id": new_discord_id
        })
        push_flash(request, "success", f"Discord ID обновлён: {new_discord_id or 'удалён'}")
    except HTTPException as exc:
        push_flash(request, "error", exc.detail)
    except Exception as exc:
        db.rollback()
        logger.exception("Update discord_id failed: %s", exc)
        push_flash(request, "error", "Ошибка обновления Discord ID")

    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


@router.get("/adminold/sessions", response_class=HTMLResponse)
async def admin_sessions_list(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """List active admin sessions"""
    if not check_permission(current_admin, "sessions", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    sessions = db.query(AdminSession).order_by(AdminSession.created_at.desc()).all()

    return templates.TemplateResponse("admin/sessions.html", {"request": request, "admin": current_admin, "sessions": sessions, "flashes": pop_flashes(request)})


@router.post("/adminold/sessions/{session_id}/revoke")
async def revoke_session(request: Request, session_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Revoke a specific admin session"""
    if not check_permission(current_admin, "sessions", "edit"):
        push_flash(request, "error", "Недостаточно прав для отзыва сессии")
        return RedirectResponse(url="/adminold/sessions", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        s = db.query(AdminSession).filter(AdminSession.id == session_id).first()
        if not s:
            push_flash(request, "error", "Сессия не найдена")
            return RedirectResponse(url="/adminold/sessions", status_code=302)

        target_admin = db.query(AdminUser).filter(AdminUser.id == s.admin_id).first()

        s.revoked = 1
        db.commit()
        log_admin_action(
            db,
            current_admin.id,
            "revoke_session",
            {
                "target": {
                    "session_id": session_id,
                    "admin_id": s.admin_id,
                    "admin_username": target_admin.discord_username if target_admin else None,
                }
            },
        )
        push_flash(request, "success", "Сессия успешно отозвана")
    except HTTPException as exc:
        push_flash(request, "error", f"Не удалось отозвать сессию: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Revoke session failed: %s", exc)
        push_flash(request, "error", "Не удалось отозвать сессию из-за внутренней ошибки")

    return RedirectResponse(url="/adminold/sessions", status_code=302)


@router.get("/api/v1/admin/sessions")
async def api_sessions_list(db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "sessions", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sessions = db.query(AdminSession).order_by(AdminSession.created_at.desc()).all()
    admin_ids = {s.admin_id for s in sessions}
    admins_map = {a.id: a.discord_username for a in db.query(AdminUser).filter(AdminUser.id.in_(admin_ids)).all()} if admin_ids else {}

    return {
        "sessions": [
            {
                "id": s.id, "admin_id": s.admin_id, "admin_name": admins_map.get(s.admin_id),
                "token_preview": s.token[:12] + "…",
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "revoked": bool(s.revoked),
            }
            for s in sessions
        ],
        "can_edit": check_permission(current_admin, "sessions", "edit"),
    }


@router.post("/api/v1/admin/sessions/{session_id}/revoke")
async def api_revoke_session(session_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "sessions", "edit"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для отзыва сессии")

    s = db.query(AdminSession).filter(AdminSession.id == session_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    target_admin = db.query(AdminUser).filter(AdminUser.id == s.admin_id).first()
    s.revoked = 1
    db.commit()
    log_admin_action(db, current_admin.id, "revoke_session", {
        "target": {"session_id": session_id, "admin_id": s.admin_id, "admin_username": target_admin.discord_username if target_admin else None}
    })
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════
# PRIVILEGE GROUPS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/adminold/privilege-groups", response_class=HTMLResponse)
async def admin_privilege_groups(request: Request):
    """Redirect to combined users+groups page."""
    return RedirectResponse(url="/adminold/users?tab=groups", status_code=302)


@router.post("/adminold/privilege-groups/create")
async def admin_privilege_group_create(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    from security import enforce_rate_limit
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    enforce_rate_limit(db, current_admin, "group_create")
    await ensure_csrf_or_valid_admin_session(request, db)

    from models import AdminPrivilegeGroup
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        push_flash(request, "error", "Название группы не может быть пустым")
        return RedirectResponse(url="/adminold/users?tab=groups", status_code=302)

    if db.query(AdminPrivilegeGroup).filter(AdminPrivilegeGroup.name == name).first():
        push_flash(request, "error", f"Группа '{name}' уже существует")
        return RedirectResponse(url="/adminold/users?tab=groups", status_code=302)

    import json as _json
    permissions_str = (form.get("permissions") or "{}").strip()
    try:
        permissions = _json.loads(permissions_str)
    except Exception:
        permissions = {}

    grp = AdminPrivilegeGroup(
        name=name,
        description=(form.get("description") or "").strip() or None,
        permissions=permissions,
        rate_limit_count=int(form.get("rate_limit_count") or 0),
        rate_limit_window_secs=int(form.get("rate_limit_window_secs") or 3600),
    )
    db.add(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "privilege_group_create", {"name": name})
    push_flash(request, "success", f"Группа '{name}' создана")
    return RedirectResponse(url="/adminold/users?tab=groups", status_code=302)


@router.post("/adminold/privilege-groups/{group_id}/edit")
async def admin_privilege_group_edit(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    from security import enforce_rate_limit
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    enforce_rate_limit(db, current_admin, "group_edit")
    await ensure_csrf_or_valid_admin_session(request, db)

    from models import AdminPrivilegeGroup
    import json as _json
    grp = db.query(AdminPrivilegeGroup).filter(AdminPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    form = await request.form()
    name = (form.get("name") or "").strip()
    if name:
        grp.name = name
    grp.description = (form.get("description") or "").strip() or None
    permissions_str = (form.get("permissions") or "{}").strip()
    try:
        grp.permissions = _json.loads(permissions_str)
    except Exception:
        pass
    grp.rate_limit_count = int(form.get("rate_limit_count") or 0)
    grp.rate_limit_window_secs = int(form.get("rate_limit_window_secs") or 3600)
    db.commit()
    log_admin_action(db, current_admin.id, "privilege_group_edit", {"id": group_id, "name": grp.name})
    push_flash(request, "success", f"Группа '{grp.name}' обновлена")
    return RedirectResponse(url="/adminold/users?tab=groups", status_code=302)


@router.post("/adminold/privilege-groups/{group_id}/delete")
async def admin_privilege_group_delete(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    from models import AdminPrivilegeGroup
    grp = db.query(AdminPrivilegeGroup).filter(AdminPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    # Unlink members before deletion
    db.query(AdminUser).filter(AdminUser.privilege_group_id == group_id).update({"privilege_group_id": None})
    db.delete(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "privilege_group_delete", {"id": group_id, "name": grp.name})
    push_flash(request, "success", "Группа удалена")
    return RedirectResponse(url="/adminold/users?tab=groups", status_code=302)


@router.post("/adminold/users/{user_id}/assign-group")
async def admin_user_assign_group(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Assign privilege group to admin user. Superuser cannot be downgraded by non-superuser."""
    from security import enforce_rate_limit
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    enforce_rate_limit(db, current_admin, "user_assign_group")
    await ensure_csrf_or_valid_admin_session(request, db)

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Guard: only superuser can modify another superuser
    if target.is_superuser and not current_admin.is_superuser:
        push_flash(request, "error", "Нельзя изменить суперпользователя")
        return RedirectResponse(url="/adminold/users?tab=users", status_code=302)

    from models import AdminPrivilegeGroup
    import json as _json
    form = await request.form()
    group_id_str = (form.get("privilege_group_id") or "").strip()
    target.privilege_group_id = int(group_id_str) if group_id_str else None

    # individual overrides
    ind_raw = (form.get("individual_permissions") or "").strip()
    try:
        target.individual_permissions = _json.loads(ind_raw) if ind_raw else None
    except Exception:
        target.individual_permissions = None

    # superuser toggle — only current superuser can grant
    if current_admin.is_superuser:
        target.is_superuser = form.get("is_superuser") == "1"

    db.commit()
    log_admin_action(db, current_admin.id, "user_assign_group", {"target_id": user_id, "group_id": target.privilege_group_id})
    push_flash(request, "success", "Права пользователя обновлены")
    return RedirectResponse(url="/adminold/users?tab=users", status_code=302)


@router.get("/adminold/debug", response_class=HTMLResponse)
async def admin_debug(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Debug page - check permission system"""
    return templates.TemplateResponse("admin/debug.html", {
        "request": request,
        "admin": current_admin,
    })


@router.get("/adminold/meeting", response_class=HTMLResponse)
async def admin_meeting(
    request: Request,
    room: str = Query("asper-general"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Voice meeting room via Jitsi."""
    if not check_permission(current_admin, "meeting", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return templates.TemplateResponse("admin/meeting.html", {
        "request": request,
        "admin": current_admin,
        "room_name": room.strip() or "asper-general",
        "admin_name": current_admin.discord_username or "Admin",
        "jitsi_domain": settings.jitsi_domain,
    })


@router.get("/adminold/audit", response_class=HTMLResponse)
async def admin_audit_list(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """List audit logs - lightweight page shell, data loaded via AJAX"""
    if not check_permission(current_admin, "audit", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    return templates.TemplateResponse(
        "admin/audit.html",
        {
            "request": request,
            "admin": current_admin,
            "flashes": pop_flashes(request),
        },
    )


@router.get("/api/v1/admin/tasks")
async def api_task_status(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Return current status of all background tasks (superuser only)."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403)
    from fastapi.responses import JSONResponse
    try:
        from task_scheduler import scheduler
        return JSONResponse({
            "queue_size": scheduler.queue_size(),
            "tasks": scheduler.list_tasks(),
        })
    except ImportError:
        return JSONResponse({"queue_size": 0, "tasks": [], "note": "task_scheduler not available"})


@router.get("/api/v1/admin/health")
async def api_system_health(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """System health: process metrics, DB stats, scheduler, log cache."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse
    import os, sys, time
    from datetime import datetime, timezone

    result = {}

    # ── Process info ──────────────────────────────────────────────────────────
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        result["process"] = {
            "pid": os.getpid(),
            "python_version": sys.version.split()[0],
            "memory_rss_mb": round(mem.rss / 1024 / 1024, 1),
            "memory_vms_mb": round(mem.vms / 1024 / 1024, 1),
            "cpu_percent": proc.cpu_percent(interval=0.1),
            "threads": proc.num_threads(),
            "open_files": len(proc.open_files()),
            "connections": len(proc.connections()),
            "uptime_secs": int(time.time() - proc.create_time()),
        }
        # System-wide
        vm = psutil.virtual_memory()
        result["system"] = {
            "total_ram_mb": round(vm.total / 1024 / 1024, 1),
            "used_ram_mb":  round(vm.used  / 1024 / 1024, 1),
            "avail_ram_mb": round(vm.available / 1024 / 1024, 1),
            "ram_pct":      vm.percent,
            "cpu_pct":      psutil.cpu_percent(interval=0.1),
            "cpu_cores":    psutil.cpu_count(logical=True),
            "load_avg":     list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else [],
        }
        # Disk
        disk = psutil.disk_usage("/")
        result["disk"] = {
            "total_gb":  round(disk.total / 1024**3, 1),
            "used_gb":   round(disk.used  / 1024**3, 1),
            "free_gb":   round(disk.free  / 1024**3, 1),
            "used_pct":  disk.percent,
        }
    except ImportError:
        # psutil not installed — basic fallback
        result["process"] = {"pid": os.getpid(), "python_version": sys.version.split()[0]}
        result["system"] = {"note": "psutil not installed"}
        result["disk"] = {}

    # ── Uptime from app.state ─────────────────────────────────────────────────
    try:
        from main import app as _app
        start_time = getattr(_app.state, "start_time", None)
        if start_time:
            uptime_s = int((datetime.utcnow() - start_time).total_seconds())
            h, r = divmod(uptime_s, 3600)
            m, s = divmod(r, 60)
            result["uptime"] = {"seconds": uptime_s, "human": f"{h}ч {m}м {s}с"}
    except Exception:
        pass

    # ── Database ─────────────────────────────────────────────────────────────
    try:
        from sqlalchemy import text
        db_ok = True
        db_latency_ms = 0
        t0 = time.monotonic()
        db.execute(text("SELECT 1"))
        db_latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # Table row counts (fast — uses information_schema)
        counts = {}
        for tbl in ["players_data", "player_sessions", "player_role_actions",
                    "server_actions", "player_punishments", "admin_notifications",
                    "online_snapshots", "player_ratings"]:
            try:
                counts[tbl] = db.execute(text(f"SELECT COUNT(*) FROM `{tbl}`")).scalar()
            except Exception:
                counts[tbl] = None

        result["database"] = {
            "status": "ok",
            "latency_ms": db_latency_ms,
            "table_counts": counts,
        }
    except Exception as e:
        result["database"] = {"status": "error", "error": str(e)}

    # ── Task Scheduler ────────────────────────────────────────────────────────
    try:
        from task_scheduler import scheduler
        tasks = scheduler.list_tasks()
        running = [t for t in tasks if t["status"] == "running"]
        pending_q = scheduler.queue_size()
        result["scheduler"] = {
            "available": True,
            "workers": scheduler._worker_count,
            "queue_size": pending_q,
            "total_tasks": len(tasks),
            "running": len(running),
            "running_tasks": [t["task_id"] for t in running],
            "recent_tasks": sorted(tasks, key=lambda x: x.get("finished_at") or "", reverse=True)[:10],
        }
    except ImportError:
        result["scheduler"] = {"available": False, "note": "task_scheduler.py not deployed"}
    except Exception as e:
        result["scheduler"] = {"available": False, "error": str(e)}

    # ── Log cache ─────────────────────────────────────────────────────────────
    try:
        import services_logs
        log_conn = services_logs._get_conn()
        meta_rows = log_conn.execute("SELECT server, event_count, last_refresh, oldest_event, newest_event FROM log_meta").fetchall()
        log_size = 0
        try:
            import os as _os
            log_size = round(_os.path.getsize(services_logs.LOG_DB_PATH) / 1024 / 1024, 2)
        except Exception:
            pass
        result["log_cache"] = {
            "db_size_mb": log_size,
            "servers": [
                {
                    "server": r[0],
                    "events": r[1],
                    "last_refresh": r[2],
                    "oldest": r[3],
                    "newest": r[4],
                }
                for r in meta_rows
            ],
        }
    except Exception as e:
        result["log_cache"] = {"error": str(e)}

    # ── Session cache (online players) ───────────────────────────────────────
    try:
        from services import _online_players, _online_players_lock, _prune_stale_online
        with _online_players_lock:
            _prune_stale_online()
            result["online_players"] = {
                "total": len(_online_players),
                "by_server": {},
            }
            for info in _online_players.values():
                sn = info.get("server_name", "?")
                result["online_players"]["by_server"][sn] = result["online_players"]["by_server"].get(sn, 0) + 1
    except Exception as e:
        result["online_players"] = {"error": str(e)}

    result["generated_at"] = datetime.utcnow().isoformat()
    return JSONResponse(result)


@router.get("/api/v1/admin/audit/logs")
async def api_audit_logs(
    request: Request,
    source: Optional[str] = Query(None, pattern="^(discord)?$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    q: Optional[str] = Query(None, max_length=200, description="Полнотекстовый поиск по действию, админу и деталям"),
    category: Optional[str] = Query(None, pattern="^(create|update|delete|auth|other)$", description="Фильтр по категории действия"),
    admin_id: Optional[int] = Query(None, description="Фильтр по ID админа"),
    date_from: Optional[str] = Query(None, description="ISO дата начала (включительно)"),
    date_to: Optional[str] = Query(None, description="ISO дата конца (включительно)"),
    action: Optional[str] = Query(None, max_length=100, description="Точный фильтр по action (подстрока)"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """JSON API for audit logs with pagination + мощный поиск.

    q — ищет по action, admin_name (через join), деталям JSON (CAST to TEXT).
    category — маппится на паттерны как в фронте actionCategory.
    date_from/date_to — ISO строки, фильтруют по created_at.
    """
    if not check_permission(current_admin, "audit", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse
    from sqlalchemy import or_, func, cast, String as SAString

    offset = (page - 1) * page_size
    query = db.query(AdminActionLog)
    if source == "discord":
        query = query.filter(AdminActionLog.action.like("discord:%"))
    elif source == "":
        query = query.filter(~AdminActionLog.action.like("discord:%"))

    # Фильтр по категории действия (как в фронте actionCategory)
    if category:
        cat = category.lower()
        if cat == "delete":
            query = query.filter(or_(
                AdminActionLog.action.ilike("%delete%"),
                AdminActionLog.action.ilike("%revoke%"),
                AdminActionLog.action.ilike("%remove%"),
                AdminActionLog.action.ilike("%void%"),
                AdminActionLog.action.ilike("%kick%"),
                AdminActionLog.action.ilike("%ban%"),
            ))
        elif cat == "create":
            query = query.filter(or_(
                AdminActionLog.action.ilike("%create%"),
                AdminActionLog.action.ilike("%add%"),
                AdminActionLog.action.ilike("%assign%"),
                AdminActionLog.action.ilike("%generate%"),
                AdminActionLog.action.ilike("%grant%"),
            ))
        elif cat == "update":
            query = query.filter(or_(
                AdminActionLog.action.ilike("%update%"),
                AdminActionLog.action.ilike("%edit%"),
                AdminActionLog.action.ilike("%save%"),
                AdminActionLog.action.ilike("%toggle%"),
                AdminActionLog.action.ilike("%change%"),
            ))
        elif cat == "auth":
            query = query.filter(or_(
                AdminActionLog.action.ilike("%login%"),
                AdminActionLog.action.ilike("%logout%"),
                AdminActionLog.action.ilike("%auth%"),
            ))
        # other — не фильтруем

    if action:
        query = query.filter(AdminActionLog.action.ilike(f"%{action}%"))
    if admin_id is not None:
        query = query.filter(AdminActionLog.admin_id == admin_id)
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            query = query.filter(AdminActionLog.created_at >= dt_from)
        except Exception:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
            query = query.filter(AdminActionLog.created_at <= dt_to)
        except Exception:
            pass
    if q:
        q = q.strip()
        if q:
            like = f"%{q}%"
            # Для деталей JSON кастим к строке (работает и на SQLite и MySQL)
            # Для админа — join не делаем, ищем по admin_id через подзапрос или по admin lookup ниже
            # Упростим: ищем по action и details, а по имени админа — через EXISTS подзапрос
            from sqlalchemy import exists
            admin_exists = db.query(AdminUser.id).filter(
                AdminUser.id == AdminActionLog.admin_id,
                AdminUser.discord_username.ilike(like)
            ).exists()
            query = query.filter(or_(
                AdminActionLog.action.ilike(like),
                cast(AdminActionLog.details, SAString).ilike(like),
                admin_exists,
            ))
    total = query.count()
    logs = (
        query
        .order_by(AdminActionLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    # Build admin lookup only for IDs present in this page
    admin_ids = list(set(log.admin_id for log in logs if log.admin_id))
    admins = db.query(AdminUser).filter(AdminUser.id.in_(admin_ids)).all() if admin_ids else []
    admin_lookup = {a.id: a.discord_username for a in admins}

    return JSONResponse({
        "logs": [
            {
                "id": log.id,
                "admin_id": log.admin_id,
                "admin_name": admin_lookup.get(log.admin_id, "Неизвестный"),
                "action": log.action,
                "details": log.details or {},
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 1,
    })


@router.get("/adminold/server-privileges", response_class=HTMLResponse)
async def admin_server_privileges(
    request: Request,
    page: int = 1,
    search: str = "",
    tab: str = "players",
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Серверные привилегии — группы и назначения игрокам."""
    if not check_permission(current_admin, "privileges", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    from services import ServerPlayerPrivilegeService
    from models import ServerPrivilegeGroup, ServerPlayerPrivilege

    privilege_service = ServerPlayerPrivilegeService(db)
    server_privileges = settings.server_permissions_mapping

    # Server privilege groups from DB
    priv_groups = db.query(ServerPrivilegeGroup).order_by(ServerPrivilegeGroup.id).all()
    priv_groups_data = []
    for pg in priv_groups:
        member_count = db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.server_group_id == pg.id
        ).count()
        priv_groups_data.append({"group": pg, "member_count": member_count})

    # Player privilege assignments
    page = max(1, page)
    limit = settings.default_page_size
    offset = (page - 1) * limit

    if search:
        privileges = privilege_service.search_privileges(search, limit=1000)
        total = len(privileges)
        privileges = privileges[offset:offset + limit]
    else:
        privileges, total = privilege_service.get_all_privileges(limit=limit, offset=offset)

    total_pages = (total + limit - 1) // limit

    # Enrich privileges with player names
    steamids = [p.steamid for p in privileges]
    player_name_map = {}
    if steamids:
        players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steamids)).all()
        player_name_map = {p.player_steamid: p.player_last_time_name for p in players}
    for priv in privileges:
        priv.player_last_time_name = player_name_map.get(priv.steamid)

    # Admin privilege groups (for assignable_by_groups selector)
    from models import AdminPrivilegeGroup
    admin_priv_groups = db.query(AdminPrivilegeGroup).order_by(AdminPrivilegeGroup.name).all()

    return templates.TemplateResponse("admin/server_privileges.html", {
        "request": request,
        "admin": current_admin,
        "privileges": privileges,
        "server_privileges": server_privileges,
        "priv_groups_data": priv_groups_data,
        "admin_priv_groups": admin_priv_groups,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "limit": limit,
        "search": search,
        "tab": tab,
        "flashes": pop_flashes(request),
    })


@router.post("/adminold/server-privileges/add")
async def add_server_privilege(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Add legacy player privilege (backward compat — use /assign for new assignments)."""
    if not check_permission(current_admin, "privileges", "edit"):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url="/adminold/server-privileges", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        form = await request.form()
        steamid = form.get("steamid", "").strip()
        selected_privileges = form.getlist("privileges")
        reason = form.get("reason", "").strip()

        if not steamid:
            push_flash(request, "error", "SteamID не может быть пустым")
            return RedirectResponse(url="/adminold/server-privileges", status_code=302)

        from services import ServerPlayerPrivilegeService
        privilege_service = ServerPlayerPrivilegeService(db)
        privilege_service.grant_privilege(
            steamid=steamid,
            privileges=selected_privileges,
            reason=reason if reason else None,
            granted_by_admin_id=current_admin.id
        )

        log_admin_action(db, current_admin.id, "grant_player_privilege",
                         {"steamid": steamid, "privileges": selected_privileges, "reason": reason})
        push_flash(request, "success", f"Привилегии выданы игроку {steamid}")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Add privilege failed: %s", exc)
        push_flash(request, "error", "Не удалось добавить привилегию")

    return RedirectResponse(url="/adminold/server-privileges?tab=players", status_code=302)


@router.post("/adminold/server-privileges/{privilege_id}/update")
async def update_server_privilege(
    request: Request,
    privilege_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update player privilege"""
    # Check if user has permission to manage server privileges
    if not check_permission(current_admin, "privileges", "edit"):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url="/adminold/server-privileges", status_code=302)
    
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        
        from models import ServerPlayerPrivilege
        from services import ServerPlayerPrivilegeService
        
        privilege_record = db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.id == privilege_id
        ).first()
        
        if not privilege_record:
            push_flash(request, "error", "Запись о привилегиях не найдена")
            return RedirectResponse(url="/adminold/server-privileges", status_code=302)
        
        form = await request.form()
        selected_privileges = form.getlist("privileges")
        reason = form.get("reason", "").strip()
        
        privilege_service = ServerPlayerPrivilegeService(db)
        privilege_service.update_privileges(
            steamid=privilege_record.steamid,
            privileges=selected_privileges,
            reason=reason if reason else None
        )
        
        log_admin_action(
            db,
            current_admin.id,
            "update_player_privilege",
            {
                "steamid": privilege_record.steamid,
                "privileges": selected_privileges,
                "reason": reason
            },
        )
        push_flash(request, "success", "Привилегии успешно обновлены")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Update privilege failed: %s", exc)
        push_flash(request, "error", "Не удалось обновить привилегии")
    
    return RedirectResponse(url="/adminold/server-privileges", status_code=302)


@router.post("/adminold/server-privileges/{privilege_id}/delete")
async def delete_server_privilege(
    request: Request,
    privilege_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Delete player privilege"""
    # Check if user has permission to manage server privileges
    if not check_permission(current_admin, "privileges", "edit"):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url="/adminold/server-privileges", status_code=302)
    
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        
        from models import ServerPlayerPrivilege
        from services import ServerPlayerPrivilegeService
        
        privilege_record = db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.id == privilege_id
        ).first()
        
        if not privilege_record:
            push_flash(request, "error", "Запись о привилегиях не найдена")
            return RedirectResponse(url="/adminold/server-privileges", status_code=302)
        
        steamid = privilege_record.steamid
        privilege_service = ServerPlayerPrivilegeService(db)
        privilege_service.revoke_privilege(steamid)
        
        log_admin_action(
            db,
            current_admin.id,
            "revoke_player_privilege",
            {"steamid": steamid},
        )
        push_flash(request, "success", f"Привилегии удалены у игрока {steamid}")
    except Exception as exc:
        db.rollback()
        logger.exception("Delete privilege failed: %s", exc)
        push_flash(request, "error", "Не удалось удалить привилегии")
    
    return RedirectResponse(url="/adminold/server-privileges", status_code=302)


# ─── Server Privilege Groups CRUD ────────────────────────────────────────────

def _check_server_priv_perm(admin: AdminUser):
    if not check_permission(admin, "privileges", "edit"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


@router.post("/adminold/server-privilege-groups/create")
async def server_priv_group_create(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _check_server_priv_perm(current_admin)
    await ensure_csrf_or_valid_admin_session(request, db)

    from models import ServerPrivilegeGroup
    import json as _json

    form = await request.form()
    name = (form.get("name") or "").strip()
    group_key = (form.get("group_key") or "").strip()
    description = (form.get("description") or "").strip() or None

    if not name or not group_key:
        push_flash(request, "error", "Название и буквенный ID обязательны")
        return RedirectResponse(url="/adminold/server-privileges?tab=groups", status_code=302)

    if db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.group_key == group_key).first():
        push_flash(request, "error", f"Группа с ID «{group_key}» уже существует")
        return RedirectResponse(url="/adminold/server-privileges?tab=groups", status_code=302)

    commands_raw = (form.get("commands") or "").strip()
    commands = [c.strip() for c in commands_raw.splitlines() if c.strip()]
    action_ids_raw = (form.get("allowed_action_ids") or "").strip()
    allowed_action_ids = [x.strip() for x in action_ids_raw.replace(",", " ").split() if x.strip()]

    grp = ServerPrivilegeGroup(
        name=name,
        group_key=group_key,
        description=description,
        commands=commands,
        allowed_action_ids=allowed_action_ids,
        assignable_by_groups=[int(x) for x in form.getlist("assignable_by_groups") if x.strip().isdigit()],
    )
    db.add(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "server_priv_group_create", {"name": name, "key": group_key})
    push_flash(request, "success", f"Группа «{name}» создана")
    return RedirectResponse(url="/adminold/server-privileges?tab=groups", status_code=302)


@router.post("/adminold/server-privilege-groups/{group_id}/edit")
async def server_priv_group_edit(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _check_server_priv_perm(current_admin)
    await ensure_csrf_or_valid_admin_session(request, db)

    from models import ServerPrivilegeGroup

    grp = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    form = await request.form()
    name = (form.get("name") or "").strip()
    group_key = (form.get("group_key") or "").strip()

    if name:
        grp.name = name
    if group_key and group_key != grp.group_key:
        if db.query(ServerPrivilegeGroup).filter(
            ServerPrivilegeGroup.group_key == group_key,
            ServerPrivilegeGroup.id != group_id
        ).first():
            push_flash(request, "error", f"ID «{group_key}» уже используется")
            return RedirectResponse(url="/adminold/server-privileges?tab=groups", status_code=302)
        grp.group_key = group_key

    grp.description = (form.get("description") or "").strip() or None

    commands_raw = (form.get("commands") or "").strip()
    grp.commands = [c.strip() for c in commands_raw.splitlines() if c.strip()]
    action_ids_raw = (form.get("allowed_action_ids") or "").strip()
    grp.allowed_action_ids = [x.strip() for x in action_ids_raw.replace(",", " ").split() if x.strip()]
    grp.assignable_by_groups = [int(x) for x in form.getlist("assignable_by_groups") if x.strip().isdigit()]

    db.commit()
    log_admin_action(db, current_admin.id, "server_priv_group_edit", {"id": group_id, "name": grp.name})
    push_flash(request, "success", f"Группа «{grp.name}» обновлена")
    return RedirectResponse(url="/adminold/server-privileges?tab=groups", status_code=302)


@router.post("/adminold/server-privilege-groups/{group_id}/delete")
async def server_priv_group_delete(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _check_server_priv_perm(current_admin)
    await ensure_csrf_or_valid_admin_session(request, db)

    from models import ServerPrivilegeGroup, ServerPlayerPrivilege

    grp = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    db.query(ServerPlayerPrivilege).filter(
        ServerPlayerPrivilege.server_group_id == group_id
    ).update({"server_group_id": None})
    db.delete(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "server_priv_group_delete", {"id": group_id, "name": grp.name})
    push_flash(request, "success", "Группа удалена")
    return RedirectResponse(url="/adminold/server-privileges?tab=groups", status_code=302)



# ══════════════════════════════════════════════════════════════════════════════
# SERVER PRIVILEGES JSON API (for the React SPA — modern group/commands/
# action-ids/server-scope flow only; the legacy flat-privileges /add /update
# /delete routes stay form-based, unused by any current UI)
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_server_priv_group(g, member_count: int) -> dict:
    return {
        "id": g.id, "name": g.name, "group_key": g.group_key, "description": g.description,
        "commands": g.commands or [], "allowed_action_ids": g.allowed_action_ids or [],
        "assignable_by_groups": g.assignable_by_groups or [], "member_count": member_count,
    }


def _serialize_server_priv(p, auto_department_name: Optional[str] = None) -> dict:
    return {
        "id": p.id, "steamid": p.steamid, "player_id": p.player_id,
        "player_name": getattr(p, "player_last_time_name", None),
        "server_group_id": p.server_group_id, "commands": p.commands or [],
        "allowed_action_ids": p.allowed_action_ids or [], "reason": p.reason,
        "server_scope": p.server_scope,
        "granted_at": p.granted_at.isoformat() if p.granted_at else None,
        # Auto-assigned (task_sync_auto_server_privileges) vs a human's
        # manual grant — the frontend shows "От отдела: X" for auto rows
        # instead of the normal granted-by-admin display.
        "is_auto": bool(p.is_auto),
        "auto_source_department_name": auto_department_name if p.is_auto else None,
    }


@router.get("/api/v1/admin/server-privileges")
async def api_server_privileges_list(
    page: int = 1, search: str = "",
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "privileges", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    from services import ServerPlayerPrivilegeService
    from models import ServerPrivilegeGroup, ServerPlayerPrivilege, AdminPrivilegeGroup

    privilege_service = ServerPlayerPrivilegeService(db)
    priv_groups = db.query(ServerPrivilegeGroup).order_by(ServerPrivilegeGroup.id).all()
    priv_groups_data = [
        _serialize_server_priv_group(pg, db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.server_group_id == pg.id).count())
        for pg in priv_groups
    ]

    page = max(1, page)
    limit = settings.default_page_size
    offset = (page - 1) * limit
    if search:
        privileges = privilege_service.search_privileges(search, limit=1000)
        total = len(privileges)
        privileges = privileges[offset:offset + limit]
    else:
        privileges, total = privilege_service.get_all_privileges(limit=limit, offset=offset)
    total_pages = (total + limit - 1) // limit

    steamids = [p.steamid for p in privileges]
    player_name_map = {}
    if steamids:
        players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steamids)).all()
        player_name_map = {p.player_steamid: p.player_last_time_name for p in players}
    for priv in privileges:
        priv.player_last_time_name = player_name_map.get(priv.steamid)

    from models import Department
    dept_ids = {p.auto_source_department_id for p in privileges if p.is_auto and p.auto_source_department_id}
    dept_name_map = {}
    if dept_ids:
        dept_name_map = {d.id: d.name for d in db.query(Department).filter(Department.id.in_(dept_ids)).all()}

    admin_priv_groups = db.query(AdminPrivilegeGroup).order_by(AdminPrivilegeGroup.name).all()

    return {
        "privileges": [_serialize_server_priv(p, dept_name_map.get(p.auto_source_department_id)) for p in privileges],
        "server_privileges": settings.server_permissions_mapping,
        "priv_groups": priv_groups_data,
        "admin_priv_groups": [{"id": g.id, "name": g.name} for g in admin_priv_groups],
        "page": page, "total": total, "total_pages": total_pages, "limit": limit, "search": search,
        "current_admin_group_id": current_admin.privilege_group_id,
        "is_superuser": current_admin.is_superuser,
    }


@router.post("/api/v1/admin/server-privilege-groups")
async def api_server_priv_group_create(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _check_server_priv_perm(current_admin)
    from models import ServerPrivilegeGroup
    body = await request.json()
    name = (body.get("name") or "").strip()
    group_key = (body.get("group_key") or "").strip()
    if not name or not group_key:
        raise HTTPException(status_code=400, detail="Название и буквенный ID обязательны")
    if db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.group_key == group_key).first():
        raise HTTPException(status_code=409, detail=f"Группа с ID «{group_key}» уже существует")

    grp = ServerPrivilegeGroup(
        name=name, group_key=group_key, description=(body.get("description") or "").strip() or None,
        commands=body.get("commands") or [], allowed_action_ids=body.get("allowed_action_ids") or [],
        assignable_by_groups=[int(x) for x in (body.get("assignable_by_groups") or [])],
    )
    db.add(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "server_priv_group_create", {"name": name, "key": group_key})
    return {"status": "ok", "group": _serialize_server_priv_group(grp, 0)}


@router.post("/api/v1/admin/server-privilege-groups/{group_id}")
async def api_server_priv_group_edit(group_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _check_server_priv_perm(current_admin)
    from models import ServerPrivilegeGroup
    grp = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    body = await request.json()
    name = (body.get("name") or "").strip()
    group_key = (body.get("group_key") or "").strip()
    if name:
        grp.name = name
    if group_key and group_key != grp.group_key:
        if db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.group_key == group_key, ServerPrivilegeGroup.id != group_id).first():
            raise HTTPException(status_code=409, detail=f"ID «{group_key}» уже используется")
        grp.group_key = group_key
    grp.description = (body.get("description") or "").strip() or None
    grp.commands = body.get("commands") or []
    grp.allowed_action_ids = body.get("allowed_action_ids") or []
    grp.assignable_by_groups = [int(x) for x in (body.get("assignable_by_groups") or [])]
    db.commit()
    log_admin_action(db, current_admin.id, "server_priv_group_edit", {"id": group_id, "name": grp.name})
    return {"status": "ok"}


@router.delete("/api/v1/admin/server-privilege-groups/{group_id}")
async def api_server_priv_group_delete(group_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _check_server_priv_perm(current_admin)
    from models import ServerPrivilegeGroup, ServerPlayerPrivilege
    grp = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.server_group_id == group_id).update({"server_group_id": None})
    db.delete(grp)
    db.commit()
    log_admin_action(db, current_admin.id, "server_priv_group_delete", {"id": group_id, "name": grp.name})
    return {"status": "ok"}


@router.post("/api/v1/admin/server-privileges/assign")
async def api_assign_server_privilege(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "privileges", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    from models import ServerPrivilegeGroup, ServerPlayerPrivilege, PlayerData

    body = await request.json()
    steamid = (body.get("steamid") or "").strip()
    if not steamid:
        raise HTTPException(status_code=400, detail="SteamID не может быть пустым")

    server_group_id = body.get("server_group_id") or None
    if server_group_id and not current_admin.is_superuser:
        target_group = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == server_group_id).first()
        if target_group:
            assignable = target_group.assignable_by_groups or []
            if current_admin.privilege_group_id not in assignable:
                raise HTTPException(status_code=403, detail=f"У вас нет прав назначать группу «{target_group.name}»")

    commands = body.get("commands") or []
    allowed_action_ids = body.get("allowed_action_ids") or []
    reason = (body.get("reason") or "").strip() or None
    server_scope_raw = (body.get("server_scope") or "").strip().upper() or None
    server_scope = server_scope_raw if server_scope_raw in ("ASPER1", "ASPER2") else None

    # Find-or-create the MANUAL row for this exact (steamid, server_scope) —
    # never the player's auto (department-derived) row, which lives in a
    # separate row task_sync_auto_server_privileges owns exclusively. This is
    # what lets a manual grant and an auto grant coexist and sum instead of
    # clobbering each other, and lets different scopes for the same player
    # (global/ASPER1/ASPER2) each get their own row.
    record = db.query(ServerPlayerPrivilege).filter(
        ServerPlayerPrivilege.steamid == steamid,
        ServerPlayerPrivilege.server_scope == server_scope,
        ServerPlayerPrivilege.is_auto == False,
    ).first()
    if record:
        record.server_group_id = server_group_id
        record.commands = commands
        record.allowed_action_ids = allowed_action_ids
        record.reason = reason
    else:
        player = db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
        record = ServerPlayerPrivilege(
            steamid=steamid, player_id=player.player_id if player else None,
            server_group_id=server_group_id, commands=commands, allowed_action_ids=allowed_action_ids,
            reason=reason, server_scope=server_scope, granted_by_admin_id=current_admin.id,
            is_auto=False,
        )
        db.add(record)
    db.commit()
    log_admin_action(db, current_admin.id, "assign_server_privilege", {
        "steamid": steamid, "group_id": server_group_id, "commands": commands, "server_scope": server_scope
    })
    return {"status": "ok"}


@router.delete("/api/v1/admin/server-privileges/{privilege_id}")
async def api_revoke_server_privilege(privilege_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "privileges", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    from models import ServerPlayerPrivilege
    record = db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.id == privilege_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    steamid = record.steamid
    db.delete(record)
    db.commit()
    log_admin_action(db, current_admin.id, "revoke_server_privilege", {"privilege_id": privilege_id, "steamid": steamid})
    return {"status": "ok"}


@router.post("/adminold/server-privileges/assign")
async def assign_server_privilege(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Assign server privilege group and/or individual commands to a player by SteamID.
    Permission check: superuser can assign any group. Non-superuser can only assign
    groups where their NexusData privilege_group_id is in the group's assignable_by_groups list.
    """
    # Basic permission check (must have privileges.edit)
    if not check_permission(current_admin, "privileges", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)

    from models import ServerPrivilegeGroup, ServerPlayerPrivilege, PlayerData

    form = await request.form()
    steamid = (form.get("steamid") or "").strip()
    if not steamid:
        push_flash(request, "error", "SteamID не может быть пустым")
        return RedirectResponse(url="/adminold/server-privileges?tab=players", status_code=302)

    server_group_id_str = (form.get("server_group_id") or "").strip()
    server_group_id = int(server_group_id_str) if server_group_id_str.isdigit() else None

    # Check if admin is allowed to assign this specific server group
    if server_group_id and not current_admin.is_superuser:
        target_group = db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id == server_group_id).first()
        if target_group:
            assignable = target_group.assignable_by_groups or []
            admin_group_id = current_admin.privilege_group_id
            if admin_group_id not in assignable:
                push_flash(request, "error", f"У вас нет прав назначать группу «{target_group.name}»")
                return RedirectResponse(url="/adminold/server-privileges?tab=players", status_code=302)

    commands_raw = (form.get("commands") or "").strip()
    commands = [c.strip() for c in commands_raw.splitlines() if c.strip()]
    action_ids_raw = (form.get("allowed_action_ids") or "").strip()
    allowed_action_ids = [x.strip() for x in action_ids_raw.replace(",", " ").split() if x.strip()]
    reason = (form.get("reason") or "").strip() or None
    server_scope_raw = (form.get("server_scope") or "").strip().upper() or None
    server_scope = server_scope_raw if server_scope_raw in ("ASPER1", "ASPER2") else None

    # Same find-or-create-by-(steamid, server_scope, manual-only) as the JSON
    # API above — never touches the player's auto (department-derived) row.
    record = db.query(ServerPlayerPrivilege).filter(
        ServerPlayerPrivilege.steamid == steamid,
        ServerPlayerPrivilege.server_scope == server_scope,
        ServerPlayerPrivilege.is_auto == False,
    ).first()

    if record:
        record.server_group_id = server_group_id
        record.commands = commands
        record.allowed_action_ids = allowed_action_ids
        record.reason = reason
    else:
        player = db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
        record = ServerPlayerPrivilege(
            steamid=steamid,
            player_id=player.player_id if player else None,
            server_group_id=server_group_id,
            commands=commands,
            allowed_action_ids=allowed_action_ids,
            reason=reason,
            server_scope=server_scope,
            granted_by_admin_id=current_admin.id,
            is_auto=False,
        )
        db.add(record)

    db.commit()
    log_admin_action(db, current_admin.id, "assign_server_privilege",
                     {"steamid": steamid, "group_id": server_group_id, "commands": commands, "server_scope": server_scope})
    push_flash(request, "success", f"Привилегии обновлены для {steamid}")
    return RedirectResponse(url="/adminold/server-privileges?tab=players", status_code=302)


# ============================================
# API ENDPOINTS FOR ACHIEVEMENTS
# ============================================

from fastapi.responses import JSONResponse
import json
import os


def load_achievements_config() -> dict:
    """Load achievements configuration from JSON file"""
    config_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
    
    # Default structure with packs and stat types
    config = {
        "packs": {},
        "statTypes": {}
    }
    
    # Try to load from achievements.json
    achievements_file = os.path.join(config_dir, 'achievements.json')
    if os.path.exists(achievements_file):
        try:
            with open(achievements_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                # Check if new format (with packs and statTypes)
                if 'packs' in loaded and 'statTypes' in loaded:
                    return loaded
                # Otherwise, try to use old format (for backward compatibility)
                elif isinstance(loaded, dict):
                    # If it has old format keys like affliction, item, perk, stat
                    return loaded
                else:
                    return config
        except Exception as e:
            logger.error(f"Error loading achievements.json: {e}")
    
    # Fallback: return default structure
    return config


@router.get("/api/v1/admin/achievements/config")
def get_achievements_config(
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Get available achievements configuration"""
    if not check_permission(current_admin, "players", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    config = load_achievements_config()
    return JSONResponse(content=config)


@router.post("/api/v1/admin/players/{player_id}/achievements")
async def add_achievement(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Add achievement to player"""
    if not check_permission(current_admin, "players", "manage_achievements"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        
        player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        body = await request.json()
        achievement = body.get("achievement")
        
        if not achievement:
            raise HTTPException(status_code=400, detail="Achievement data is required")
        
        # Validate achievement structure
        if not isinstance(achievement, dict):
            raise HTTPException(status_code=400, detail="Achievement must be a dictionary")
        
        required_fields = ["type", "id", "name"]
        for field in required_fields:
            if field not in achievement:
                raise HTTPException(status_code=400, detail=f"Field '{field}' is required in achievement")
        
        # Get current achievements
        current_achievements = player.achievements or []
        
        # Check if achievement with same ID already exists
        for existing in current_achievements:
            if existing.get("id") == achievement.get("id"):
                raise HTTPException(status_code=409, detail="Achievement with this ID already exists")
        
        # Add new achievement
        current_achievements.append(achievement)
        player.achievements = current_achievements
        
        db.commit()
        db.refresh(player)
        
        # Log action
        log_admin_action(db, current_admin.id, "add_achievement", {
            "player_id": player_id,
            "achievement_id": achievement.get("id"),
            "achievement_type": achievement.get("type")
        })
        
        return JSONResponse(
            status_code=201,
            content={
                "status": "success",
                "message": "Achievement added",
                "achievement": achievement
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding achievement: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/api/v1/admin/players/{player_id}/achievements")
async def delete_achievement(
    request: Request,
    player_id: int,
    id: str = Query(..., description="Achievement ID to delete"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Delete achievement from player"""
    if not check_permission(current_admin, "players", "manage_achievements"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        
        player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        # Get current achievements
        current_achievements = player.achievements or []
        
        # Find and remove achievement
        initial_count = len(current_achievements)
        current_achievements = [a for a in current_achievements if a.get("id") != id]
        
        if len(current_achievements) == initial_count:
            raise HTTPException(status_code=404, detail="Achievement not found")
        
        player.achievements = current_achievements
        
        db.commit()
        db.refresh(player)
        
        # Log action
        log_admin_action(db, current_admin.id, "delete_achievement", {
            "player_id": player_id,
            "achievement_id": id
        })
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Achievement deleted"
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting achievement: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================
# REWARDS API (kval_kits, items, stats, talents)
# ============================================

def _get_rewards_player(player_id: int, db: Session) -> "PlayerData":
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


def _get_achievements_dict(player: "PlayerData") -> dict:
    raw = player.achievements or {}
    if isinstance(raw, list):
        raw = {}
    return dict(raw)


def _rewards_reference(db: Session) -> dict:
    """Справочник для форм выдачи наград: профессии, навыки и таланты с
    русскими названиями.

    Без него интерфейс требовал вписывать идентификатор профессии руками
    («captain», «securityofficer»), а опечатка молча создавала награду для
    несуществующей профессии — игра её просто не находила, и понять почему
    награда «не работает» было неоткуда.

    Источник — тот же static/doll_reference.json, что и у конструктора кукол
    (выгрузка из игрового контента). К нему добавляются профессии из таблицы
    китов: там могут быть модовые, которых в выгрузке нет.
    """
    from models import Kit
    import services_respawn_dolls

    ref = services_respawn_dolls.doll_reference() or {}

    jobs = []
    seen = set()
    for job in ref.get("jobs") or []:
        identifier = (job.get("identifier") or "").strip()
        if identifier and identifier not in seen:
            seen.add(identifier)
            jobs.append({"identifier": identifier, "name": job.get("name") or identifier})

    try:
        # Тот же фильтр, что и у страницы китов (api_kits_list), чтобы списки
        # профессий там и здесь не разъезжались: `_k` — квал-киты, не профессии.
        rows = db.query(Kit.job_id).filter(~Kit.job_id.like("%_k")).distinct().all()
        for (job_id,) in rows:
            job_id = (job_id or "").strip()
            if job_id and job_id not in seen:
                seen.add(job_id)
                jobs.append({"identifier": job_id, "name": job_id})
    except Exception as e:  # noqa: BLE001 — справочник не должен ронять карточку
        logger.warning(f"Не удалось дополнить список профессий из китов: {e}")

    jobs.sort(key=lambda j: j["name"].lower())
    return {
        "jobs": jobs,
        "skill_labels": ref.get("skill_labels") or {},
        "talents": ref.get("talents") or {},
    }


@router.post("/api/v1/admin/players/{player_id}/rewards/kval-kits")
async def set_kval_kit(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Set (add or update) a kval-kit for a profession in player achievements."""
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    job_id = body.get("job_id", "").strip()
    level = body.get("level")
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id required")
    try:
        level = int(level)
        if level < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="level must be a positive integer")
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    kval_kits = dict(ach.get("kval_kits", {}))
    kval_kits[job_id] = level
    ach["kval_kits"] = kval_kits
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "set_kval_kit", {"player_id": player_id, "job_id": job_id, "level": level})
    return JSONResponse({"status": "ok", "kval_kits": ach["kval_kits"]})


@router.delete("/api/v1/admin/players/{player_id}/rewards/kval-kits")
async def remove_kval_kit(
    request: Request,
    player_id: int,
    job_id: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Remove a kval-kit entry for a profession from player achievements."""
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    kval_kits = dict(ach.get("kval_kits", {}))
    if job_id not in kval_kits:
        raise HTTPException(status_code=404, detail="kval-kit not found")
    del kval_kits[job_id]
    ach["kval_kits"] = kval_kits
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "remove_kval_kit", {"player_id": player_id, "job_id": job_id})
    return JSONResponse({"status": "ok", "kval_kits": ach["kval_kits"]})


@router.post("/api/v1/admin/players/{player_id}/rewards/items")
async def set_reward_item(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Выдать (или изменить количество) дополнительный предмет.

    Без job_id (или с job_id="all") предмет попадает в `items` — он выдаётся
    независимо от выбранной профессии, как было всегда. С конкретной
    профессией — в `job_items[job_id]`.

    ОТДЕЛЬНЫМ ключом, а не вложением внутрь `items`: `items` — часть
    контракта с игровым сервером ({item_id: количество}, см.
    GAME_SERVER_API_GUIDE.md), и смена его формы сломала бы плагин, который
    уже это читает. Новый ключ старый плагин просто игнорирует.
    """
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    item_id = body.get("item_id", "").strip()
    job_id = (body.get("job_id") or "").strip()
    quantity = body.get("quantity")
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id required")
    try:
        quantity = int(quantity)
        if quantity < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="quantity must be a positive integer")
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)

    if not job_id or job_id == "all":
        items = dict(ach.get("items", {}))
        items[item_id] = quantity
        ach["items"] = items
    else:
        job_items = {k: dict(v) for k, v in (ach.get("job_items") or {}).items() if isinstance(v, dict)}
        bucket = job_items.setdefault(job_id, {})
        bucket[item_id] = quantity
        ach["job_items"] = job_items

    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "set_reward_item", {
        "player_id": player_id, "item_id": item_id, "quantity": quantity, "job_id": job_id or "all",
    })
    return JSONResponse({
        "status": "ok", "items": ach.get("items", {}), "job_items": ach.get("job_items", {}),
    })


@router.delete("/api/v1/admin/players/{player_id}/rewards/items")
async def remove_reward_item(
    request: Request,
    player_id: int,
    item_id: str = Query(...),
    job_id: Optional[str] = Query(None, description="Профессия; пусто или «all» — предмет из общего списка"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Снять дополнительный предмет — общий (`items`) или профессиональный
    (`job_items[job_id]`), в зависимости от job_id."""
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    job = (job_id or "").strip()

    if not job or job == "all":
        items = dict(ach.get("items", {}))
        if item_id not in items:
            raise HTTPException(status_code=404, detail="Item not found")
        del items[item_id]
        ach["items"] = items
    else:
        job_items = {k: dict(v) for k, v in (ach.get("job_items") or {}).items() if isinstance(v, dict)}
        if item_id not in job_items.get(job, {}):
            raise HTTPException(status_code=404, detail="Item not found")
        del job_items[job][item_id]
        # Пустую профессию не оставляем: иначе в справочнике наград копятся
        # профессии без предметов, которые интерфейсу нечем показать.
        if not job_items[job]:
            del job_items[job]
        ach["job_items"] = job_items

    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "remove_reward_item", {
        "player_id": player_id, "item_id": item_id, "job_id": job or "all",
    })
    return JSONResponse({
        "status": "ok", "items": ach.get("items", {}), "job_items": ach.get("job_items", {}),
    })


@router.post("/api/v1/admin/players/{player_id}/rewards/stats")
async def set_reward_stat(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Set a stat bonus. For 'all': {job_id:'all', value:5}. Per-profession: {job_id:'captain', stat_key:'mechanic', value:25}."""
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    job_id = body.get("job_id", "").strip()
    stat_key = body.get("stat_key", "").strip() if body.get("stat_key") else None
    value = body.get("value")
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id required")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="value must be an integer")
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    stats = dict(ach.get("stats", {}))
    if job_id == "all":
        stats["all"] = value
    else:
        if not stat_key:
            raise HTTPException(status_code=400, detail="stat_key required for per-profession stats")
        job_stats = dict(stats.get(job_id, {}))
        job_stats[stat_key] = value
        stats[job_id] = job_stats
    ach["stats"] = stats
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "set_reward_stat", {"player_id": player_id, "job_id": job_id, "stat_key": stat_key, "value": value})
    return JSONResponse({"status": "ok", "stats": ach["stats"]})


@router.delete("/api/v1/admin/players/{player_id}/rewards/stats")
async def remove_reward_stat(
    request: Request,
    player_id: int,
    job_id: str = Query(...),
    stat_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Remove a stat. For 'all': just job_id=all. Per-profession: job_id=captain&stat_key=mechanic."""
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    stats = dict(ach.get("stats", {}))
    if job_id == "all":
        if "all" not in stats:
            raise HTTPException(status_code=404, detail="Stat not found")
        del stats["all"]
    else:
        if job_id not in stats:
            raise HTTPException(status_code=404, detail="Job not found in stats")
        if stat_key:
            job_stats = dict(stats[job_id])
            if stat_key not in job_stats:
                raise HTTPException(status_code=404, detail="Stat key not found")
            del job_stats[stat_key]
            if job_stats:
                stats[job_id] = job_stats
            else:
                del stats[job_id]
        else:
            del stats[job_id]
    ach["stats"] = stats
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "remove_reward_stat", {"player_id": player_id, "job_id": job_id, "stat_key": stat_key})
    return JSONResponse({"status": "ok", "stats": ach["stats"]})


@router.post("/api/v1/admin/players/{player_id}/rewards/talents")
async def add_reward_talent(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Add a talent. {job_id:'captain'|'all', talent_id:'talentid1'}."""
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    job_id = body.get("job_id", "").strip()
    talent_id = body.get("talent_id", "").strip()
    if not job_id or not talent_id:
        raise HTTPException(status_code=400, detail="job_id and talent_id required")
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    talents = dict(ach.get("talents", {}))
    job_talents = list(talents.get(job_id, []))
    if talent_id not in job_talents:
        job_talents.append(talent_id)
    talents[job_id] = job_talents
    ach["talents"] = talents
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "add_reward_talent", {"player_id": player_id, "job_id": job_id, "talent_id": talent_id})
    return JSONResponse({"status": "ok", "talents": ach["talents"]})


@router.delete("/api/v1/admin/players/{player_id}/rewards/talents")
async def remove_reward_talent(
    request: Request,
    player_id: int,
    job_id: str = Query(...),
    talent_id: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Remove a talent from a player's achievements."""
    if not check_permission(current_admin, "players", "manage_rewards"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    talents = dict(ach.get("talents", {}))
    if job_id not in talents:
        raise HTTPException(status_code=404, detail="Job not found in talents")
    job_talents = list(talents[job_id])
    if talent_id not in job_talents:
        raise HTTPException(status_code=404, detail="Talent not found")
    job_talents.remove(talent_id)
    if job_talents:
        talents[job_id] = job_talents
    else:
        del talents[job_id]
    ach["talents"] = talents
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "remove_reward_talent", {"player_id": player_id, "job_id": job_id, "talent_id": talent_id})
    return JSONResponse({"status": "ok", "talents": ach["talents"]})


# ============================================
# KITS MANAGEMENT ENDPOINTS
# ============================================

from models import Kit


def _require_kits_permission(current_admin: AdminUser):
    if not check_permission(current_admin, "kits", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


@router.get("/adminold/kits", response_class=HTMLResponse)
async def admin_kits_list(
    request: Request,
    job_id: Optional[str] = Query(None),
    kit_level: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """List and manage base kits"""
    _require_kits_permission(current_admin)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    # Convert kit_level from string to int (empty string → None)
    kit_level_int: Optional[int] = int(kit_level) if kit_level and kit_level.strip() else None

    query = db.query(Kit)
    if job_id:
        query = query.filter(Kit.job_id == job_id)
    if kit_level_int is not None:
        query = query.filter(Kit.kit_level == kit_level_int)

    # Base kits: job_id does NOT end with _k
    kits = query.filter(~Kit.job_id.like("%_k")).order_by(Kit.job_id, Kit.kit_level).all()

    # Qualification kits: job_id ends with _k
    qual_kits = db.query(Kit).filter(Kit.job_id.like("%_k")).order_by(Kit.job_id, Kit.kit_level).all()

    # Unique job_ids for filter dropdown (base only)
    all_job_ids = [row[0] for row in db.query(Kit.job_id).filter(~Kit.job_id.like("%_k")).distinct().order_by(Kit.job_id).all()]

    # Unique base job names for qual kits (strip _k suffix)
    all_qual_base_jobs = sorted(set(k.job_id[:-2] for k in qual_kits))

    return templates.TemplateResponse("admin/kits.html", {
        "request": request,
        "admin": current_admin,
        "kits": kits,
        "qual_kits": qual_kits,
        "all_job_ids": all_job_ids,
        "all_qual_base_jobs": all_qual_base_jobs,
        "kits_mapping": settings.kits_mapping,
        "selected_job_id": job_id or "",
        "selected_kit_level": kit_level_int if kit_level_int is not None else "",
        "flashes": pop_flashes(request),
    })


@router.post("/adminold/kits/add")
async def admin_kits_add(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Create a new kit"""
    _require_kits_permission(current_admin)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        form = await request.form()

        job_id = form.get("job_id", "").strip()
        kit_level_raw = form.get("kit_level", "0")
        items_raw = form.get("items", "[]").strip()

        if not job_id:
            push_flash(request, "error", "Профессия (job_id) не может быть пустой")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        try:
            kit_level = int(kit_level_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение уровня кита")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        try:
            items = json.loads(items_raw)
        except json.JSONDecodeError:
            push_flash(request, "error", "Некорректный JSON в поле предметов")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        # Check duplicate
        existing = db.query(Kit).filter(Kit.job_id == job_id, Kit.kit_level == kit_level).first()
        if existing:
            push_flash(request, "error", f"Кит для профессии '{job_id}' с уровнем {kit_level} уже существует")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        new_kit = Kit(job_id=job_id, kit_level=kit_level, items=items)
        db.add(new_kit)
        db.commit()
        db.refresh(new_kit)

        log_admin_action(db, current_admin.id, "create_kit", {
            "kit_id": new_kit.id,
            "job_id": job_id,
            "kit_level": kit_level,
        })
        push_flash(request, "success", f"Кит '{job_id}' (уровень {kit_level}) успешно создан")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Create kit failed: %s", exc)
        push_flash(request, "error", "Не удалось создать кит")

    return RedirectResponse(url="/adminold/kits", status_code=302)


@router.post("/adminold/kits/{kit_id}/update")
async def admin_kits_update(
    request: Request,
    kit_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Update an existing kit"""
    _require_kits_permission(current_admin)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        form = await request.form()

        kit = db.query(Kit).filter(Kit.id == kit_id).first()
        if not kit:
            push_flash(request, "error", "Кит не найден")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        job_id = form.get("job_id", kit.job_id).strip()
        kit_level_raw = form.get("kit_level", str(kit.kit_level))
        items_raw = form.get("items", "[]").strip()

        if not job_id:
            push_flash(request, "error", "Профессия (job_id) не может быть пустой")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        try:
            kit_level = int(kit_level_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение уровня кита")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        try:
            items = json.loads(items_raw)
        except json.JSONDecodeError:
            push_flash(request, "error", "Некорректный JSON в поле предметов")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        # Check duplicate (excluding self)
        conflict = db.query(Kit).filter(
            Kit.job_id == job_id,
            Kit.kit_level == kit_level,
            Kit.id != kit_id
        ).first()
        if conflict:
            push_flash(request, "error", f"Кит для профессии '{job_id}' с уровнем {kit_level} уже существует")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        changes = []
        if kit.job_id != job_id:
            changes.append({"field": "job_id", "from": kit.job_id, "to": job_id})
        if kit.kit_level != kit_level:
            changes.append({"field": "kit_level", "from": kit.kit_level, "to": kit_level})
        if kit.items != items:
            changes.append({"field": "items", "from": kit.items, "to": items})

        if not changes:
            push_flash(request, "info", "Изменений не обнаружено")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        kit.job_id = job_id
        kit.kit_level = kit_level
        kit.items = items
        db.commit()

        log_admin_action(db, current_admin.id, "update_kit", {
            "kit_id": kit_id,
            "changes": changes,
        })
        push_flash(request, "success", f"Кит успешно обновлён")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Update kit failed: %s", exc)
        push_flash(request, "error", "Не удалось обновить кит")

    return RedirectResponse(url="/adminold/kits", status_code=302)


@router.post("/adminold/kits/{kit_id}/delete")
async def admin_kits_delete(
    request: Request,
    kit_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Delete a kit"""
    _require_kits_permission(current_admin)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        kit = db.query(Kit).filter(Kit.id == kit_id).first()
        if not kit:
            push_flash(request, "error", "Кит не найден")
            return RedirectResponse(url="/adminold/kits", status_code=302)

        job_id = kit.job_id
        level = kit.kit_level
        db.delete(kit)
        db.commit()

        log_admin_action(db, current_admin.id, "delete_kit", {
            "kit_id": kit_id,
            "job_id": job_id,
            "kit_level": level,
        })
        push_flash(request, "success", f"Кит '{job_id}' (уровень {level}) удалён")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Delete kit failed: %s", exc)
        push_flash(request, "error", "Не удалось удалить кит")

    return RedirectResponse(url="/adminold/kits", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# KITS JSON API (for the React SPA)
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_kit(k: Kit) -> dict:
    return {
        "id": k.id, "job_id": k.job_id, "kit_level": k.kit_level, "items": k.items,
        "variation_2_items": k.variation_2_items,
        "variation_3_items": k.variation_3_items,
        "is_special": k.is_special,
        "special_variation": k.special_variation,
    }


def _validate_special_kit_fields(body: dict, current_special_variation: Optional[int] = None) -> tuple:
    """Shared validation for the is_special/special_variation pair (item 15) —
    a special kit must name exactly one variation (1/2/3) it overrides for."""
    is_special = bool(body.get("is_special", False))
    special_variation = body.get("special_variation", current_special_variation if is_special else None)
    if is_special:
        try:
            special_variation = int(special_variation)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Особый кит должен указывать вариацию (1, 2 или 3)")
        if special_variation not in (1, 2, 3):
            raise HTTPException(status_code=400, detail="Вариация особого кита должна быть 1, 2 или 3")
    else:
        special_variation = None
    return is_special, special_variation


@router.get("/api/v1/admin/kits")
async def api_kits_list(
    job_id: Optional[str] = Query(None), kit_level: Optional[str] = Query(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    _require_kits_permission(current_admin)
    kit_level_int = int(kit_level) if kit_level and kit_level.strip() else None

    query = db.query(Kit)
    if job_id:
        query = query.filter(Kit.job_id == job_id)
    if kit_level_int is not None:
        query = query.filter(Kit.kit_level == kit_level_int)
    kits = query.filter(~Kit.job_id.like("%_k")).order_by(Kit.job_id, Kit.kit_level).all()
    qual_kits = db.query(Kit).filter(Kit.job_id.like("%_k")).order_by(Kit.job_id, Kit.kit_level).all()
    all_job_ids = [row[0] for row in db.query(Kit.job_id).filter(~Kit.job_id.like("%_k")).distinct().order_by(Kit.job_id).all()]
    all_qual_base_jobs = sorted(set(k.job_id[:-2] for k in qual_kits))

    return {
        "kits": [_serialize_kit(k) for k in kits],
        "qual_kits": [_serialize_kit(k) for k in qual_kits],
        "all_job_ids": all_job_ids,
        "all_qual_base_jobs": all_qual_base_jobs,
        "kits_mapping": settings.kits_mapping,
    }


@router.post("/api/v1/admin/kits/sync")
async def api_kits_force_sync(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Queue a forced reference-data resync (kit catalog + rank/kit/role
    mappings) — the game server plugin re-pulls GET /server/cache/reference
    and replaces its local kitCache regardless of the hash check. See
    RoundManager_ND.cs's 'sync_reference' case in ExecuteActionAsync."""
    _require_kits_permission(current_admin)

    body = await request.json() if await request.body() else {}
    server_name = body.get("server_name") or None

    action = ServerModerationService(db).enqueue_action(
        {"action_type": "sync_reference", "target_steamid": "system"},
        admin_id=current_admin.id,
        server_name=server_name,
    )
    log_admin_action(db, current_admin.id, "force_sync_reference", {"server_name": server_name})
    return {"status": "queued", "action_id": action.id}


@router.post("/api/v1/admin/kits")
async def api_kits_add(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_kits_permission(current_admin)
    body = await request.json()
    job_id = (body.get("job_id") or "").strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="Профессия (job_id) не может быть пустой")
    try:
        kit_level = int(body.get("kit_level", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректное значение уровня кита")
    items = body.get("items", [])
    is_special, special_variation = _validate_special_kit_fields(body)

    if db.query(Kit).filter(Kit.job_id == job_id, Kit.kit_level == kit_level).first():
        raise HTTPException(status_code=409, detail=f"Кит для профессии '{job_id}' с уровнем {kit_level} уже существует")

    new_kit = Kit(
        job_id=job_id, kit_level=kit_level, items=items,
        variation_2_items=body.get("variation_2_items"),
        variation_3_items=body.get("variation_3_items"),
        is_special=is_special, special_variation=special_variation,
    )
    db.add(new_kit)
    db.commit()
    db.refresh(new_kit)
    log_admin_action(db, current_admin.id, "create_kit", {"kit_id": new_kit.id, "job_id": job_id, "kit_level": kit_level})
    return {"status": "ok", "kit": _serialize_kit(new_kit)}


@router.post("/api/v1/admin/kits/{kit_id}")
async def api_kits_update(kit_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_kits_permission(current_admin)
    kit = db.query(Kit).filter(Kit.id == kit_id).first()
    if not kit:
        raise HTTPException(status_code=404, detail="Кит не найден")

    body = await request.json()
    job_id = (body.get("job_id") or kit.job_id).strip()
    try:
        kit_level = int(body.get("kit_level", kit.kit_level))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректное значение уровня кита")
    items = body.get("items", kit.items)
    is_special, special_variation = _validate_special_kit_fields(body, kit.special_variation)

    conflict = db.query(Kit).filter(Kit.job_id == job_id, Kit.kit_level == kit_level, Kit.id != kit_id).first()
    if conflict:
        raise HTTPException(status_code=409, detail=f"Кит для профессии '{job_id}' с уровнем {kit_level} уже существует")

    changes = []
    if kit.job_id != job_id:
        changes.append({"field": "job_id", "from": kit.job_id, "to": job_id})
    if kit.kit_level != kit_level:
        changes.append({"field": "kit_level", "from": kit.kit_level, "to": kit_level})
    kit.job_id = job_id
    kit.kit_level = kit_level
    kit.items = items
    kit.variation_2_items = body.get("variation_2_items", kit.variation_2_items)
    kit.variation_3_items = body.get("variation_3_items", kit.variation_3_items)
    kit.is_special = is_special
    kit.special_variation = special_variation
    db.commit()
    log_admin_action(db, current_admin.id, "update_kit", {"kit_id": kit_id, "changes": changes})
    return {"status": "ok", "kit": _serialize_kit(kit)}


@router.delete("/api/v1/admin/kits/{kit_id}")
async def api_kits_delete(kit_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_kits_permission(current_admin)
    kit = db.query(Kit).filter(Kit.id == kit_id).first()
    if not kit:
        raise HTTPException(status_code=404, detail="Кит не найден")
    job_id, level = kit.job_id, kit.kit_level
    db.delete(kit)
    db.commit()
    log_admin_action(db, current_admin.id, "delete_kit", {"kit_id": kit_id, "job_id": job_id, "kit_level": level})
    return {"status": "ok"}


@router.post("/api/v1/admin/qual-kits")
async def api_qual_kits_add(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_kits_permission(current_admin)
    body = await request.json()
    base_job = (body.get("base_job") or "").strip()
    if not base_job:
        raise HTTPException(status_code=400, detail="Профессия не может быть пустой")
    job_id = base_job if base_job.endswith("_k") else base_job + "_k"

    try:
        kit_level = int(body.get("kit_level", 1))
        if kit_level not in range(1, 6):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Уровень квалификации должен быть от 1 до 5")

    items = body.get("items", {})
    if not isinstance(items, dict):
        raise HTTPException(status_code=400, detail="items должен быть JSON-объектом")
    is_special, special_variation = _validate_special_kit_fields(body)

    if db.query(Kit).filter(Kit.job_id == job_id, Kit.kit_level == kit_level).first():
        raise HTTPException(status_code=409, detail=f"Квал-кит '{job_id}' уровня {kit_level} уже существует")

    new_kit = Kit(
        job_id=job_id, kit_level=kit_level, items=items,
        variation_2_items=body.get("variation_2_items"),
        variation_3_items=body.get("variation_3_items"),
        is_special=is_special, special_variation=special_variation,
    )
    db.add(new_kit)
    db.commit()
    db.refresh(new_kit)
    log_admin_action(db, current_admin.id, "create_qual_kit", {"kit_id": new_kit.id, "job_id": job_id, "kit_level": kit_level})
    return {"status": "ok", "kit": _serialize_kit(new_kit)}


@router.post("/api/v1/admin/qual-kits/{kit_id}")
async def api_qual_kits_update(kit_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_kits_permission(current_admin)
    kit = db.query(Kit).filter(Kit.id == kit_id, Kit.job_id.like("%_k")).first()
    if not kit:
        raise HTTPException(status_code=404, detail="Квал-кит не найден")

    body = await request.json()
    try:
        kit_level = int(body.get("kit_level", kit.kit_level))
        if kit_level not in range(1, 6):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Уровень квалификации должен быть от 1 до 5")

    items = body.get("items", kit.items)
    if not isinstance(items, dict):
        raise HTTPException(status_code=400, detail="items должен быть JSON-объектом")
    is_special, special_variation = _validate_special_kit_fields(body, kit.special_variation)

    conflict = db.query(Kit).filter(Kit.job_id == kit.job_id, Kit.kit_level == kit_level, Kit.id != kit_id).first()
    if conflict:
        raise HTTPException(status_code=409, detail=f"Квал-кит '{kit.job_id}' уровня {kit_level} уже существует")

    kit.kit_level = kit_level
    kit.items = items
    kit.variation_2_items = body.get("variation_2_items", kit.variation_2_items)
    kit.variation_3_items = body.get("variation_3_items", kit.variation_3_items)
    kit.is_special = is_special
    kit.special_variation = special_variation
    db.commit()
    log_admin_action(db, current_admin.id, "update_qual_kit", {"kit_id": kit_id})
    return {"status": "ok", "kit": _serialize_kit(kit)}


@router.delete("/api/v1/admin/qual-kits/{kit_id}")
async def api_qual_kits_delete(kit_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_kits_permission(current_admin)
    kit = db.query(Kit).filter(Kit.id == kit_id, Kit.job_id.like("%_k")).first()
    if not kit:
        raise HTTPException(status_code=404, detail="Квал-кит не найден")
    job_id, level = kit.job_id, kit.kit_level
    db.delete(kit)
    db.commit()
    log_admin_action(db, current_admin.id, "delete_qual_kit", {"kit_id": kit_id, "job_id": job_id, "kit_level": level})
    return {"status": "ok"}


# ── Qualification kit routes ──────────────────────────────────────────────────

@router.post("/adminold/qual-kits/add")
async def admin_qual_kits_add(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Create a new qualification kit (job_id ends with _k, kit_level 1-5)"""
    _require_kits_permission(current_admin)
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        form = await request.form()

        base_job = form.get("base_job", "").strip()
        kit_level_raw = form.get("kit_level", "1")
        items_raw = form.get("items", "{}").strip()

        if not base_job:
            push_flash(request, "error", "Профессия не может быть пустой")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        job_id = base_job if base_job.endswith("_k") else base_job + "_k"

        try:
            kit_level = int(kit_level_raw)
            if kit_level not in range(1, 6):
                raise ValueError
        except (TypeError, ValueError):
            push_flash(request, "error", "Уровень квалификации должен быть от 1 до 5")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        try:
            items = json.loads(items_raw)
            if not isinstance(items, dict):
                raise ValueError("items must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            push_flash(request, "error", f"Некорректный JSON: {e}")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        existing = db.query(Kit).filter(Kit.job_id == job_id, Kit.kit_level == kit_level).first()
        if existing:
            push_flash(request, "error", f"Квал-кит '{job_id}' уровня {kit_level} уже существует")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        new_kit = Kit(job_id=job_id, kit_level=kit_level, items=items)
        db.add(new_kit)
        db.commit()
        db.refresh(new_kit)

        log_admin_action(db, current_admin.id, "create_qual_kit", {
            "kit_id": new_kit.id, "job_id": job_id, "kit_level": kit_level,
        })
        push_flash(request, "success", f"Квал-кит '{job_id}' (уровень {kit_level}) создан")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Create qual kit failed: %s", exc)
        push_flash(request, "error", "Не удалось создать квал-кит")

    return RedirectResponse(url="/adminold/kits#qual", status_code=302)


@router.post("/adminold/qual-kits/{kit_id}/update")
async def admin_qual_kits_update(
    request: Request,
    kit_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Update a qualification kit"""
    _require_kits_permission(current_admin)
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        form = await request.form()

        kit = db.query(Kit).filter(Kit.id == kit_id, Kit.job_id.like("%_k")).first()
        if not kit:
            push_flash(request, "error", "Квал-кит не найден")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        kit_level_raw = form.get("kit_level", str(kit.kit_level))
        items_raw = form.get("items", "{}").strip()

        try:
            kit_level = int(kit_level_raw)
            if kit_level not in range(1, 6):
                raise ValueError
        except (TypeError, ValueError):
            push_flash(request, "error", "Уровень квалификации должен быть от 1 до 5")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        try:
            items = json.loads(items_raw)
            if not isinstance(items, dict):
                raise ValueError("items must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            push_flash(request, "error", f"Некорректный JSON: {e}")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        conflict = db.query(Kit).filter(
            Kit.job_id == kit.job_id, Kit.kit_level == kit_level, Kit.id != kit_id
        ).first()
        if conflict:
            push_flash(request, "error", f"Квал-кит '{kit.job_id}' уровня {kit_level} уже существует")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        kit.kit_level = kit_level
        kit.items = items
        db.commit()

        log_admin_action(db, current_admin.id, "update_qual_kit", {"kit_id": kit_id})
        push_flash(request, "success", "Квал-кит обновлён")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Update qual kit failed: %s", exc)
        push_flash(request, "error", "Не удалось обновить квал-кит")

    return RedirectResponse(url="/adminold/kits#qual", status_code=302)


@router.post("/adminold/qual-kits/{kit_id}/delete")
async def admin_qual_kits_delete(
    request: Request,
    kit_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Delete a qualification kit"""
    _require_kits_permission(current_admin)
    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        kit = db.query(Kit).filter(Kit.id == kit_id, Kit.job_id.like("%_k")).first()
        if not kit:
            push_flash(request, "error", "Квал-кит не найден")
            return RedirectResponse(url="/adminold/kits#qual", status_code=302)

        job_id = kit.job_id
        level = kit.kit_level
        db.delete(kit)
        db.commit()

        log_admin_action(db, current_admin.id, "delete_qual_kit", {
            "kit_id": kit_id, "job_id": job_id, "kit_level": level,
        })
        push_flash(request, "success", f"Квал-кит '{job_id}' (уровень {level}) удалён")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Delete qual kit failed: %s", exc)
        push_flash(request, "error", "Не удалось удалить квал-кит")

    return RedirectResponse(url="/adminold/kits#qual", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# PLAYER NOTES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/players/{player_id}/notes/save")
async def save_player_note(
    request: Request,
    player_id: int,
    content: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_notes"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerNoteService(db).save_note(player_id, current_admin.id, content)
    log_admin_action(db, current_admin.id, "save_player_note", {"player_id": player_id})
    push_flash(request, "success", "Личное дело сохранено")
    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# LABELS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/players/{player_id}/labels/assign")
async def assign_label(
    request: Request,
    player_id: int,
    category_id: int = Form(...),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_labels"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerLabelService(db).assign(player_id, category_id, note, current_admin.id)
    log_admin_action(db, current_admin.id, "assign_label", {"player_id": player_id, "category_id": category_id})
    push_flash(request, "success", "Метка установлена")
    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


@router.post("/adminold/players/{player_id}/labels/{label_id}/remove")
async def remove_label(
    request: Request,
    player_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_labels"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    if not PlayerLabelService(db).remove(label_id, player_id=player_id):
        raise HTTPException(status_code=404, detail="Метка не найдена")
    log_admin_action(db, current_admin.id, "remove_label", {"player_id": player_id, "label_id": label_id})
    push_flash(request, "success", "Метка снята")
    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


def _allowed_moderation_template_types(admin: AdminUser) -> set[str]:
    allowed: set[str] = set()
    if check_permission(admin, "moderation", "message"):
        allowed.add("message")
    if check_permission(admin, "punishments", "issue"):
        allowed.update(("warning", "kick", "ban"))
    if check_permission(admin, "moderation", "kick"):
        allowed.add("kick")
    if check_permission(admin, "moderation", "ban"):
        allowed.add("ban")
    return allowed


@router.get("/api/v1/admin/moderation-templates")
async def api_moderation_templates_list(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    allowed_types = set(MODERATION_TEMPLATE_TYPES) if current_admin.is_superuser else _allowed_moderation_template_types(current_admin)
    return {"templates": [_serialize_moderation_template(row) for row in ModerationActionTemplateService(db).list(allowed_types)]}


@router.post("/api/v1/admin/moderation-templates")
async def api_moderation_template_create(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    try:
        row = ModerationActionTemplateService(db).create(
            body.get("action_type"), body.get("name"), body.get("content"), current_admin.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_admin_action(db, current_admin.id, "moderation_template_create", {"template_id": row.id, "action_type": row.action_type})
    return {"status": "ok", "template": _serialize_moderation_template(row)}


@router.patch("/api/v1/admin/moderation-templates/{template_id}")
async def api_moderation_template_update(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    try:
        row = ModerationActionTemplateService(db).update(
            template_id, name=body.get("name"), content=body.get("content"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    log_admin_action(db, current_admin.id, "moderation_template_update", {"template_id": template_id})
    return {"status": "ok", "template": _serialize_moderation_template(row)}


@router.delete("/api/v1/admin/moderation-templates/{template_id}")
async def api_moderation_template_delete(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователей")
    await ensure_csrf_or_valid_admin_session(request, db)
    if not ModerationActionTemplateService(db).delete(template_id):
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    log_admin_action(db, current_admin.id, "moderation_template_delete", {"template_id": template_id})
    return {"status": "ok"}


def _has_label_category_perm(admin: AdminUser, action: str) -> bool:
    """Granular label-category permission with fallback to legacy manage_label_categories."""
    if check_permission(admin, "players", action):
        return True
    # Legacy single key grants all three granular actions
    return check_permission(admin, "players", "manage_label_categories")


def _require_label_category_perm(admin: AdminUser, action: str):
    if not _has_label_category_perm(admin, action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


@router.get("/adminold/label-categories")
async def label_categories_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    # Viewing list allowed if admin has any of the granular perms or legacy
    if not any(
        _has_label_category_perm(current_admin, a)
        for a in ("create_label_category", "edit_label_category", "delete_label_category")
    ):
        raise HTTPException(status_code=403)
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    cats = LabelCategoryService(db).get_all()
    return templates.TemplateResponse("admin/label_categories.html", {
        "request": request, "admin": current_admin,
        "categories": cats, "flashes": pop_flashes(request),
    })


@router.post("/adminold/label-categories/create")
async def create_label_category(
    request: Request,
    name: str = Form(...),
    color: str = Form(default="#888888"),
    icon: str = Form(default="🏷"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_label_category_perm(current_admin, "create_label_category")
    await ensure_csrf_or_valid_admin_session(request, db)
    LabelCategoryService(db).create(name.strip(), color, icon, current_admin.id)
    push_flash(request, "success", f"Категория «{name}» создана")
    return RedirectResponse(url="/adminold/label-categories", status_code=302)


@router.post("/adminold/label-categories/{cat_id}/delete")
async def delete_label_category(
    request: Request,
    cat_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_label_category_perm(current_admin, "delete_label_category")
    await ensure_csrf_or_valid_admin_session(request, db)
    ok = LabelCategoryService(db).delete(cat_id)
    push_flash(request, "success" if ok else "error",
               "Категория удалена" if ok else "Категория не найдена")
    return RedirectResponse(url="/adminold/label-categories", status_code=302)


@router.get("/api/v1/admin/label-categories")
async def api_label_categories_list(db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not any(
        _has_label_category_perm(current_admin, a)
        for a in ("create_label_category", "edit_label_category", "delete_label_category")
    ) and not check_permission(current_admin, "players", "manage_labels") and not check_permission(current_admin, "players", "view"):
        raise HTTPException(status_code=403)
    cats = LabelCategoryService(db).get_all()
    from services import LabelFolderService
    folders = LabelFolderService(db).get_all()
    return {"categories": [{"id": c.id, "name": c.name, "color": c.color, "icon": c.icon, "folder_id": c.folder_id, "sort_order": c.sort_order, "description": c.description} for c in cats],
            "folders": [{"id": f.id, "name": f.name, "sort_order": f.sort_order} for f in folders]}


@router.post("/api/v1/admin/label-categories")
async def api_create_label_category(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "create_label_category")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    cat = LabelCategoryService(db).create(name, body.get("color") or "#888888", body.get("icon") or "🏷", current_admin.id, folder_id=body.get("folder_id"), description=body.get("description"))
    return {"status": "ok", "category": {"id": cat.id, "name": cat.name, "color": cat.color, "icon": cat.icon, "folder_id": cat.folder_id, "sort_order": cat.sort_order, "description": cat.description}}


@router.post("/api/v1/admin/label-categories/{cat_id}")
async def api_update_label_category(cat_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "edit_label_category")
    body = await request.json()
    cat = LabelCategoryService(db).update(cat_id, body.get("name"), body.get("color"), body.get("icon"), folder_id=body.get("folder_id"), description=body.get("description"), has_folder="folder_id" in body, has_desc="description" in body)
    if not cat:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    return {"status": "ok", "category": {"id": cat.id, "name": cat.name, "color": cat.color, "icon": cat.icon, "folder_id": cat.folder_id, "sort_order": cat.sort_order, "description": cat.description}}


@router.delete("/api/v1/admin/label-categories/{cat_id}")
async def api_delete_label_category(cat_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "delete_label_category")
    ok = LabelCategoryService(db).delete(cat_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    return {"status": "ok"}


@router.post("/api/v1/admin/label-categories/reorder")
async def api_reorder_label_categories(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "edit_label_category")
    body = await request.json()
    ordered_ids = body.get("ordered_ids") or []
    LabelCategoryService(db).reorder(ordered_ids)
    return {"status": "ok"}


@router.get("/api/v1/admin/label-folders")
async def api_label_folders_list(db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    from services import LabelFolderService
    folders = LabelFolderService(db).get_all()
    return {"folders": [{"id": f.id, "name": f.name, "sort_order": f.sort_order} for f in folders]}


@router.post("/api/v1/admin/label-folders")
async def api_create_label_folder(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "create_label_category")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name: raise HTTPException(status_code=400, detail="name required")
    from services import LabelFolderService
    folder = LabelFolderService(db).create(name, current_admin.id)
    return {"status": "ok", "folder": {"id": folder.id, "name": folder.name, "sort_order": folder.sort_order}}


@router.post("/api/v1/admin/label-folders/{folder_id}")
async def api_update_label_folder(folder_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "edit_label_category")
    body = await request.json()
    from services import LabelFolderService
    folder = LabelFolderService(db).update(folder_id, body.get("name"))
    if not folder: raise HTTPException(status_code=404, detail="Папка не найдена")
    return {"status": "ok", "folder": {"id": folder.id, "name": folder.name}}


@router.delete("/api/v1/admin/label-folders/{folder_id}")
async def api_delete_label_folder(folder_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "delete_label_category")
    from services import LabelFolderService
    ok = LabelFolderService(db).delete(folder_id)
    if not ok: raise HTTPException(status_code=404, detail="Папка не найдена")
    return {"status": "ok"}


@router.post("/api/v1/admin/label-folders/reorder")
async def api_reorder_label_folders(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    _require_label_category_perm(current_admin, "edit_label_category")
    body = await request.json()
    from services import LabelFolderService
    LabelFolderService(db).reorder(body.get("ordered_ids") or [])
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# TRAPS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/players/{player_id}/traps/set")
async def set_trap(
    request: Request,
    player_id: int,
    reason: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_traps"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404)
    PlayerTrapService(db).set_trap(player_id, player.player_steamid, reason, current_admin.id)
    log_admin_action(db, current_admin.id, "set_trap", {"player_id": player_id})
    push_flash(request, "success", "Ловушка установлена")
    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


@router.post("/adminold/players/{player_id}/traps/{trap_id}/deactivate")
async def deactivate_trap(
    request: Request,
    player_id: int,
    trap_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "manage_traps"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerTrapService(db).deactivate(trap_id)
    push_flash(request, "success", "Ловушка деактивирована")
    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ADMINISTRATION PAGE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/adminold/administration", response_class=HTMLResponse)
async def admin_administration(
    request: Request,
    period: str = Query("day"),          # day | week | month | all
    twink_page: int = Query(1),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Administration: online stats, config editor, global twink analysis."""
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    from sqlalchemy import func, text
    from models import PlayerSession

    # ── Period boundary ──────────────────────────────────────────────────────
    now = datetime.utcnow()
    period_map = {
        "day":   now - timedelta(days=1),
        "week":  now - timedelta(weeks=1),
        "month": now - timedelta(days=30),
        "all":   None,
    }
    since = period_map.get(period, period_map["day"])

    # ── Online stats ──────────────────────────────────────────────────────────
    sess_q = db.query(PlayerSession)
    if since:
        sess_q = sess_q.filter(PlayerSession.login_time >= since)

    sessions = sess_q.all()

    unique_players = len({s.player_id for s in sessions})
    total_sessions = len(sessions)
    total_play_secs = sum((s.session_duration or 0) for s in sessions)
    avg_session_mins = round(total_play_secs / 60 / max(total_sessions, 1))

    # Peak concurrent players per hour
    # Build timeline: +1 at login_time, -1 at logout_time
    events = []
    for s in sessions:
        events.append((s.login_time, +1))
        if s.logout_time:
            events.append((s.logout_time, -1))
    events.sort(key=lambda x: x[0])
    peak_concurrent = 0
    current_count = 0
    for _, delta in events:
        current_count += delta
        if current_count > peak_concurrent:
            peak_concurrent = current_count

    # Concurrent online chart: peak / avg / min per time slot
    if period == "day":
        slot_count = 24
        slot_delta = timedelta(hours=1)
        label_fmt = "%H:%M"
    elif period == "week":
        slot_count = 7 * 6  # 4-hour slots
        slot_delta = timedelta(hours=4)
        label_fmt = "%d.%m %H:%M"
    elif period == "month":
        slot_count = 30
        slot_delta = timedelta(days=1)
        label_fmt = "%d.%m"
    else:
        slot_count = 52
        slot_delta = timedelta(weeks=1)
        label_fmt = "%d.%m.%y"

    slot_start_global = since if since else (now - slot_delta * slot_count)
    chart_labels = []
    peak_values = []
    avg_values = []
    min_values = []

    N_SAMPLES = 10  # sample points within each slot
    for i in range(slot_count):
        sl_start = slot_start_global + slot_delta * i
        sl_end   = sl_start + slot_delta
        active = [
            s for s in sessions
            if s.login_time < sl_end and (s.logout_time is None or s.logout_time > sl_start)
        ]
        chart_labels.append(sl_start.strftime(label_fmt))
        if not active:
            peak_values.append(0)
            avg_values.append(0)
            min_values.append(0)
            continue
        slot_secs = slot_delta.total_seconds()
        samples = []
        for j in range(N_SAMPLES):
            t = sl_start + timedelta(seconds=slot_secs * j / N_SAMPLES)
            cnt = sum(
                1 for s in active
                if s.login_time <= t and (s.logout_time is None or s.logout_time >= t)
            )
            samples.append(cnt)
        peak_values.append(max(samples))
        avg_values.append(round(sum(samples) / len(samples), 1))
        min_values.append(min(samples))

    # Top active players in period
    top_players_raw = db.execute(text(
        "SELECT ps.player_id, pd.player_last_time_name, pd.player_steamid, "
        "       SUM(ps.session_duration) as total_secs, COUNT(*) as sessions "
        "FROM player_sessions ps "
        "LEFT JOIN players_data pd ON pd.player_id = ps.player_id "
        + ("WHERE ps.login_time >= :since " if since else "")
        + "GROUP BY ps.player_id ORDER BY total_secs DESC LIMIT 20"
    ), {"since": since} if since else {}).fetchall()

    # ── Config (editable fields only) ────────────────────────────────────────
    editable_cfg = _get_editable_config(current_admin)

    # ── Global twink pairs ────────────────────────────────────────────────────
    twink_svc = TwinkDetectionService(db)
    all_twinks = twink_svc.get_all_twink_pairs(min_signals=1)
    twink_per_page = 30
    twink_total = len(all_twinks)
    twink_pages = max(1, (twink_total + twink_per_page - 1) // twink_per_page)
    twink_page = max(1, min(twink_page, twink_pages))
    twinks_paged = all_twinks[(twink_page - 1) * twink_per_page: twink_page * twink_per_page]

    return templates.TemplateResponse("admin/administration.html", {
        "request": request,
        "admin": current_admin,
        "flashes": pop_flashes(request),
        "period": period,
        # Stats
        "unique_players": unique_players,
        "total_sessions": total_sessions,
        "avg_session_mins": avg_session_mins,
        "peak_concurrent": peak_concurrent,
        "chart_labels": chart_labels,
        "peak_values": peak_values,
        "avg_values": avg_values,
        "min_values": min_values,
        "top_players": top_players_raw,
        # Config
        "editable_cfg": editable_cfg,
        # Twinks
        "twinks": twinks_paged,
        "twink_page": twink_page,
        "twink_pages": twink_pages,
        "twink_total": twink_total,
    })


SUPERUSER_ONLY_CFG_FIELDS = {
    "onlyoffice_document_server_url", "onlyoffice_callback_base_url", "onlyoffice_jwt_secret",
    # Pterodactyl bearer token grants real power over game servers (department
    # test servers + the Sub-projects "Сервер 2"/ASPER2 control panel).
    "pterodactyl_panel_url", "pterodactyl_api_key", "sub_projects_test_server_uuid",
    # Обновление кода из git: подменив каталог, remote или ветку, можно
    # заставить сервер выполнить чужой код. Это строго суперпользовательское,
    # даже несмотря на то, что саму команду `update` запускают из консоли.
    "git_update_repo_dir", "git_update_repo_url", "git_update_remote", "git_update_branch",
    "git_update_build_frontend", "git_update_restart_backend",
    "git_update_token", "git_update_token_username", "git_update_npm_path",
    "git_update_frontend_dist_branch",
    # The dossier bridge is a server-side credential and transport setting.
    # Keep it superuser-only even though the AI center itself is visible to
    # ordinary moderators.
    "ai_dossier_url", "ai_dossier_api_key", "ai_dossier_tls_ca_file",
    "deepgram_api_key", "deepgram_model", "deepgram_language",
}

# Любой секрет с этой страницы отдаётся наружу ЗАГЛУШКОЙ, а не значением:
# клиент не должен получать пароли/токены/вебхук-URL (в URL вшит токен),
# чтобы они не светились в состоянии страницы, логах запросов и т.п.
# Пришедшая обратно заглушка означает «не трогать» — сохраняется прежнее
# значение из .env (см. обработчик сохранения). Чтобы СТЕРЕТЬ секрет, его
# надо прислать пустой строкой.
SECRET_PLACEHOLDER = "••••••••"
MASKED_CFG_FIELDS = {
    "discord_client_secret",
    "discord_trap_webhook_url",
    "sub_projects_webhook_url",
    "sftp_server_1_password",
    "sftp_server_2_password",
    "onlyoffice_jwt_secret",
    "pterodactyl_api_key",
    "git_update_token",
    "ai_dossier_api_key",
    "deepgram_api_key",
}


def _get_editable_config(current_admin: Optional[AdminUser] = None) -> dict:
    """Return dict of editable config fields and their current values.
    ONLYOFFICE fields (one of them is a secret shared with the Document
    Server) are only ever included for superusers — everyone else gets the
    rest of the config as before."""
    import os as _os, json as _json
    # Patch broken list fields in .env before loading Settings
    env_path = ".env"
    list_keys = {"CORS_ORIGINS", "ALLOWED_IPS"}
    if _os.path.exists(env_path):
        updates = {}
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#"):
                    k, _, v = stripped.partition("=")
                    k = k.strip()
                    v = v.strip()
                    if k in list_keys and v and not v.startswith("["):
                        # comma-separated → JSON array
                        items = [x.strip() for x in v.split(",") if x.strip()]
                        updates[k] = _json.dumps(items)
        if updates:
            update_env_file(env_path, updates)
            from config import reset_settings_cache
            reset_settings_cache()

    def _mask(key: str, value: str) -> str:
        return SECRET_PLACEHOLDER if (key in MASKED_CFG_FIELDS and value) else value

    s = get_settings()
    cfg = {
        "debug": s.debug,
        "discord_trap_webhook_url": _mask("discord_trap_webhook_url", s.discord_trap_webhook_url),
        "discord_client_id": s.discord_client_id,
        "discord_client_secret": _mask("discord_client_secret", s.discord_client_secret),
        "discord_redirect_uri": s.discord_redirect_uri,
        "allowed_ips": ",".join(s.allowed_ips_list) if s.allowed_ips_list else s.allowed_ips,
        "rate_limit_enabled": s.rate_limit_enabled,
        "rate_limit_requests": s.rate_limit_requests,
        "rate_limit_window": s.rate_limit_window,
        "cors_origins": ",".join(s.cors_origins_list) if s.cors_origins_list else s.cors_origins,
        "sub_projects_webhook_url": _mask("sub_projects_webhook_url", s.sub_projects_webhook_url),
        "public_site_url": s.public_site_url,
        "sftp_server_1_name": s.sftp_server_1_name,
        "sftp_server_1_host": s.sftp_server_1_host,
        "sftp_server_1_port": s.sftp_server_1_port,
        "sftp_server_1_username": s.sftp_server_1_username,
        "sftp_server_1_password": _mask("sftp_server_1_password", s.sftp_server_1_password),
        "sftp_server_1_key_path": s.sftp_server_1_key_path,
        "sftp_server_1_remote_dir": s.sftp_server_1_remote_dir,
        "sftp_server_2_name": s.sftp_server_2_name,
        "sftp_server_2_host": s.sftp_server_2_host,
        "sftp_server_2_port": s.sftp_server_2_port,
        "sftp_server_2_username": s.sftp_server_2_username,
        "sftp_server_2_password": _mask("sftp_server_2_password", s.sftp_server_2_password),
        "sftp_server_2_key_path": s.sftp_server_2_key_path,
        "sftp_server_2_remote_dir": s.sftp_server_2_remote_dir,
        "sftp_server_1_logs_dir": s.sftp_server_1_logs_dir,
        "sftp_server_2_logs_dir": s.sftp_server_2_logs_dir,
        "sub_projects_discord_channel_id": s.sub_projects_discord_channel_id,
    }
    if current_admin is not None and current_admin.is_superuser:
        cfg.update({
            "onlyoffice_document_server_url": s.onlyoffice_document_server_url,
            "onlyoffice_callback_base_url": s.onlyoffice_callback_base_url,
            "onlyoffice_jwt_secret": _mask("onlyoffice_jwt_secret", s.onlyoffice_jwt_secret),
            "pterodactyl_panel_url": s.pterodactyl_panel_url,
            "pterodactyl_api_key": _mask("pterodactyl_api_key", s.pterodactyl_api_key),
            "sub_projects_test_server_uuid": s.sub_projects_test_server_uuid,
            "git_update_repo_dir": s.git_update_repo_dir,
            "git_update_repo_url": s.git_update_repo_url,
            "git_update_remote": s.git_update_remote,
            "git_update_branch": s.git_update_branch,
            "git_update_token": SECRET_PLACEHOLDER if s.git_update_token else "",
            "git_update_token_username": s.git_update_token_username,
            "git_update_build_frontend": s.git_update_build_frontend,
            "git_update_restart_backend": s.git_update_restart_backend,
            "git_update_npm_path": s.git_update_npm_path,
            "git_update_frontend_dist_branch": s.git_update_frontend_dist_branch,
            "ai_dossier_url": s.ai_dossier_url,
            "ai_dossier_api_key": _mask("ai_dossier_api_key", s.ai_dossier_api_key),
            "ai_dossier_tls_ca_file": s.ai_dossier_tls_ca_file,
            "deepgram_api_key": _mask("deepgram_api_key", s.deepgram_api_key),
            "deepgram_model": s.deepgram_model,
            "deepgram_language": s.deepgram_language,
        })
    return cfg


@router.get("/api/v1/admin/administration")
async def api_admin_administration(
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    """Только конфигурация (.env) — статистика убрана отсюда: она дублировала
    (сильно менее эффективно) уже существующую страницу «Аналитика», и была
    основной причиной, по которой открытие «Администрирования» на несколько
    секунд подвешивало ВЕСЬ сайт для всех.

    До этой правки здесь синхронно, прямо в теле async-обработчика (то есть
    БЛОКИРУЯ event loop uvicorn целиком — ни один другой запрос от любого
    пользователя не обслуживался, пока это не закончится), выполнялись:
      - db.query(PlayerSession).all() без индекса на login_time (полное
        сканирование растущей на каждый вход/выход таблицы);
      - вложенные Python-циклы O(слотов × сэмплов × сессий) для построения
        графика "онлайн за период" — то же самое, что services_analytics.py
        уже делает одним GROUP BY в SQL;
      - TwinkDetectionService.get_all_twink_pairs() — self-JOIN по
        player_ips на КАЖДОЙ загрузке страницы, результат которого фронтенд
        при этом вообще нигде не отображал (мёртрая выдача, посчитанная
        зря).
    Именно эта комбинация (полный скан + O(n²)-подобные циклы + self-join,
    всё без await/run_in_threadpool) и была наиболее вероятной причиной
    полных зависаний сайта на 5-10 секунд при открытии этой страницы."""
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    return {"editable_cfg": _get_editable_config(current_admin)}


@router.post("/api/v1/admin/administration/config")
async def api_admin_administration_config_save(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)

    body = await request.json()
    env_path = ".env"
    updates: dict = {}

    field_map = {
        "debug": "DEBUG", "discord_trap_webhook_url": "DISCORD_TRAP_WEBHOOK_URL",
        "discord_client_id": "DISCORD_CLIENT_ID", "discord_client_secret": "DISCORD_CLIENT_SECRET",
        "discord_redirect_uri": "DISCORD_REDIRECT_URI", "allowed_ips": "ALLOWED_IPS",
        "rate_limit_enabled": "RATE_LIMIT_ENABLED", "rate_limit_requests": "RATE_LIMIT_REQUESTS",
        "rate_limit_window": "RATE_LIMIT_WINDOW", "cors_origins": "CORS_ORIGINS",
        "sub_projects_webhook_url": "SUB_PROJECTS_WEBHOOK_URL",
        "public_site_url": "PUBLIC_SITE_URL",
        "sftp_server_1_name": "SFTP_SERVER_1_NAME", "sftp_server_1_host": "SFTP_SERVER_1_HOST",
        "sftp_server_1_port": "SFTP_SERVER_1_PORT", "sftp_server_1_username": "SFTP_SERVER_1_USERNAME",
        "sftp_server_1_password": "SFTP_SERVER_1_PASSWORD", "sftp_server_1_key_path": "SFTP_SERVER_1_KEY_PATH",
        "sftp_server_1_remote_dir": "SFTP_SERVER_1_REMOTE_DIR",
        "sftp_server_2_name": "SFTP_SERVER_2_NAME", "sftp_server_2_host": "SFTP_SERVER_2_HOST",
        "sftp_server_2_port": "SFTP_SERVER_2_PORT", "sftp_server_2_username": "SFTP_SERVER_2_USERNAME",
        "sftp_server_2_password": "SFTP_SERVER_2_PASSWORD", "sftp_server_2_key_path": "SFTP_SERVER_2_KEY_PATH",
        "sftp_server_2_remote_dir": "SFTP_SERVER_2_REMOTE_DIR",
        "sftp_server_1_logs_dir": "SFTP_SERVER_1_LOGS_DIR", "sftp_server_2_logs_dir": "SFTP_SERVER_2_LOGS_DIR",
        "sub_projects_discord_channel_id": "SUB_PROJECTS_DISCORD_CHANNEL_ID",
        "onlyoffice_document_server_url": "ONLYOFFICE_DOCUMENT_SERVER_URL",
        "onlyoffice_callback_base_url": "ONLYOFFICE_CALLBACK_BASE_URL",
        "onlyoffice_jwt_secret": "ONLYOFFICE_JWT_SECRET",
        "pterodactyl_panel_url": "PTERODACTYL_PANEL_URL",
        "pterodactyl_api_key": "PTERODACTYL_API_KEY",
        "sub_projects_test_server_uuid": "SUB_PROJECTS_TEST_SERVER_UUID",
        "git_update_repo_dir": "GIT_UPDATE_REPO_DIR",
        "git_update_repo_url": "GIT_UPDATE_REPO_URL",
        "git_update_remote": "GIT_UPDATE_REMOTE",
        "git_update_branch": "GIT_UPDATE_BRANCH",
        "git_update_token": "GIT_UPDATE_TOKEN",
        "git_update_token_username": "GIT_UPDATE_TOKEN_USERNAME",
        "git_update_build_frontend": "GIT_UPDATE_BUILD_FRONTEND",
        "git_update_restart_backend": "GIT_UPDATE_RESTART_BACKEND",
        "git_update_npm_path": "GIT_UPDATE_NPM_PATH",
        "git_update_frontend_dist_branch": "GIT_UPDATE_FRONTEND_DIST_BRANCH",
        "ai_dossier_url": "AI_DOSSIER_URL",
        "ai_dossier_api_key": "AI_DOSSIER_API_KEY",
        "ai_dossier_tls_ca_file": "AI_DOSSIER_TLS_CA_FILE",
        "deepgram_api_key": "DEEPGRAM_API_KEY",
        "deepgram_model": "DEEPGRAM_MODEL",
        "deepgram_language": "DEEPGRAM_LANGUAGE",
    }

    for field, env_key in field_map.items():
        if field not in body:
            # This endpoint is also used for small, focused settings cards
            # (for example the AI center). Missing fields must remain
            # untouched; writing them as empty strings corrupts numeric
            # settings and turns a partial save into a site-wide outage.
            continue
        if field in SUPERUSER_ONLY_CFG_FIELDS and not current_admin.is_superuser:
            # Настройки ONLYOFFICE/Pterodactyl (секреты и серверные UUID)
            # меняют только суперпользователи — молча пропускаем поле, даже
            # если оно затесалось в тело запроса.
            continue
        val = body[field]
        if field in MASKED_CFG_FIELDS and (val is None or str(val) == SECRET_PLACEHOLDER):
            # Пришла заглушка (поле не трогали) или поля нет в запросе вовсе —
            # оставляем то, что уже лежит в .env. Иначе обычное сохранение
            # страницы затирало бы секрет заглушкой или пустой строкой.
            # Чтобы СТЕРЕТЬ токен, поле надо прислать пустой строкой.
            continue
        if val is None:
            if field in ("debug", "rate_limit_enabled",
                         "git_update_build_frontend", "git_update_restart_backend"):
                val = "false"
        elif val is True or val in ("on", "1", "true", "yes"):
            val = "true"
        elif val is False or val in ("off", "0", "false", "no"):
            val = "false"

        if field in ("cors_origins", "allowed_ips"):
            raw = str(val or "").strip()
            items = [x.strip() for x in raw.split(",") if x.strip()]
            updates[env_key] = json.dumps(items)
        else:
            updates[env_key] = str(val) if val is not None else ""

    update_env_file(env_path, updates)
    from config import reset_settings_cache
    reset_settings_cache()
    log_admin_action(db, current_admin.id, "save_config", {})
    return {"status": "ok"}


@router.post("/adminold/administration/config")
async def admin_administration_config_save(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Save editable config fields to .env file."""
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    form = await request.form()

    # Update only fields submitted by the legacy form. The helper preserves
    # comments, ordering, unknown keys and line endings in .env.
    env_path = ".env"
    updates: dict = {}

    # Map form fields → env keys
    field_map = {
        "debug": "DEBUG",
        "discord_trap_webhook_url": "DISCORD_TRAP_WEBHOOK_URL",
        "discord_client_id": "DISCORD_CLIENT_ID",
        "discord_client_secret": "DISCORD_CLIENT_SECRET",
        "discord_redirect_uri": "DISCORD_REDIRECT_URI",
        "allowed_ips": "ALLOWED_IPS",
        "rate_limit_enabled": "RATE_LIMIT_ENABLED",
        "rate_limit_requests": "RATE_LIMIT_REQUESTS",
        "rate_limit_window": "RATE_LIMIT_WINDOW",
        "cors_origins": "CORS_ORIGINS",
        "sub_projects_webhook_url": "SUB_PROJECTS_WEBHOOK_URL",
        "sftp_server_1_name": "SFTP_SERVER_1_NAME",
        "sftp_server_1_host": "SFTP_SERVER_1_HOST",
        "sftp_server_1_port": "SFTP_SERVER_1_PORT",
        "sftp_server_1_username": "SFTP_SERVER_1_USERNAME",
        "sftp_server_1_password": "SFTP_SERVER_1_PASSWORD",
        "sftp_server_1_key_path": "SFTP_SERVER_1_KEY_PATH",
        "sftp_server_1_remote_dir": "SFTP_SERVER_1_REMOTE_DIR",
        "sftp_server_2_name": "SFTP_SERVER_2_NAME",
        "sftp_server_2_host": "SFTP_SERVER_2_HOST",
        "sftp_server_2_port": "SFTP_SERVER_2_PORT",
        "sftp_server_2_username": "SFTP_SERVER_2_USERNAME",
        "sftp_server_2_password": "SFTP_SERVER_2_PASSWORD",
        "sftp_server_2_key_path": "SFTP_SERVER_2_KEY_PATH",
        "sftp_server_2_remote_dir": "SFTP_SERVER_2_REMOTE_DIR",
        "sftp_server_1_logs_dir": "SFTP_SERVER_1_LOGS_DIR",
        "sftp_server_2_logs_dir": "SFTP_SERVER_2_LOGS_DIR",
        "sub_projects_discord_channel_id": "SUB_PROJECTS_DISCORD_CHANNEL_ID",
        "onlyoffice_document_server_url": "ONLYOFFICE_DOCUMENT_SERVER_URL",
        "onlyoffice_callback_base_url": "ONLYOFFICE_CALLBACK_BASE_URL",
        "onlyoffice_jwt_secret": "ONLYOFFICE_JWT_SECRET",
        "deepgram_api_key": "DEEPGRAM_API_KEY",
        "deepgram_model": "DEEPGRAM_MODEL",
        "deepgram_language": "DEEPGRAM_LANGUAGE",
    }

    for field, env_key in field_map.items():
        if field in SUPERUSER_ONLY_CFG_FIELDS and not current_admin.is_superuser:
            continue
        val = form.get(field)
        if field in MASKED_CFG_FIELDS and (val is None or str(val) == SECRET_PLACEHOLDER):
            # Пришла заглушка (поле не трогали) или поля нет в форме вовсе —
            # оставляем то, что уже лежит в .env. Иначе сохранение страницы
            # затирало бы секрет заглушкой. Чтобы СТЕРЕТЬ секрет, его надо
            # прислать пустой строкой.
            continue
        if val is None:
            # checkboxes that are unchecked won't appear in form
            if field in ("debug", "rate_limit_enabled",
                         "git_update_build_frontend", "git_update_restart_backend"):
                val = "false"
        else:
            # normalize checkboxes
            if val in ("on", "1", "true", "yes"):
                val = "true"
            elif val in ("off", "0", "false", "no"):
                val = "false"

        # List fields: store as JSON array in .env
        if field in ("cors_origins", "allowed_ips"):
            raw = (val or "").strip()
            items = [x.strip() for x in raw.split(",") if x.strip()]
            import json as _json
            updates[env_key] = _json.dumps(items)
        else:
            updates[env_key] = val or ""

    # Write back
    try:
        update_env_file(env_path, updates)
        # Invalidate settings cache
        from config import reset_settings_cache
        reset_settings_cache()
        push_flash(request, "success", "Конфигурация сохранена. Перезапустите сервер для применения части настроек.")
    except Exception as e:
        push_flash(request, "error", f"Ошибка сохранения: {e}")

    log_admin_action(db, current_admin.id, "save_config", {})
    return RedirectResponse(url="/adminold/administration", status_code=302)


# ════════════════════════════════════════════════════════════════════════════
# PUNISHMENTS
# ════════════════════════════════════════════════════════════════════════════

@router.get("/adminold/api/players/search")
async def api_players_search(
    q: str = Query("", min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    from fastapi.responses import JSONResponse
    from sqlalchemy import or_
    if not q or len(q) < 2:
        return JSONResponse([])
    like = f"%{q}%"
    rows = (
        db.query(PlayerData)
        .filter(or_(
            PlayerData.player_last_time_name.ilike(like),
            PlayerData.player_steamid.ilike(like),
        ))
        .order_by(PlayerData.player_last_time_name)
        .limit(15)
        .all()
    )
    return JSONResponse([
        {
            "id": r.player_id,
            "name": r.player_last_time_name or "",
            "steamid": r.player_steamid or "",
        }
        for r in rows
    ])


@router.get("/adminold/punishments")
async def admin_punishments(
    request: Request,
    tab: str = Query("warning"),
    search: str = Query(""),
    page: int = Query(1),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "view"):
        raise HTTPException(status_code=403)
    if tab not in PUNISHMENT_TYPES:
        tab = "warning"

    per_page = 50
    svc = PunishmentService(db)
    rows, total = svc.list_by_type(tab, search=search, limit=per_page, offset=(page - 1) * per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))

    # annotate status
    enriched = []
    for p, player in rows:
        enriched.append({
            "p": p,
            "player": player,
            "status": svc.effective_status(p),
        })

    return templates.TemplateResponse("admin/punishments.html", {
        "request": request,
        "admin": current_admin,
        "flashes": pop_flashes(request),
        "tab": tab,
        "search": search,
        "rows": enriched,
        "total": total,
        "page": page,
        "pages": pages,
        "type_labels": PUNISHMENT_TYPE_LABELS,
        "punishment_types": PUNISHMENT_TYPES,
    })


@router.post("/adminold/punishments/create")
async def admin_punishment_create(
    request: Request,
    player_steamid: str = Form(...),
    ptype: str = Form(...),
    reason: str = Form(""),
    expires_days: Optional[int] = Form(None),
    notes: str = Form(""),
    server_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "issue"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    if ptype not in PUNISHMENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid type")

    player_steamid = player_steamid.strip()
    if not player_steamid:
        raise HTTPException(status_code=400, detail="SteamID is required")

    # Convert Steam64 → STEAM2 for server actions; store Steam64 as-is
    from utils import steam64_to_steam2
    steam2 = steam64_to_steam2(player_steamid)

    player = db.query(PlayerData).filter(PlayerData.player_steamid == player_steamid).first()
    player_id = player.player_id if player else 0

    from services import _now_msk
    expires_at = None
    if expires_days and expires_days > 0:
        from datetime import timedelta
        expires_at = _now_msk() + timedelta(days=expires_days)

    svc = PunishmentService(db)
    punishment = svc.create(
        player_id=player_id,
        player_steamid=player_steamid,
        player_ip=player.last_ip if player else None,
        ptype=ptype,
        reason=reason or None,
        issued_by_admin_id=current_admin.id,
        issued_by_name=f"ND. {current_admin.discord_username}",
        issued_by_was_superuser=current_admin.is_superuser,
        expires_at=expires_at,
        notes=notes or None,
    )

    # For bans and kicks — queue server action
    if ptype in ("ban", "kick"):
        from models import ServerAction
        from utils import steam64_to_steam2
        steam2_for_action = steam64_to_steam2(player_steamid)
        action = ServerAction(
            action_type=ptype,
            target_steamid=steam2_for_action or player_steamid,
            reason=reason or f"{PUNISHMENT_TYPE_LABELS.get(ptype, ptype)} через панель",
            target_server=server_name or None,
            queued_by_admin_id=current_admin.id,
        )
        # If targeted, mark other server as not applicable
        if server_name and server_name.upper() == "ASPER1":
            action.status_asper2 = 1
        elif server_name and server_name.upper() == "ASPER2":
            action.status_asper1 = 1
        db.add(action)
        db.commit()

    log_admin_action(db, current_admin.id, "punishment_create",
                     {"punishment_id": punishment.id, "type": ptype, "player_id": player_id})
    push_flash(request, "success", f"{PUNISHMENT_TYPE_LABELS.get(ptype, ptype)} выдан(о).")
    return RedirectResponse(url=f"/adminold/players/{player_id}", status_code=302)


@router.post("/adminold/punishments/{punishment_id}/edit")
async def admin_punishment_edit(
    request: Request,
    punishment_id: int,
    reason: str = Form(""),
    expires_days: Optional[int] = Form(None),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)

    from services import _now_msk
    expires_at = None
    if expires_days and expires_days > 0:
        from datetime import timedelta
        expires_at = _now_msk() + timedelta(days=expires_days)

    svc.update(punishment_id,
               reason=reason or None,
               expires_at=expires_at,
               notes=notes or None)

    log_admin_action(db, current_admin.id, "punishment_edit", {"punishment_id": punishment_id})
    push_flash(request, "success", "Наказание обновлено.")
    tab = p.type
    return RedirectResponse(url=f"/adminold/punishments?tab={tab}", status_code=302)


@router.post("/adminold/punishments/{punishment_id}/void")
async def admin_punishment_void(
    request: Request,
    punishment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "revoke"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    tab = p.type
    svc.set_void(punishment_id)
    log_admin_action(db, current_admin.id, "punishment_void", {"punishment_id": punishment_id})
    push_flash(request, "info", "Наказание помечено неактуальным.")
    return RedirectResponse(url=f"/adminold/punishments?tab={tab}", status_code=302)


@router.post("/adminold/punishments/{punishment_id}/unban")
async def admin_punishment_unban(
    request: Request,
    punishment_id: int,
    server_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """
    Разбанить игрока: пометить бан неактивным + поставить задачу 'unban' в очередь действий.
    server_name: имя сервера (ASPER1/ASPER2/null=все).
    """
    if not check_permission(current_admin, "punishments", "revoke"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    if p.type != "ban":
        push_flash(request, "error", "Разбан возможен только для банов")
        return RedirectResponse(url=f"/adminold/punishments?tab={p.type}", status_code=302)

    from utils import steam64_to_steam2
    steamid = p.player_steamid or ""
    steam2 = steam64_to_steam2(steamid) if steamid else None

    voided_count = svc.set_all_active_bans_void(steamid)

    from models import ServerAction
    action = ServerAction(
        action_type="unban",
        target_steamid=steam2 or steamid,
        reason=f"Unban by ND. {current_admin.discord_username}",
        target_server=server_name or None,
        queued_by_admin_id=current_admin.id,
    )
    # Mark non-targeted server as not applicable
    if server_name and server_name.upper() == "ASPER1":
        action.status_asper2 = 1
    elif server_name and server_name.upper() == "ASPER2":
        action.status_asper1 = 1
    db.add(action)
    db.commit()

    srv_label = f" → {server_name}" if server_name else " → все серверы"
    log_admin_action(db, current_admin.id, "punishment_unban", {
        "punishment_id": punishment_id,
        "steamid": steamid,
        "server_name": server_name,
        "action_id": action.id,
        "voided_punishments": voided_count,
    })
    push_flash(request, "success", f"Разбан поставлен в очередь (action_id={action.id}){srv_label}")
    return RedirectResponse(url=f"/adminold/punishments?tab=ban", status_code=302)


@router.post("/adminold/punishments/{punishment_id}/delete")
async def admin_punishment_delete(
    request: Request,
    punishment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    # Only superuser can permanently delete punishments
    if not current_admin.is_superuser:
        push_flash(request, "error", "Удаление наказаний доступно только суперпользователю")
        return RedirectResponse(url="/adminold/punishments", status_code=302)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    tab = p.type
    svc.delete(punishment_id)
    log_admin_action(db, current_admin.id, "punishment_delete", {"punishment_id": punishment_id})
    push_flash(request, "success", "Наказание удалено.")
    return RedirectResponse(url=f"/adminold/punishments?tab={tab}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# PUNISHMENTS JSON API (for the React SPA — mirrors the form-based routes above)
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_punishment_full(p, player) -> dict:
    return {
        "id": p.id,
        "player_id": p.player_id,
        "player_steamid": p.player_steamid,
        # Forced display name wins regardless of how the punishment was
        # issued (game-server API or admin panel) — resolved at display time.
        "player_name": (player.forced_display_name or player.player_last_time_name) if player else None,
        "player_actual_name": player.player_last_time_name if player else None,
        "player_forced_name": player.forced_display_name if player else None,
        "type": p.type,
        "reason": p.reason,
        "issued_by_name": p.issued_by_name,
        "issued_by_was_superuser": bool(p.issued_by_was_superuser),
        "issued_at": p.issued_at.isoformat() if p.issued_at else None,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "is_active": bool(p.is_active),
        "notes": p.notes,
    }


@router.get("/api/v1/admin/punishments")
async def api_punishments_list(
    tab: str = Query("warning"),
    search: str = Query(""),
    status: str = Query("", description="active | expired | void | '' (все)"),
    superuser_only: bool = Query(False),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "view"):
        raise HTTPException(status_code=403)
    if tab not in PUNISHMENT_TYPES:
        tab = "warning"
    if status not in ("", "active", "expired", "void"):
        status = ""

    per_page = 50
    svc = PunishmentService(db)
    rows, total = svc.list_by_type(
        tab, search=search, status=status, superuser_only=superuser_only,
        limit=per_page, offset=(page - 1) * per_page,
    )
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))

    enriched = []
    for p, player in rows:
        row = _serialize_punishment_full(p, player)
        row["status"] = svc.effective_status(p)
        enriched.append(row)

    return {
        "tab": tab,
        "search": search,
        "status": status,
        "superuser_only": superuser_only,
        "rows": enriched,
        "total": total,
        "page": page,
        "pages": pages,
        "type_labels": PUNISHMENT_TYPE_LABELS,
        "punishment_types": list(PUNISHMENT_TYPES),
        "can_edit": check_permission(current_admin, "punishments", "edit"),
        "can_delete": current_admin.is_superuser,
    }


@router.post("/api/v1/admin/punishments")
async def api_punishment_create(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "issue"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    body = await request.json()
    player_steamid = (body.get("player_steamid") or "").strip()
    ptype = body.get("ptype")
    reason = body.get("reason") or ""
    expires_days = body.get("expires_days")
    notes = body.get("notes") or ""
    server_name = body.get("server_name")

    if ptype not in PUNISHMENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid type")
    if not player_steamid:
        raise HTTPException(status_code=400, detail="SteamID is required")

    player = db.query(PlayerData).filter(PlayerData.player_steamid == player_steamid).first()
    player_id = player.player_id if player else 0

    from services import _now_msk
    expires_at = None
    if expires_days and expires_days > 0:
        expires_at = _now_msk() + timedelta(days=expires_days)

    svc = PunishmentService(db)
    punishment = svc.create(
        player_id=player_id,
        player_steamid=player_steamid,
        player_ip=player.last_ip if player else None,
        ptype=ptype,
        reason=reason or None,
        issued_by_admin_id=current_admin.id,
        issued_by_name=f"ND. {current_admin.discord_username}",
        issued_by_was_superuser=current_admin.is_superuser,
        expires_at=expires_at,
        notes=notes or None,
    )

    if ptype in ("ban", "kick"):
        from models import ServerAction
        from utils import steam64_to_steam2
        steam2_for_action = steam64_to_steam2(player_steamid)
        action = ServerAction(
            action_type=ptype,
            target_steamid=steam2_for_action or player_steamid,
            reason=reason or f"{PUNISHMENT_TYPE_LABELS.get(ptype, ptype)} через панель",
            target_server=server_name or None,
            queued_by_admin_id=current_admin.id,
        )
        if server_name and server_name.upper() == "ASPER1":
            action.status_asper2 = 1
        elif server_name and server_name.upper() == "ASPER2":
            action.status_asper1 = 1
        db.add(action)
        db.commit()

    log_admin_action(db, current_admin.id, "punishment_create",
                     {"punishment_id": punishment.id, "type": ptype, "player_id": player_id})
    # п.5: наказания с сайта — сразу в Discord (оба канала)
    try:
        from routes.game_api import _queue_punishment_discord_notification
        _queue_punishment_discord_notification(db, punishment)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to queue punishment discord notification for {punishment.id}: {e}")
    row = _serialize_punishment_full(punishment, player)
    row["status"] = svc.effective_status(punishment)
    return {"status": "ok", "punishment": row, "player_id": player_id}


@router.post("/api/v1/admin/punishments/{punishment_id}")
async def api_punishment_edit(
    request: Request,
    punishment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)

    body = await request.json()
    from services import _now_msk
    expires_days = body.get("expires_days")
    expires_at = None
    if expires_days and expires_days > 0:
        expires_at = _now_msk() + timedelta(days=expires_days)

    svc.update(punishment_id,
               reason=body.get("reason") or None,
               expires_at=expires_at,
               notes=body.get("notes") or None)

    log_admin_action(db, current_admin.id, "punishment_edit", {"punishment_id": punishment_id})
    return {"status": "ok"}


@router.post("/api/v1/admin/punishments/{punishment_id}/void")
async def api_punishment_void(
    request: Request,
    punishment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "revoke"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    svc.set_void(punishment_id)
    log_admin_action(db, current_admin.id, "punishment_void", {"punishment_id": punishment_id})
    return {"status": "ok"}


@router.post("/api/v1/admin/punishments/{punishment_id}/unban")
async def api_punishment_unban(
    request: Request,
    punishment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "revoke"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    body = await request.json()
    server_name = body.get("server_name")

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    if p.type != "ban":
        raise HTTPException(status_code=400, detail="Разбан возможен только для банов")

    from utils import steam64_to_steam2
    steamid = p.player_steamid or ""
    steam2 = steam64_to_steam2(steamid) if steamid else None

    voided_count = svc.set_all_active_bans_void(steamid)

    from models import ServerAction
    action = ServerAction(
        action_type="unban",
        target_steamid=steam2 or steamid,
        reason=f"Unban by ND. {current_admin.discord_username}",
        target_server=server_name or None,
        queued_by_admin_id=current_admin.id,
    )
    if server_name and server_name.upper() == "ASPER1":
        action.status_asper2 = 1
    elif server_name and server_name.upper() == "ASPER2":
        action.status_asper1 = 1
    db.add(action)
    db.commit()

    log_admin_action(db, current_admin.id, "punishment_unban", {
        "punishment_id": punishment_id, "steamid": steamid, "server_name": server_name,
        "action_id": action.id, "voided_punishments": voided_count,
    })
    return {"status": "ok", "action_id": action.id, "voided_punishments": voided_count}


@router.delete("/api/v1/admin/punishments/{punishment_id}")
async def api_punishment_delete(
    request: Request,
    punishment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Удаление наказаний доступно только суперпользователю")
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    svc.delete(punishment_id)
    log_admin_action(db, current_admin.id, "punishment_delete", {"punishment_id": punishment_id})
    return {"status": "ok"}


# ── DEBUG DIAGNOSTICS ────────────────────────────────────────────────────────

@router.get("/debug")
async def admin_debug(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Detailed permission diagnostics for troubleshooting access issues."""
    import json
    
    # Build diagnostic info
    diagnostics = {
        "admin_id": current_admin.id,
        "discord_username": current_admin.discord_username,
        "is_superuser": current_admin.is_superuser,
        "privilege_group_id": current_admin.privilege_group_id,
        "individual_permissions": current_admin.individual_permissions or {},
    }
    
    # Include detailed group info
    grp_info = None
    grp_raw_permissions = None
    if current_admin.privilege_group:
        grp = current_admin.privilege_group
        grp_raw_permissions = grp.permissions
        grp_info = {
            "id": grp.id,
            "name": grp.name,
            "description": grp.description,
            "permissions_type": str(type(grp_raw_permissions).__name__),
            "permissions_is_none": grp_raw_permissions is None,
            "permissions_is_empty": not grp_raw_permissions if grp_raw_permissions else True,
            "permissions_raw": grp_raw_permissions,
            "permissions_formatted": json.dumps(grp_raw_permissions, ensure_ascii=False, indent=2) if grp_raw_permissions else "null",
            "rate_limit_count": grp.rate_limit_count,
            "rate_limit_window_secs": grp.rate_limit_window_secs,
        }
    
    diagnostics["privilege_group"] = grp_info
    
    # Test all permission checks with detailed tracing — sourced from the
    # single permissions_registry.py instead of a hand-maintained page/action
    # list that had already drifted from the real page set (missing/extra
    # pages, and a fixed view/edit/delete triad that doesn't reflect the
    # custom actions several pages actually use).
    from permissions_registry import PERMISSION_REGISTRY

    permission_tests = {}
    permission_traces = {}
    for page, page_def in PERMISSION_REGISTRY.items():
        permission_tests[page] = {}
        permission_traces[page] = {}
        for action_def in page_def["actions"]:
            action = action_def["key"]
            result = check_permission(current_admin, page, action)
            permission_tests[page][action] = result
            
            # Trace why it returned False (if False)
            trace = []
            if current_admin.is_superuser:
                trace.append("✓ Is superuser → TRUE")
            else:
                trace.append("- Not superuser")
            
            # Check individual permissions
            ind = current_admin.individual_permissions or {}
            ind_pages = ind.get("pages", {})
            if page in ind_pages and action in ind_pages[page]:
                trace.append(f"✓ Found in individual_permissions[{page}][{action}] = {ind_pages[page][action]}")
            else:
                trace.append(f"- Not in individual_permissions")
            
            # Check group permissions
            if current_admin.privilege_group and current_admin.privilege_group.permissions:
                grp_pages = current_admin.privilege_group.permissions.get("pages", {})
                if page in grp_pages and action in grp_pages[page]:
                    trace.append(f"✓ Found in group.permissions[{page}][{action}] = {grp_pages[page][action]}")
                else:
                    trace.append(f"- Not in group.permissions (available pages: {list(grp_pages.keys())})")
            else:
                trace.append("- No privilege group or permissions is empty/null")
            
            permission_traces[page][action] = " → ".join(trace)
    
    diagnostics["permission_checks"] = permission_tests
    diagnostics["permission_traces"] = permission_traces
    
    # Return as HTML for browser viewing
    html_content = """
    <html>
    <head>
        <title>Admin Permission Diagnostics</title>
        <style>
            body { font-family: monospace; margin: 20px; background: #f5f5f5; }
            .section { background: white; padding: 15px; margin: 10px 0; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            h2 { border-bottom: 2px solid #333; padding-bottom: 10px; }
            .info-row { display: flex; margin: 8px 0; flex-wrap: wrap; }
            .label { font-weight: bold; width: 220px; }
            .value { flex: 1; min-width: 200px; word-break: break-all; }
            .true { color: green; font-weight: bold; }
            .false { color: red; font-weight: bold; }
            .warning { color: orange; font-weight: bold; }
            table { width: 100%; border-collapse: collapse; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background: #f0f0f0; }
            .pre { background: #f9f9f9; padding: 10px; border-left: 3px solid #ddd; overflow-x: auto; max-height: 300px; }
            .trace { background: #fffbea; padding: 8px; border-left: 3px solid #ffd700; font-size: 12px; margin: 4px 0; }
        </style>
    </head>
    <body>
        <h1>Admin Permission Diagnostics</h1>
        
        <div class="section">
            <h2>Admin Info</h2>
            <div class="info-row">
                <span class="label">Admin ID:</span>
                <span class="value">""" + str(diagnostics["admin_id"]) + """</span>
            </div>
            <div class="info-row">
                <span class="label">Discord Username:</span>
                <span class="value">""" + diagnostics["discord_username"] + """</span>
            </div>
            <div class="info-row">
                <span class="label">Superuser:</span>
                <span class="value """ + ('class="true"' if diagnostics["is_superuser"] else 'class="false"') + """>
                    """ + str(diagnostics["is_superuser"]) + """
                </span>
            </div>
            <div class="info-row">
                <span class="label">Privilege Group ID:</span>
                <span class="value">""" + str(diagnostics["privilege_group_id"]) + """</span>
            </div>
        </div>
        
        <div class="section">
            <h2>Privilege Group Details</h2>
    """
    
    if grp_info:
        html_content += f"""
            <div class="info-row">
                <span class="label">Group ID:</span>
                <span class="value">{grp_info["id"]}</span>
            </div>
            <div class="info-row">
                <span class="label">Group Name:</span>
                <span class="value">{grp_info["name"]}</span>
            </div>
            <div class="info-row">
                <span class="label">Description:</span>
                <span class="value">{grp_info["description"] or "N/A"}</span>
            </div>
            <div class="info-row">
                <span class="label">Permissions Type:</span>
                <span class="value">{grp_info["permissions_type"]}</span>
            </div>
            <div class="info-row">
                <span class="label">Permissions Is None:</span>
                <span class="value """ + ('class="warning"' if grp_info["permissions_is_none"] else '') + """>{str(grp_info["permissions_is_none"])}</span>
            </div>
            <div class="info-row">
                <span class="label">Permissions Is Empty:</span>
                <span class="value """ + ('class="warning"' if grp_info["permissions_is_empty"] else '') + """>{str(grp_info["permissions_is_empty"])}</span>
            </div>
            <div class="info-row">
                <span class="label">Raw Permissions:</span>
                <div class="value"><div class="pre">{grp_info["permissions_formatted"]}</div></div>
            </div>
            <div class="info-row">
                <span class="label">Rate Limit Count:</span>
                <span class="value">{grp_info["rate_limit_count"]}</span>
            </div>
            <div class="info-row">
                <span class="label">Rate Limit Window (secs):</span>
                <span class="value">{grp_info["rate_limit_window_secs"]}</span>
            </div>
        """
    else:
        html_content += "<p><strong style='color: red;'>⚠️ No privilege group assigned!</strong></p>"
    
    html_content += """
        </div>
        
        <div class="section">
            <h2>Individual Permission Overrides</h2>
    """
    
    if diagnostics["individual_permissions"]:
        html_content += f"<div class='pre'>{json.dumps(diagnostics['individual_permissions'], ensure_ascii=False, indent=2)}</div>"
    else:
        html_content += "<p>None (using group defaults)</p>"
    
    html_content += """
        </div>
        
        <div class="section">
            <h2>Permission Check Results</h2>
            <table>
                <tr>
                    <th>Page</th>
                    <th>View</th>
                    <th>Edit</th>
                    <th>Delete</th>
                </tr>
    """
    
    for page in pages:
        html_content += f"<tr><td><strong>{page}</strong></td>"
        for action in actions:
            result = permission_tests[page][action]
            html_content += f"<td class='{'true' if result else 'false'}'>{str(result)}</td>"
        html_content += "</tr>"
    
    html_content += """
            </table>
        </div>
        
        <div class="section">
            <h2>Permission Resolution Trace (for view action)</h2>
            <p>Shows step-by-step why each permission check returns True/False:</p>
    """
    
    for page in pages:
        trace = permission_traces[page].get("view", "N/A")
        result = permission_tests[page].get("view", False)
        html_content += f"<div class='trace' style='color: {"green" if result else "red"}'><strong>{page}:</strong> {trace}</div>"
    
    html_content += """
        </div>
        
        <div class="section">
            <h2>Troubleshooting Checklist</h2>
            <ul>
                <li><strong>Permissions Is Empty: True</strong> → Group has no permissions defined. Go to /admin/users → Groups → Edit group → set permissions</li>
                <li><strong>Permissions Is None: True</strong> → Same as above</li>
                <li><strong>All permission tests False</strong> → Either no group, or group.permissions is empty/null</li>
                <li><strong>Specific pages False</strong> → Check the trace to see which step failed (likely not in group.permissions dict)</li>
                <li>Expected format: <code>{"pages": {"players": {"view": true, "edit": true}}}</code></li>
                <li>If permissions look correct but still False → Try <strong>refreshing the page</strong> (F5)</li>
            </ul>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)
