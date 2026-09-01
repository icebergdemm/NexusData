"""
Admin panel endpoints for the Discord bot's config/stats page.

The bot-facing HTTP API this module used to expose (`/api/v1/bot/*` —
members/sync, actions queue+ack, config, department-chat, round-status,
ratings, twink-suspects) is gone: the bot now runs in-process (see
discord_bot/bot.py, discord_bot/data.py) and calls those data-access
functions directly instead of looping back over HTTP with its own API key.
Only the admin-session-gated panel_router below remains.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import secrets
import json as _json

from database import get_db
from security import check_permission
from models import DiscordMember, DiscordBotConfig, DiscordAntiRaidLog, PlayerData, AdminUser, AdminSession

panel_router = APIRouter()

_templates = Jinja2Templates(directory="templates")
_templates.env.globals.update(check_permission=check_permission)


def _get_admin(request: Request, db: Session) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = db.query(AdminUser).options(joinedload(AdminUser.privilege_group)).filter(AdminUser.id == session.admin_id).first()
            if admin:
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _twink_suspects(db: Session) -> list:
    from datetime import timedelta

    recent_threshold = datetime.utcnow() - timedelta(days=7)
    recent_members = db.query(DiscordMember).filter(DiscordMember.joined_at >= recent_threshold).all()
    confirmed_ids = set(
        p.discord_id for p in db.query(PlayerData).filter(
            PlayerData.discord_id.isnot(None), PlayerData.discord_id_confirmed == 1
        ).all()
    )
    suspects = []
    for member in recent_members:
        if member.discord_id in confirmed_ids:
            continue
        if member.display_name and len(member.display_name) >= 4:
            similar = db.query(PlayerData).filter(
                PlayerData.player_last_time_name.ilike(f"%{member.display_name[:6]}%")
            ).first()
            if similar and similar.discord_id and similar.discord_id != member.discord_id:
                suspects.append({
                    "new_discord_id": member.discord_id,
                    "new_username": member.username,
                    "new_display_name": member.display_name,
                    "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                    "similar_player_name": similar.player_last_time_name,
                })
    return suspects


@panel_router.get("/adminold/discord-bot", response_class=HTMLResponse)
async def admin_discord_bot_page(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    from discord_bot import bot as discord_bot

    members_count = db.query(DiscordMember).count()
    bot_online = discord_bot.bot.is_ready()

    last_member = db.query(DiscordMember).order_by(DiscordMember.last_synced_at.desc()).first()
    last_sync = last_member.last_synced_at.strftime("%d.%m.%Y %H:%M") if last_member and last_member.last_synced_at else None

    configs = db.query(DiscordBotConfig).all()
    config = {c.key: c.value for c in configs}

    antiraid_events = db.query(DiscordAntiRaidLog).order_by(DiscordAntiRaidLog.occurred_at.desc()).limit(30).all()
    ar_list = [
        {
            "event_type": e.event_type,
            "discord_id": e.discord_id,
            "username": e.username,
            "details": e.details,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "resolved": e.resolved,
        }
        for e in antiraid_events
    ]

    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)

    return _templates.TemplateResponse("admin/discord_bot.html", {
        "request": request,
        "admin": admin,
        "members_count": members_count,
        "bot_online": bot_online,
        "last_sync": last_sync,
        "config": config,
        "antiraid_events": ar_list,
        "twink_suspects": _twink_suspects(db),
        "flashes": flashes,
    })


@panel_router.get("/api/v1/admin/discord-bot")
async def api_admin_discord_bot(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)

    from discord_bot import bot as discord_bot

    members_count = db.query(DiscordMember).count()
    last_member = db.query(DiscordMember).order_by(DiscordMember.last_synced_at.desc()).first()
    last_sync = last_member.last_synced_at.isoformat() if last_member and last_member.last_synced_at else None

    configs = db.query(DiscordBotConfig).all()
    config = {c.key: c.value for c in configs}

    # Recent failures of the site -> Discord role push (routes/departments.py's
    # _queue_role_sync) — now read directly from the bot's own in-memory log
    # (see discord_bot/bot.py's get_recent_role_sync_failures) instead of a
    # DiscordBotAction row, since the bot dispatches these directly and there
    # is no longer a persisted queue to inspect.
    failed_role_syncs = discord_bot.get_recent_role_sync_failures()

    antiraid_events = db.query(DiscordAntiRaidLog).order_by(DiscordAntiRaidLog.occurred_at.desc()).limit(30).all()
    ar_list = [
        {
            "event_type": e.event_type, "discord_id": e.discord_id, "username": e.username,
            "details": e.details, "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "resolved": bool(e.resolved),
        }
        for e in antiraid_events
    ]

    return {
        "members_count": members_count, "bot_online": discord_bot.bot.is_ready(), "last_sync": last_sync,
        "config": config, "antiraid_events": ar_list, "twink_suspects": _twink_suspects(db),
        "failed_role_syncs": failed_role_syncs,
        "can_edit": check_permission(admin, "discord_bot", "edit"),
        "can_manage_config": check_permission(admin, "discord_bot", "manage_config"),
    }


@panel_router.post("/api/v1/admin/discord-bot/config")
async def api_admin_discord_bot_set_config(request: Request, db: Session = Depends(get_db)):
    """Set a DiscordBotConfig key/value from the admin-session-authenticated web panel
    (previously only settable via Discord bang-commands using the bot's own API key)."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    body = await request.json()
    key = (body.get("key") or "").strip()
    value = body.get("value")
    if not key:
        raise HTTPException(status_code=400, detail="key required")

    record = db.query(DiscordBotConfig).filter(DiscordBotConfig.key == key).first()
    if record:
        record.value = value
    else:
        record = DiscordBotConfig(key=key, value=value)
        db.add(record)
    db.commit()
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_bot_config_set", {"key": key})
    return {"status": "ok", "key": key, "value": value}


# ══════════════════════════════════════════════════════════════════════════════
# TICKET SETTINGS — раньше правились только Discord-командами бота
# (!setuptickets, !setticketcategory, !setticketstatus, ручное редактирование
# config/tickets.json на сервере). Здесь — единая точка правки со страницы
# «Discord бот»: типы обращений (название/эмодзи/цвет/роли уведомления/
# шаблон), категория, канал статуса, роли персонала для /close и /ticket_add.
# ══════════════════════════════════════════════════════════════════════════════

_TICKET_TYPE_FIELDS = {"name", "emoji", "color", "ping_roles", "template", "allow_cooldown"}


def _validate_tickets_config(config: dict) -> Optional[str]:
    """Текст ошибки, если конфигурация не годится для сохранения, иначе None.

    Проверяется форма, а не факт существования ролей/каналов в Discord —
    бот всё равно живёт в отдельном процессе внутри того же приложения, и
    дёргать его API ради проверки одного ID усложнило бы сохранение без
    реальной пользы: несуществующий ID просто не будет ничего делать в
    Discord, как и раньше при правке файла руками."""
    if not isinstance(config, dict):
        return "Ожидается объект конфигурации"

    types = config.get("types")
    if not isinstance(types, dict):
        return "types: ожидается объект {ключ: {...}}"
    for key, tconf in types.items():
        if not isinstance(key, str) or not key:
            return "types: ключ типа обращения не может быть пустым"
        if not isinstance(tconf, dict):
            return f"types.{key}: ожидается объект"
        extra = set(tconf.keys()) - _TICKET_TYPE_FIELDS
        if extra:
            return f"types.{key}: неизвестные поля {sorted(extra)}"
        if not (tconf.get("name") or "").strip():
            return f"types.{key}: название не может быть пустым"
        ping_roles = tconf.get("ping_roles") or []
        if not isinstance(ping_roles, list) or not all(isinstance(r, str) for r in ping_roles):
            return f"types.{key}.ping_roles: ожидается список строк (Discord ID ролей)"

    for field in ("category_id", "status_channel_id"):
        if field in config and config[field] is not None and not isinstance(config[field], str):
            return f"{field}: ожидается строка"

    manager_role_ids = config.get("manager_role_ids", [])
    if not isinstance(manager_role_ids, list) or not all(isinstance(r, str) for r in manager_role_ids):
        return "manager_role_ids: ожидается список строк (Discord ID ролей)"

    return None


@panel_router.get("/api/v1/admin/discord-bot/tickets-config")
async def api_admin_tickets_config(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)

    from discord_bot import data as bot_data

    config = bot_data.get_tickets_config()
    config.setdefault("category_id", "")
    config.setdefault("status_channel_id", "")
    config.setdefault("manager_role_ids", [])
    return {
        "config": config,
        "can_manage_config": check_permission(admin, "discord_bot", "manage_config"),
    }


@panel_router.put("/api/v1/admin/discord-bot/tickets-config")
async def api_admin_tickets_config_save(request: Request, db: Session = Depends(get_db)):
    """Перезаписывает config/tickets.json целиком — тот же файл, который
    читает бот (discord_bot/data.py: get_tickets_config), при следующем
    создании тикета/проверке прав он подхватит новые значения без
    перезапуска: файл читается заново на каждый вызов, не кэшируется."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    body = await request.json()
    config = body.get("config")
    error = _validate_tickets_config(config)
    if error:
        raise HTTPException(status_code=400, detail=error)

    from discord_bot import data as bot_data
    bot_data.set_tickets_config(config)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_bot_tickets_config_set", {"types": list((config.get("types") or {}).keys())})
    return {"status": "ok", "config": config}


@panel_router.get("/api/v1/admin/discord-bot/role-sync/preview")
async def api_discord_role_sync_preview(request: Request, db: Session = Depends(get_db)):
    """Read-only diff of "site says this member should hold these department
    roles" vs DiscordMember.role_ids (last-known snapshot) — the site is the
    source of truth, this just finds where Discord hasn't caught up yet, in
    EITHER direction (add missing, remove stale). Nothing is dispatched here;
    the admin picks which rows to actually sync via the /apply endpoint
    below. Compare with workers.task_reconcile_department_discord_roles,
    which only ever adds automatically — this manual tool is reviewed by a
    human, so it's safe to also surface (and let them choose to apply)
    removals."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    from sqlalchemy.orm import joinedload
    from models import DepartmentMember, Department, DiscordMember

    memberships = (
        db.query(DepartmentMember)
        .join(Department, Department.id == DepartmentMember.department_id)
        .filter(Department.is_archived.is_(False))
        .options(joinedload(DepartmentMember.admin), joinedload(DepartmentMember.department).joinedload(Department.access_levels))
        .all()
    )

    rows = []
    for m in memberships:
        member_admin = m.admin
        if not member_admin or not member_admin.discord_id:
            continue
        dept = m.department

        managed_roles = set()
        if dept.base_discord_role_id:
            managed_roles.add(dept.base_discord_role_id)
        for level in dept.access_levels:
            if level.discord_role_id:
                managed_roles.add(level.discord_role_id)
        if not managed_roles:
            continue

        expected_roles = set()
        if dept.base_discord_role_id:
            expected_roles.add(dept.base_discord_role_id)
        held = m.held_level_keys or []
        for level in dept.access_levels:
            if level.key in held and level.discord_role_id:
                expected_roles.add(level.discord_role_id)

        snapshot = db.query(DiscordMember).filter(DiscordMember.discord_id == member_admin.discord_id).first()
        current_roles = set(snapshot.role_ids or []) if snapshot else set()

        add_role_ids = sorted(expected_roles - current_roles)
        # Only ever proposes removing a role THIS department's module
        # actually manages (base role or one of its access levels') — never
        # a role from another department or one granted manually in Discord
        # for unrelated reasons.
        remove_role_ids = sorted((current_roles & managed_roles) - expected_roles)
        if not add_role_ids and not remove_role_ids:
            continue

        rows.append({
            "admin_id": member_admin.id,
            "admin_name": member_admin.discord_username,
            "department_id": dept.id,
            "department_name": dept.name,
            "add_role_ids": add_role_ids,
            "remove_role_ids": remove_role_ids,
        })

    return {"rows": rows}


@panel_router.post("/api/v1/admin/discord-bot/role-sync/apply")
async def api_discord_role_sync_apply(request: Request, db: Session = Depends(get_db)):
    """Dispatch sync_department_role bot actions for exactly the rows the
    admin selected from the preview above — never re-derives the diff
    itself, so what gets applied is exactly what was reviewed on screen."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    from models import AdminUser as _AdminUser
    from discord_bot import bot as discord_bot

    body = await request.json()
    selections = body.get("selections") or []
    queued = 0
    for sel in selections:
        member_admin = db.query(_AdminUser).filter(_AdminUser.id == sel.get("admin_id")).first()
        if not member_admin or not member_admin.discord_id:
            continue
        for role_id in sel.get("add_role_ids") or []:
            discord_bot.schedule(discord_bot.execute_bot_action(
                "sync_department_role",
                {"discord_id": member_admin.discord_id, "add_role_id": role_id},
            ))
            queued += 1
        for role_id in sel.get("remove_role_ids") or []:
            discord_bot.schedule(discord_bot.execute_bot_action(
                "sync_department_role",
                {"discord_id": member_admin.discord_id, "remove_role_id": role_id},
            ))
            queued += 1

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_role_sync_manual_apply", {"queued": queued, "selections": len(selections)})
    return {"status": "ok", "queued": queued}


@panel_router.post("/api/v1/admin/discord-bot/action")
async def api_admin_discord_bot_send_action(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "broadcast"):
        raise HTTPException(status_code=403)

    body = await request.json()
    action_type = body.get("action_type")
    payload = body.get("payload") or {}
    if not action_type:
        raise HTTPException(status_code=400, detail="action_type required")

    from discord_bot import bot as discord_bot
    discord_bot.schedule(discord_bot.execute_bot_action(action_type, payload))
    return {"status": "ok"}


@panel_router.post("/adminold/discord-bot/action")
async def admin_discord_bot_send_action(
    request: Request,
    action_type: str = Form(...),
    payload_json: str = Form("{}"),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "broadcast"):
        raise HTTPException(status_code=403)

    try:
        payload = _json.loads(payload_json)
    except _json.JSONDecodeError:
        payload = {}

    from discord_bot import bot as discord_bot
    discord_bot.schedule(discord_bot.execute_bot_action(action_type, payload))

    flashes = request.session.get("_flashes", [])
    flashes.append({"level": "success", "message": f"Действие «{action_type}» отправлено боту"})
    request.session["_flashes"] = flashes

    return RedirectResponse(url="/adminold/discord-bot", status_code=302)
