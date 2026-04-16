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
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """List players"""
    player_service = PlayerService(db)
    players, total = player_service.get_all_players(page, page_size)
    
    total_pages = (total + page_size - 1) // page_size
    
    return templates.TemplateResponse("admin/players.html", {
        "request": request,
        "admin": current_admin,
        "players": players,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
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
    except:
        kills = []
    
    try:
        role_actions = role_action_service.get_player_actions(player_id)
    except:
        role_actions = []
    
    try:
        ips = ip_service.get_player_ips(player_id)
    except:
        ips = []
    
    try:
        names = name_service.get_player_names(player_id)
    except:
        names = []
    
    try:
        role_stats = role_stat_service.get_player_role_stats(player_id)
    except:
        role_stats = []
    
    try:
        sessions = session_service.get_player_sessions(player_id)
    except:
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