from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
import secrets
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
)
from models import (
    AdminUser, AdminSession, AdminActionLog, PlayerStats, PlayerData,
    LabelCategory, PlayerLabel, PlayerNote, PlayerTrap, Kit, PlayerSession,
    PlayerPunishment,
)
from config import get_settings
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


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Admin dashboard with online chart"""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    from services import _online_players, _online_players_lock

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

    # Quick stats
    unique_today = len({s.player_id for s in sessions})
    # Current online (combined from all servers, pruned of stale entries)
    from services import _prune_stale_online
    with _online_players_lock:
        _prune_stale_online()
        current_online = len(_online_players)

    # Total players
    total_players = db.query(PlayerData).count()

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "admin": current_admin,
        "flashes": pop_flashes(request),
        "chart_labels": chart_labels,
        "peak_values": peak_values,
        "avg_values": avg_values,
        "current_online": current_online,
        "unique_today": unique_today,
        "total_players": total_players,
    })


@router.get("/admin/users", response_class=HTMLResponse)
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


@router.post("/admin/users")
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
        return RedirectResponse(url="/admin/users", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)

        admin_service = AdminService(db)

        # Check if already exists
        existing = admin_service.get_admin_by_discord_id(discord_id)
        if existing:
            push_flash(request, "error", "Администратор с таким Discord ID уже существует")
            return RedirectResponse(url="/admin/users", status_code=302)

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
    if not check_permission(current_admin, "admin", "edit"):
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
    if not check_permission(current_admin, "admin", "delete"):
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

        # Guard: only superuser can delete another superuser
        if target_admin.is_superuser and not current_admin.is_superuser:
            push_flash(request, "error", "Только суперпользователь может удалить суперпользователя")
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


@router.get("/admin/players/{player_id}", response_class=HTMLResponse)
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


@router.post("/admin/players/{player_id}/update")
async def update_player(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update player data (rank/kit) - requires 'delete' or 'special' permission"""
    if not (check_permission(current_admin, "players", "delete") or check_permission(current_admin, "players", "edit") or check_permission(current_admin, "players_special", "view")):
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

        old_rank_id = player.rank_id
        old_kit_level = player.kit_level

        changes = []
        if old_rank_id != new_rank_id:
            changes.append({"field": "rank_id", "from": old_rank_id, "to": new_rank_id})
        if old_kit_level != new_kit_level:
            changes.append({"field": "kit_level", "from": old_kit_level, "to": new_kit_level})

        if not changes:
            push_flash(request, "info", "Изменений не обнаружено")
            return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)

        player.rank_id = new_rank_id
        player.kit_level = new_kit_level

        db.commit()
        db.refresh(player)

        # If rank changed and player has confirmed discord_id, queue role update for bot
        if old_rank_id != new_rank_id and player.discord_id and player.discord_id_confirmed:
            from models import DiscordBotAction
            rank_roles = settings.rank_discord_roles or {}
            old_discord_role = rank_roles.get(str(old_rank_id), "") if old_rank_id is not None else ""
            new_discord_role = rank_roles.get(str(new_rank_id), "") if new_rank_id is not None else ""

            logger.info(f"[RankChange] discord_id={player.discord_id}, old_rank={old_rank_id}->role={old_discord_role}, new_rank={new_rank_id}->role={new_discord_role}")

            bot_action = DiscordBotAction(
                action_type="update_rank_role",
                payload={
                    "discord_id": player.discord_id,
                    "remove_role_id": old_discord_role or None,
                    "add_role_id": new_discord_role or None,
                    "player_name": player.player_last_time_name,
                    "old_rank": settings.ranks_mapping.get(str(old_rank_id), ""),
                    "new_rank": settings.ranks_mapping.get(str(new_rank_id), ""),
                },
                queued_by_admin_id=current_admin.id,
            )
            db.add(bot_action)
            db.commit()
            logger.info(f"[RankChange] Bot action created: {bot_action.id}")
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

    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


@router.post("/admin/players/{player_id}/discord-id")
async def update_player_discord_id(
    request: Request,
    player_id: int,
    discord_id: str = Form(""),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Update player's Discord ID (confirmed by admin)."""
    if not (check_permission(current_admin, "players", "edit") or check_permission(current_admin, "players", "delete") or check_permission(current_admin, "players_special", "view")):
        push_flash(request, "error", "Недостаточно прав")
        return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)

    try:
        await ensure_csrf_or_valid_admin_session(request, db)
        player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
        if not player:
            push_flash(request, "error", "Игрок не найден")
            return RedirectResponse(url="/admin/players", status_code=302)

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

    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions_list(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """List active admin sessions"""
    if not check_permission(current_admin, "sessions", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Initialize CSRF token if not exists
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    sessions = db.query(AdminSession).order_by(AdminSession.created_at.desc()).all()

    return templates.TemplateResponse("admin/sessions.html", {"request": request, "admin": current_admin, "sessions": sessions, "flashes": pop_flashes(request)})


@router.post("/admin/sessions/{session_id}/revoke")
async def revoke_session(request: Request, session_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Revoke a specific admin session"""
    if not check_permission(current_admin, "sessions", "edit"):
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


# ═══════════════════════════════════════════════════════════════════════════
# PRIVILEGE GROUPS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/admin/privilege-groups", response_class=HTMLResponse)
async def admin_privilege_groups(request: Request):
    """Redirect to combined users+groups page."""
    return RedirectResponse(url="/admin/users?tab=groups", status_code=302)


@router.post("/admin/privilege-groups/create")
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
        return RedirectResponse(url="/admin/users?tab=groups", status_code=302)

    if db.query(AdminPrivilegeGroup).filter(AdminPrivilegeGroup.name == name).first():
        push_flash(request, "error", f"Группа '{name}' уже существует")
        return RedirectResponse(url="/admin/users?tab=groups", status_code=302)

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
    return RedirectResponse(url="/admin/users?tab=groups", status_code=302)


@router.post("/admin/privilege-groups/{group_id}/edit")
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
    return RedirectResponse(url="/admin/users?tab=groups", status_code=302)


@router.post("/admin/privilege-groups/{group_id}/delete")
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
    return RedirectResponse(url="/admin/users?tab=groups", status_code=302)


@router.post("/admin/users/{user_id}/assign-group")
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
        return RedirectResponse(url="/admin/users?tab=users", status_code=302)

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
    return RedirectResponse(url="/admin/users?tab=users", status_code=302)


@router.get("/admin/debug", response_class=HTMLResponse)
async def admin_debug(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Debug page - check permission system"""
    return templates.TemplateResponse("admin/debug.html", {
        "request": request,
        "admin": current_admin,
    })


@router.get("/admin/meeting", response_class=HTMLResponse)
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


@router.get("/admin/audit", response_class=HTMLResponse)
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


@router.get("/api/v1/admin/audit/logs")
async def api_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """JSON API for audit logs with pagination."""
    if not check_permission(current_admin, "audit", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse

    offset = (page - 1) * page_size
    total = db.query(AdminActionLog).count()
    logs = (
        db.query(AdminActionLog)
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


@router.get("/admin/server-privileges", response_class=HTMLResponse)
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


@router.post("/admin/server-privileges/add")
async def add_server_privilege(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin)
):
    """Add legacy player privilege (backward compat — use /assign for new assignments)."""
    if not check_permission(current_admin, "privileges", "edit"):
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

        log_admin_action(db, current_admin.id, "grant_player_privilege",
                         {"steamid": steamid, "privileges": selected_privileges, "reason": reason})
        push_flash(request, "success", f"Привилегии выданы игроку {steamid}")
    except HTTPException as exc:
        push_flash(request, "error", f"Ошибка: {exc.detail}")
    except Exception as exc:
        db.rollback()
        logger.exception("Add privilege failed: %s", exc)
        push_flash(request, "error", "Не удалось добавить привилегию")

    return RedirectResponse(url="/admin/server-privileges?tab=players", status_code=302)


@router.post("/admin/server-privileges/{privilege_id}/update")
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
    if not check_permission(current_admin, "privileges", "edit"):
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


# ─── Server Privilege Groups CRUD ────────────────────────────────────────────

def _check_server_priv_perm(admin: AdminUser):
    if not check_permission(admin, "privileges", "edit"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


@router.post("/admin/server-privilege-groups/create")
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
        return RedirectResponse(url="/admin/server-privileges?tab=groups", status_code=302)

    if db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.group_key == group_key).first():
        push_flash(request, "error", f"Группа с ID «{group_key}» уже существует")
        return RedirectResponse(url="/admin/server-privileges?tab=groups", status_code=302)

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
    return RedirectResponse(url="/admin/server-privileges?tab=groups", status_code=302)


@router.post("/admin/server-privilege-groups/{group_id}/edit")
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
            return RedirectResponse(url="/admin/server-privileges?tab=groups", status_code=302)
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
    return RedirectResponse(url="/admin/server-privileges?tab=groups", status_code=302)


@router.post("/admin/server-privilege-groups/{group_id}/delete")
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
    return RedirectResponse(url="/admin/server-privileges?tab=groups", status_code=302)


@router.post("/admin/server-privileges/assign")
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
        return RedirectResponse(url="/admin/server-privileges?tab=players", status_code=302)

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
                return RedirectResponse(url="/admin/server-privileges?tab=players", status_code=302)

    commands_raw = (form.get("commands") or "").strip()
    commands = [c.strip() for c in commands_raw.splitlines() if c.strip()]
    action_ids_raw = (form.get("allowed_action_ids") or "").strip()
    allowed_action_ids = [x.strip() for x in action_ids_raw.replace(",", " ").split() if x.strip()]
    reason = (form.get("reason") or "").strip() or None
    server_scope_raw = (form.get("server_scope") or "").strip().upper() or None
    server_scope = server_scope_raw if server_scope_raw in ("ASPER1", "ASPER2") else None

    record = db.query(ServerPlayerPrivilege).filter(
        ServerPlayerPrivilege.steamid == steamid
    ).first()

    if record:
        record.server_group_id = server_group_id
        record.commands = commands
        record.allowed_action_ids = allowed_action_ids
        record.reason = reason
        record.server_scope = server_scope
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
        )
        db.add(record)

    db.commit()
    log_admin_action(db, current_admin.id, "assign_server_privilege",
                     {"steamid": steamid, "group_id": server_group_id, "commands": commands, "server_scope": server_scope})
    push_flash(request, "success", f"Привилегии обновлены для {steamid}")
    return RedirectResponse(url="/admin/server-privileges?tab=players", status_code=302)


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
    if not check_permission(current_admin, "players", "edit"):
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
    if not check_permission(current_admin, "players", "edit"):
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


@router.post("/api/v1/admin/players/{player_id}/rewards/kval-kits")
async def set_kval_kit(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Set (add or update) a kval-kit for a profession in player achievements."""
    if not check_permission(current_admin, "players", "edit"):
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
    if not check_permission(current_admin, "players", "edit"):
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
    """Set (add or update) a custom item for a player."""
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    body = await request.json()
    item_id = body.get("item_id", "").strip()
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
    items = dict(ach.get("items", {}))
    items[item_id] = quantity
    ach["items"] = items
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "set_reward_item", {"player_id": player_id, "item_id": item_id, "quantity": quantity})
    return JSONResponse({"status": "ok", "items": ach["items"]})


@router.delete("/api/v1/admin/players/{player_id}/rewards/items")
async def remove_reward_item(
    request: Request,
    player_id: int,
    item_id: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Remove a custom item from player achievements."""
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf_or_valid_admin_session(request, db)
    player = _get_rewards_player(player_id, db)
    ach = _get_achievements_dict(player)
    items = dict(ach.get("items", {}))
    if item_id not in items:
        raise HTTPException(status_code=404, detail="Item not found")
    del items[item_id]
    ach["items"] = items
    player.achievements = ach
    db.commit()
    log_admin_action(db, current_admin.id, "remove_reward_item", {"player_id": player_id, "item_id": item_id})
    return JSONResponse({"status": "ok", "items": ach["items"]})


@router.post("/api/v1/admin/players/{player_id}/rewards/stats")
async def set_reward_stat(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Set a stat bonus. For 'all': {job_id:'all', value:5}. Per-profession: {job_id:'captain', stat_key:'mechanic', value:25}."""
    if not check_permission(current_admin, "players", "edit"):
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
    if not check_permission(current_admin, "players", "edit"):
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
    if not check_permission(current_admin, "players", "edit"):
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
    if not check_permission(current_admin, "players", "edit"):
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


@router.get("/admin/kits", response_class=HTMLResponse)
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


# ── Qualification kit routes ──────────────────────────────────────────────────

@router.post("/admin/qual-kits/add")
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
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

        job_id = base_job if base_job.endswith("_k") else base_job + "_k"

        try:
            kit_level = int(kit_level_raw)
            if kit_level not in range(1, 6):
                raise ValueError
        except (TypeError, ValueError):
            push_flash(request, "error", "Уровень квалификации должен быть от 1 до 5")
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

        try:
            items = json.loads(items_raw)
            if not isinstance(items, dict):
                raise ValueError("items must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            push_flash(request, "error", f"Некорректный JSON: {e}")
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

        existing = db.query(Kit).filter(Kit.job_id == job_id, Kit.kit_level == kit_level).first()
        if existing:
            push_flash(request, "error", f"Квал-кит '{job_id}' уровня {kit_level} уже существует")
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

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

    return RedirectResponse(url="/admin/kits#qual", status_code=302)


@router.post("/admin/qual-kits/{kit_id}/update")
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
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

        kit_level_raw = form.get("kit_level", str(kit.kit_level))
        items_raw = form.get("items", "{}").strip()

        try:
            kit_level = int(kit_level_raw)
            if kit_level not in range(1, 6):
                raise ValueError
        except (TypeError, ValueError):
            push_flash(request, "error", "Уровень квалификации должен быть от 1 до 5")
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

        try:
            items = json.loads(items_raw)
            if not isinstance(items, dict):
                raise ValueError("items must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            push_flash(request, "error", f"Некорректный JSON: {e}")
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

        conflict = db.query(Kit).filter(
            Kit.job_id == kit.job_id, Kit.kit_level == kit_level, Kit.id != kit_id
        ).first()
        if conflict:
            push_flash(request, "error", f"Квал-кит '{kit.job_id}' уровня {kit_level} уже существует")
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

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

    return RedirectResponse(url="/admin/kits#qual", status_code=302)


@router.post("/admin/qual-kits/{kit_id}/delete")
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
            return RedirectResponse(url="/admin/kits#qual", status_code=302)

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

    return RedirectResponse(url="/admin/kits#qual", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# PLAYER NOTES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/players/{player_id}/notes/save")
async def save_player_note(
    request: Request,
    player_id: int,
    content: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerNoteService(db).save_note(player_id, current_admin.id, content)
    log_admin_action(db, current_admin.id, "save_player_note", {"player_id": player_id})
    push_flash(request, "success", "Личное дело сохранено")
    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# LABELS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/players/{player_id}/labels/assign")
async def assign_label(
    request: Request,
    player_id: int,
    category_id: int = Form(...),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerLabelService(db).assign(player_id, category_id, note, current_admin.id)
    log_admin_action(db, current_admin.id, "assign_label", {"player_id": player_id, "category_id": category_id})
    push_flash(request, "success", "Метка установлена")
    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


@router.post("/admin/players/{player_id}/labels/{label_id}/remove")
async def remove_label(
    request: Request,
    player_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerLabelService(db).remove(label_id)
    log_admin_action(db, current_admin.id, "remove_label", {"player_id": player_id, "label_id": label_id})
    push_flash(request, "success", "Метка снята")
    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


@router.get("/admin/label-categories")
async def label_categories_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    cats = LabelCategoryService(db).get_all()
    return templates.TemplateResponse("admin/label_categories.html", {
        "request": request, "admin": current_admin,
        "categories": cats, "flashes": pop_flashes(request),
    })


@router.post("/admin/label-categories/create")
async def create_label_category(
    request: Request,
    name: str = Form(...),
    color: str = Form(default="#888888"),
    icon: str = Form(default="🏷"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    LabelCategoryService(db).create(name.strip(), color, icon, current_admin.id)
    push_flash(request, "success", f"Категория «{name}» создана")
    return RedirectResponse(url="/admin/label-categories", status_code=302)


@router.post("/admin/label-categories/{cat_id}/delete")
async def delete_label_category(
    request: Request,
    cat_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    ok = LabelCategoryService(db).delete(cat_id)
    push_flash(request, "success" if ok else "error",
               "Категория удалена" if ok else "Категория не найдена")
    return RedirectResponse(url="/admin/label-categories", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# TRAPS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/players/{player_id}/traps/set")
async def set_trap(
    request: Request,
    player_id: int,
    reason: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise HTTPException(status_code=404)
    PlayerTrapService(db).set_trap(player_id, player.player_steamid, reason, current_admin.id)
    log_admin_action(db, current_admin.id, "set_trap", {"player_id": player_id})
    push_flash(request, "success", "Ловушка установлена")
    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


@router.post("/admin/players/{player_id}/traps/{trap_id}/deactivate")
async def deactivate_trap(
    request: Request,
    player_id: int,
    trap_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "players", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)
    PlayerTrapService(db).deactivate(trap_id)
    push_flash(request, "success", "Ловушка деактивирована")
    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ADMINISTRATION PAGE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/administration", response_class=HTMLResponse)
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
    editable_cfg = _get_editable_config()

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


def _get_editable_config() -> dict:
    """Return dict of editable config fields and their current values."""
    import os as _os, json as _json
    # Patch broken list fields in .env before loading Settings
    env_path = ".env"
    list_keys = {"CORS_ORIGINS", "ALLOWED_IPS"}
    if _os.path.exists(env_path):
        lines = []
        changed = False
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
                        lines.append(f"{k}={_json.dumps(items)}\n")
                        changed = True
                        continue
                lines.append(line if line.endswith("\n") else line + "\n")
        if changed:
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            from config import reset_settings_cache
            reset_settings_cache()

    s = get_settings()
    return {
        "debug": s.debug,
        "discord_trap_webhook_url": s.discord_trap_webhook_url,
        "discord_client_id": s.discord_client_id,
        "discord_client_secret": s.discord_client_secret,
        "discord_redirect_uri": s.discord_redirect_uri,
        "use_discord_proxy": s.use_discord_proxy,
        "discord_proxy_url": s.discord_proxy_url,
        "allowed_ips": ",".join(s.allowed_ips_list) if s.allowed_ips_list else s.allowed_ips,
        "rate_limit_enabled": s.rate_limit_enabled,
        "rate_limit_requests": s.rate_limit_requests,
        "rate_limit_window": s.rate_limit_window,
        "cors_origins": ",".join(s.cors_origins_list) if s.cors_origins_list else s.cors_origins,
        "sub_projects_webhook_url": s.sub_projects_webhook_url,
        "sftp_server_1_name": s.sftp_server_1_name,
        "sftp_server_1_host": s.sftp_server_1_host,
        "sftp_server_1_port": s.sftp_server_1_port,
        "sftp_server_1_username": s.sftp_server_1_username,
        "sftp_server_1_password": s.sftp_server_1_password,
        "sftp_server_1_key_path": s.sftp_server_1_key_path,
        "sftp_server_1_remote_dir": s.sftp_server_1_remote_dir,
        "sftp_server_2_name": s.sftp_server_2_name,
        "sftp_server_2_host": s.sftp_server_2_host,
        "sftp_server_2_port": s.sftp_server_2_port,
        "sftp_server_2_username": s.sftp_server_2_username,
        "sftp_server_2_password": s.sftp_server_2_password,
        "sftp_server_2_key_path": s.sftp_server_2_key_path,
        "sftp_server_2_remote_dir": s.sftp_server_2_remote_dir,
        "sftp_server_1_logs_dir": s.sftp_server_1_logs_dir,
        "sftp_server_2_logs_dir": s.sftp_server_2_logs_dir,
        "sub_projects_discord_channel_id": s.sub_projects_discord_channel_id,
    }


@router.post("/admin/administration/config")
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

    # Build new .env content
    env_path = ".env"
    import os

    # Read existing .env lines (preserve unknown keys)
    existing: dict = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()

    # Map form fields → env keys
    field_map = {
        "debug": "DEBUG",
        "discord_trap_webhook_url": "DISCORD_TRAP_WEBHOOK_URL",
        "discord_client_id": "DISCORD_CLIENT_ID",
        "discord_client_secret": "DISCORD_CLIENT_SECRET",
        "discord_redirect_uri": "DISCORD_REDIRECT_URI",
        "use_discord_proxy": "USE_DISCORD_PROXY",
        "discord_proxy_url": "DISCORD_PROXY_URL",
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
    }

    for field, env_key in field_map.items():
        val = form.get(field)
        if val is None:
            # checkboxes that are unchecked won't appear in form
            if field in ("debug", "use_discord_proxy", "rate_limit_enabled"):
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
            existing[env_key] = _json.dumps(items)
        else:
            existing[env_key] = val or ""

    # Write back
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
        # Invalidate settings cache
        from config import reset_settings_cache
        reset_settings_cache()
        push_flash(request, "success", "Конфигурация сохранена. Перезапустите сервер для применения части настроек.")
    except Exception as e:
        push_flash(request, "error", f"Ошибка сохранения: {e}")

    log_admin_action(db, current_admin.id, "save_config", {})
    return RedirectResponse(url="/admin/administration", status_code=302)


# ════════════════════════════════════════════════════════════════════════════
# PUNISHMENTS
# ════════════════════════════════════════════════════════════════════════════

@router.get("/admin/api/players/search")
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


@router.get("/admin/punishments")
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


@router.post("/admin/punishments/create")
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
    if not check_permission(current_admin, "punishments", "edit"):
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
    return RedirectResponse(url=f"/admin/players/{player_id}", status_code=302)


@router.post("/admin/punishments/{punishment_id}/edit")
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
    return RedirectResponse(url=f"/admin/punishments?tab={tab}", status_code=302)


@router.post("/admin/punishments/{punishment_id}/void")
async def admin_punishment_void(
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
    tab = p.type
    svc.set_void(punishment_id)
    log_admin_action(db, current_admin.id, "punishment_void", {"punishment_id": punishment_id})
    push_flash(request, "info", "Наказание помечено неактуальным.")
    return RedirectResponse(url=f"/admin/punishments?tab={tab}", status_code=302)


@router.post("/admin/punishments/{punishment_id}/unban")
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
    if not check_permission(current_admin, "punishments", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    if p.type != "ban":
        push_flash(request, "error", "Разбан возможен только для банов")
        return RedirectResponse(url=f"/admin/punishments?tab={p.type}", status_code=302)

    from utils import steam64_to_steam2
    steamid = p.player_steamid or ""
    steam2 = steam64_to_steam2(steamid) if steamid else None

    svc.set_void(punishment_id)

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
    })
    push_flash(request, "success", f"Разбан поставлен в очередь (action_id={action.id}){srv_label}")
    return RedirectResponse(url=f"/admin/punishments?tab=ban", status_code=302)


@router.post("/admin/punishments/{punishment_id}/delete")
async def admin_punishment_delete(
    request: Request,
    punishment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "punishments", "delete"):
        raise HTTPException(status_code=403)
    await ensure_csrf_or_valid_admin_session(request, db)

    svc = PunishmentService(db)
    p = svc.get_by_id(punishment_id)
    if not p:
        raise HTTPException(status_code=404)
    tab = p.type
    svc.delete(punishment_id)
    log_admin_action(db, current_admin.id, "punishment_delete", {"punishment_id": punishment_id})
    push_flash(request, "success", "Наказание удалено.")
    return RedirectResponse(url=f"/admin/punishments?tab={tab}", status_code=302)


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
    
    # Test all permission checks with detailed tracing
    pages = ["players", "players_special", "moderation", "dispatch", "sessions", "kits", "admin", "stats", "audit", "punishments", "privileges", "sub_projects", "replays", "ratings", "discord_bot", "tickets", "meeting"]
    actions = ["view", "edit", "delete"]
    
    permission_tests = {}
    permission_traces = {}
    for page in pages:
        permission_tests[page] = {}
        permission_traces[page] = {}
        for action in actions:
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