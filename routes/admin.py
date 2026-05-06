from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
import secrets
from datetime import datetime, timedelta
from database import get_db
from services import (
    AdminService, DiscordAuthService, PlayerService,
    PlayerKillService, PlayerRoleActionService, PlayerIPService,
    PlayerNameService, PlayerRoleStatService, PlayerSessionService
)
from models import AdminUser, AdminSession, AdminActionLog, PlayerStats, PlayerData
from config import get_settings
import logging

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(max=max, min=min)
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
            admin = db.query(AdminUser).filter(AdminUser.id == session.admin_id).first()
            if admin:
                # Keep display-only session data synced with the authoritative DB session.
                request.session["username"] = admin.discord_username
                return admin

    raise HTTPException(status_code=401, detail="Not authenticated")


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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    """Discord login page"""
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    
    # Redirect only if server-side admin session exists and is valid.
    token = request.cookies.get("admin_token")
    if token:
        now = datetime.utcnow()
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            return RedirectResponse(url="/admin", status_code=302)

        # Stale/invalid token cookie: clear it to prevent redirect loops.
        request.session.pop("username", None)
        response = templates.TemplateResponse("login.html", {"request": request, "oauth_url": discord_auth.get_oauth_url()})
        response.delete_cookie("admin_token")
        return response

    oauth_url = discord_auth.get_oauth_url()
    return templates.TemplateResponse("login.html", {"request": request, "oauth_url": oauth_url})


@router.get("/auth/discord/callback")
async def discord_callback(
    request: Request,
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
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

    try:
        # Exchange code for token
        token_data = await discord_auth.exchange_code(code)
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

        # Update last login
        admin_service.update_last_login(discord_id)

        # Create server-side admin session and set cookie
        token = secrets.token_urlsafe(32)
        now = datetime.utcnow()
        expires = now + timedelta(seconds=settings.session_max_age)
        admin_session = AdminSession(admin_id=admin.id, token=token, created_at=now, expires_at=expires, revoked=0)
        db.add(admin_session)
        db.commit()

        response = RedirectResponse(url="/admin", status_code=302)
        response.set_cookie(
            "admin_token",
            token,
            httponly=True,
            max_age=settings.session_max_age,
            samesite="lax",
            secure=not settings.debug,
        )
        # Session keeps only non-authoritative display data; auth is cookie+DB session.
        request.session["username"] = admin.discord_username

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


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Admin dashboard"""
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "admin": current_admin,
        "flashes": pop_flashes(request),
    })


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """List admin users"""
    # Only admins can manage users
    if "admin" not in current_admin.permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    
    admin_service = AdminService(db)
    admins = admin_service.get_all_admins()
    
    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "admin": current_admin,
        "admins": admins,
        "roles": list(settings.roles_mapping.keys()),
        "flashes": pop_flashes(request),
    })


@router.post("/admin/users")
async def create_admin_user(
    request: Request,
    discord_id: str = Form(...),
    role: str = Form("admin"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Create new admin user (manual)"""
    if "admin" not in current_admin.permissions:
        push_flash(request, "error", "Недостаточно прав для создания администратора")
        return RedirectResponse(url="/admin/users", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        admin_service = AdminService(db)

        # Check if already exists
        existing = admin_service.get_admin_by_discord_id(discord_id)
        if existing:
            push_flash(request, "error", "Администратор с таким Discord ID уже существует")
            return RedirectResponse(url="/admin/users", status_code=302)

        # Create with minimal info
        permissions = settings.roles_mapping.get(role, ["read"])
        admin = AdminUser(
            discord_id=discord_id,
            discord_username=f"User_{discord_id}",
            role=role,
            permissions=permissions
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

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/admin/users/{admin_id}/update")
async def update_admin_user(
    request: Request,
    admin_id: int,
    role: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update admin user"""
    if "admin" not in current_admin.permissions:
        push_flash(request, "error", "Недостаточно прав для изменения администратора")
        return RedirectResponse(url="/admin/users", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        target_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not target_admin:
            push_flash(request, "error", "Администратор не найден")
            return RedirectResponse(url="/admin/users", status_code=302)

        old_role = target_admin.role
        if old_role == role:
            push_flash(request, "info", "Роль не изменилась")
            return RedirectResponse(url="/admin/users", status_code=302)

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
    
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/admin/users/{admin_id}/delete")
async def delete_admin_user(
    request: Request,
    admin_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Delete admin user"""
    if "admin" not in current_admin.permissions:
        push_flash(request, "error", "Недостаточно прав для удаления администратора")
        return RedirectResponse(url="/admin/users", status_code=302)

    try:
        if current_admin.id == admin_id:
            push_flash(request, "error", "Нельзя удалить собственную учетную запись")
            return RedirectResponse(url="/admin/users", status_code=302)

        await ensure_csrf_or_valid_admin_session(request, db)

        target_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not target_admin:
            push_flash(request, "error", "Администратор не найден")
            return RedirectResponse(url="/admin/users", status_code=302)

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
    
    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/admin/players", response_class=HTMLResponse)
async def admin_players_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None, min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """List players with optional search"""
    # Only users with read or write permissions can view players
    if "read" not in current_admin.permissions and "write" not in current_admin.permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    
    player_service = PlayerService(db)
    
    # If search query provided, perform full search
    if search:
        from models import PlayerName, PlayerIP
        from sqlalchemy import or_
        
        query_lower = f"%{search.lower()}%"
        # Perform comprehensive search
        player_ids = set()
        results = []
        
        # Search in PlayerData
        player_results = player_service.search_players(search, limit=1000)
        results.extend(player_results)
        player_ids.update(p.player_id for p in player_results)
        
        total = len(results)
        players = results[:page_size]
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        search_query = search
    else:
        # Normal pagination
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
        "flashes": pop_flashes(request),
    })


@router.get("/admin/players/{player_id}", response_class=HTMLResponse)
async def admin_player_detail(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Player detail page with extended data"""
    # Only users with read or write permissions can view players
    if "read" not in current_admin.permissions and "write" not in current_admin.permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    
    player_service = PlayerService(db)
    player = player_service.get_player_by_id(player_id)
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
    
    return templates.TemplateResponse("admin/player_detail.html", {
        "request": request,
        "admin": current_admin,
        "player": player,
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
    })


@router.post("/admin/players/{player_id}/update")
async def update_player(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update player data"""
    if "write" not in current_admin.permissions:
        push_flash(request, "error", "Недостаточно прав для редактирования игрока")
        return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        form = await request.form()

        player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
        if not player:
            push_flash(request, "error", "Игрок не найден")
            return RedirectResponse(url="/admin/players", status_code=302)

        rank_raw = form.get("rank_id")
        kit_raw = form.get("kit_level")
        achievements_raw = form.get("achievements")

        try:
            new_rank_id = None if rank_raw in (None, "") else int(rank_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение ранга")
            return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)

        try:
            new_kit_level = None if kit_raw in (None, "") else int(kit_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение набора")
            return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)

        new_achievements = player.achievements
        if achievements_raw is not None:
            import json
            try:
                new_achievements = json.loads(achievements_raw)
            except json.JSONDecodeError:
                push_flash(request, "error", "Некорректный JSON в поле достижений")
                return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)

        old_rank_id = player.rank_id
        old_kit_level = player.kit_level
        old_achievements = player.achievements

        changes = []
        if old_rank_id != new_rank_id:
            changes.append({"field": "rank_id", "from": old_rank_id, "to": new_rank_id})
        if old_kit_level != new_kit_level:
            changes.append({"field": "kit_level", "from": old_kit_level, "to": new_kit_level})
        if old_achievements != new_achievements:
            changes.append({"field": "achievements", "from": old_achievements, "to": new_achievements})

        if not changes:
            push_flash(request, "info", "Изменений не обнаружено")
            return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)

        player.rank_id = new_rank_id
        player.kit_level = new_kit_level
        player.achievements = new_achievements

        db.commit()
        db.refresh(player)
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

    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions_list(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """List active admin sessions"""
    if "admin" not in current_admin.permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    sessions = db.query(AdminSession).order_by(AdminSession.created_at.desc()).all()

    return templates.TemplateResponse("admin/sessions.html", {"request": request, "admin": current_admin, "sessions": sessions, "flashes": pop_flashes(request)})


@router.post("/admin/sessions/{session_id}/revoke")
async def revoke_session(request: Request, session_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Revoke a specific admin session"""
    if "admin" not in current_admin.permissions:
        push_flash(request, "error", "Недостаточно прав для отзыва сессии")
        return RedirectResponse(url="/admin/sessions", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        s = db.query(AdminSession).filter(AdminSession.id == session_id).first()
        if not s:
            push_flash(request, "error", "Сессия не найдена")
            return RedirectResponse(url="/admin/sessions", status_code=302)

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

    return RedirectResponse(url="/admin/sessions", status_code=302)


@router.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit_list(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """List audit logs"""
    if "admin" not in current_admin.permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    logs = db.query(AdminActionLog).order_by(AdminActionLog.created_at.desc()).limit(200).all()
    admins = db.query(AdminUser).all()
    admin_lookup = {a.id: a.discord_username for a in admins}
    return templates.TemplateResponse(
        "admin/audit.html",
        {
            "request": request,
            "admin": current_admin,
            "logs": logs,
            "admin_lookup": admin_lookup,
            "flashes": pop_flashes(request),
        },
    )


@router.get("/admin/server-privileges", response_class=HTMLResponse)
async def admin_server_privileges(
    request: Request,
    page: int = 1,
    search: str = "",
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Server privileges management page - manage player privileges"""
    # Check if user has permission to manage server privileges
    if not ("admin" in current_admin.permissions or 
            "manage_admins" in current_admin.server_permissions):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    
    from services import ServerPlayerPrivilegeService
    privilege_service = ServerPlayerPrivilegeService(db)
    
    # Get server privileges config
    server_privileges = settings.server_permissions_mapping
    
    # Search or list all privileges
    page = max(1, page)
    limit = settings.default_page_size
    offset = (page - 1) * limit
    
    if search:
        privileges = privilege_service.search_privileges(search, limit=1000)
        total = len(privileges)
        privileges = privileges[offset:offset+limit]
    else:
        privileges, total = privilege_service.get_all_privileges(limit=limit, offset=offset)
    
    total_pages = (total + limit - 1) // limit
    
    return templates.TemplateResponse("admin/server_privileges.html", {
        "request": request,
        "admin": current_admin,
        "privileges": privileges,
        "server_privileges": server_privileges,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "limit": limit,
        "search": search,
        "flashes": pop_flashes(request),
    })


@router.post("/admin/server-privileges/add")
async def add_server_privilege(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Add new player privilege"""
    # Check if user has permission to manage server privileges
    if not ("admin" in current_admin.permissions or 
            "manage_admins" in current_admin.server_permissions):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url="/admin/server-privileges", status_code=302)
    
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        
        form = await request.form()
        steamid = form.get("steamid", "").strip()
        selected_privileges = form.getlist("privileges")
        reason = form.get("reason", "").strip()
        
        if not steamid:
            push_flash(request, "error", "SteamID не может быть пустым")
            return RedirectResponse(url="/admin/server-privileges", status_code=302)
        
        from services import ServerPlayerPrivilegeService
        privilege_service = ServerPlayerPrivilegeService(db)
        privilege_service.grant_privilege(
            steamid=steamid,
            privileges=selected_privileges,
            reason=reason if reason else None,
            granted_by_admin_id=current_admin.id
        )
        
        log_admin_action(
            db,
            current_admin.id,
            "grant_player_privilege",
            {
                "steamid": steamid,
                "privileges": selected_privileges,
                "reason": reason
            },
        )
        push_flash(request, "success", f"Привилегии выданы игроку {steamid}")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Add privilege failed: %s", exc)
        push_flash(request, "error", "Не удалось добавить привилегию")
    
    return RedirectResponse(url="/admin/server-privileges", status_code=302)


@router.post("/admin/server-privileges/{privilege_id}/update")
async def update_server_privilege(
    request: Request,
    privilege_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update player privilege"""
    # Check if user has permission to manage server privileges
    if not ("admin" in current_admin.permissions or 
            "manage_admins" in current_admin.server_permissions):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url="/admin/server-privileges", status_code=302)
    
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        
        from models import ServerPlayerPrivilege
        from services import ServerPlayerPrivilegeService
        
        privilege_record = db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.id == privilege_id
        ).first()
        
        if not privilege_record:
            push_flash(request, "error", "Запись о привилегиях не найдена")
            return RedirectResponse(url="/admin/server-privileges", status_code=302)
        
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
    
    return RedirectResponse(url="/admin/server-privileges", status_code=302)


@router.post("/admin/server-privileges/{privilege_id}/delete")
async def delete_server_privilege(
    request: Request,
    privilege_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Delete player privilege"""
    # Check if user has permission to manage server privileges
    if not ("admin" in current_admin.permissions or 
            "manage_admins" in current_admin.server_permissions):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url="/admin/server-privileges", status_code=302)
    
    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        
        from models import ServerPlayerPrivilege
        from services import ServerPlayerPrivilegeService
        
        privilege_record = db.query(ServerPlayerPrivilege).filter(
            ServerPlayerPrivilege.id == privilege_id
        ).first()
        
        if not privilege_record:
            push_flash(request, "error", "Запись о привилегиях не найдена")
            return RedirectResponse(url="/admin/server-privileges", status_code=302)
        
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
    
    return RedirectResponse(url="/admin/server-privileges", status_code=302)


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
    if "read" not in current_admin.permissions:
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
    if "write" not in current_admin.permissions:
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
    if "write" not in current_admin.permissions:
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
# KITS MANAGEMENT ENDPOINTS
# ============================================

from models import Kit


def _require_kits_permission(current_admin: AdminUser):
    if "admin" not in current_admin.permissions and "write" not in current_admin.permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


@router.get("/admin/kits", response_class=HTMLResponse)
async def admin_kits_list(
    request: Request,
    job_id: Optional[str] = Query(None),
    kit_level: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """List and manage base kits"""
    _require_kits_permission(current_admin)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    query = db.query(Kit)
    if job_id:
        query = query.filter(Kit.job_id == job_id)
    if kit_level is not None:
        query = query.filter(Kit.kit_level == kit_level)

    kits = query.order_by(Kit.job_id, Kit.kit_level).all()

    # Unique job_ids for filter dropdown
    all_job_ids = [row[0] for row in db.query(Kit.job_id).distinct().order_by(Kit.job_id).all()]

    return templates.TemplateResponse("admin/kits.html", {
        "request": request,
        "admin": current_admin,
        "kits": kits,
        "all_job_ids": all_job_ids,
        "kits_mapping": settings.kits_mapping,
        "selected_job_id": job_id or "",
        "selected_kit_level": kit_level if kit_level is not None else "",
        "flashes": pop_flashes(request),
    })


@router.post("/admin/kits/add")
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
            return RedirectResponse(url="/admin/kits", status_code=302)

        try:
            kit_level = int(kit_level_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение уровня кита")
            return RedirectResponse(url="/admin/kits", status_code=302)

        try:
            items = json.loads(items_raw)
        except json.JSONDecodeError:
            push_flash(request, "error", "Некорректный JSON в поле предметов")
            return RedirectResponse(url="/admin/kits", status_code=302)

        # Check duplicate
        existing = db.query(Kit).filter(Kit.job_id == job_id, Kit.kit_level == kit_level).first()
        if existing:
            push_flash(request, "error", f"Кит для профессии '{job_id}' с уровнем {kit_level} уже существует")
            return RedirectResponse(url="/admin/kits", status_code=302)

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

    return RedirectResponse(url="/admin/kits", status_code=302)


@router.post("/admin/kits/{kit_id}/update")
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
            return RedirectResponse(url="/admin/kits", status_code=302)

        job_id = form.get("job_id", kit.job_id).strip()
        kit_level_raw = form.get("kit_level", str(kit.kit_level))
        items_raw = form.get("items", "[]").strip()

        if not job_id:
            push_flash(request, "error", "Профессия (job_id) не может быть пустой")
            return RedirectResponse(url="/admin/kits", status_code=302)

        try:
            kit_level = int(kit_level_raw)
        except (TypeError, ValueError):
            push_flash(request, "error", "Некорректное значение уровня кита")
            return RedirectResponse(url="/admin/kits", status_code=302)

        try:
            items = json.loads(items_raw)
        except json.JSONDecodeError:
            push_flash(request, "error", "Некорректный JSON в поле предметов")
            return RedirectResponse(url="/admin/kits", status_code=302)

        # Check duplicate (excluding self)
        conflict = db.query(Kit).filter(
            Kit.job_id == job_id,
            Kit.kit_level == kit_level,
            Kit.id != kit_id
        ).first()
        if conflict:
            push_flash(request, "error", f"Кит для профессии '{job_id}' с уровнем {kit_level} уже существует")
            return RedirectResponse(url="/admin/kits", status_code=302)

        changes = []
        if kit.job_id != job_id:
            changes.append({"field": "job_id", "from": kit.job_id, "to": job_id})
        if kit.kit_level != kit_level:
            changes.append({"field": "kit_level", "from": kit.kit_level, "to": kit_level})
        if kit.items != items:
            changes.append({"field": "items", "from": kit.items, "to": items})

        if not changes:
            push_flash(request, "info", "Изменений не обнаружено")
            return RedirectResponse(url="/admin/kits", status_code=302)

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

    return RedirectResponse(url="/admin/kits", status_code=302)


@router.post("/admin/kits/{kit_id}/delete")
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
            return RedirectResponse(url="/admin/kits", status_code=302)

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

    return RedirectResponse(url="/admin/kits", status_code=302)