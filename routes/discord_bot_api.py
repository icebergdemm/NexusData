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
import logging

from database import get_db
from security import check_permission
from models import DiscordMember, DiscordBotConfig, DiscordAntiRaidLog, PlayerData, AdminUser, AdminSession

panel_router = APIRouter()

# This endpoint is intentionally much narrower than execute_bot_action().
# Most action types are internal service-to-bot contracts with their own
# permission checks (tickets, documents, roles, etc.).  Exposing them through
# the broadcast console would let a broadcaster bypass those checks.
_BROADCAST_ACTION_TYPES = frozenset({"send_message", "send_embed"})

_templates = Jinja2Templates(directory="templates")
_templates.env.globals.update(check_permission=check_permission)


def _get_admin(request: Request, db: Session) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


def _discord_id_strings(values) -> list[str]:
    """Keep Discord snowflakes as strings across the JSON boundary."""
    return sorted(str(value) for value in values)


async def _validate_picker_whitelist(value: object, source: str) -> str:
    """Reject stale IDs from the live channel picker.

    The advanced field is intentionally the escape hatch for archived/hidden
    objects. Picker saves, including requests from an older already-open
    frontend tab, must only contain channels the bot can currently see.
    """
    if source == "advanced":
        return value if isinstance(value, str) else _json.dumps(value, ensure_ascii=False)

    if isinstance(value, list):
        raw_ids = value
    else:
        try:
            raw_ids = _json.loads(str(value or ""))
        except (_json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Whitelist picker: ожидается JSON-массив Discord ID") from exc
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="Whitelist picker: ожидается JSON-массив Discord ID")

    from routes.discord_viewer_api import _call_bot
    from discord_bot import bot as discord_bot

    tree = await _call_bot(discord_bot.viewer_get_tree())
    available = {
        str(channel["id"])
        for channel in tree.get("channels", [])
        if channel.get("type") in {"text", "thread"}
    }
    selected = []
    invalid = []
    for item in raw_ids:
        channel_id = str(item).strip()
        if not channel_id or channel_id in selected:
            continue
        if channel_id not in available:
            invalid.append(channel_id)
        else:
            selected.append(channel_id)
    if invalid:
        shown = ", ".join(invalid[:5])
        suffix = f" и ещё {len(invalid) - 5}" if len(invalid) > 5 else ""
        raise HTTPException(
            status_code=409,
            detail=f"Picker получил недоступные Discord ID: {shown}{suffix}. Обновите список каналов и выберите их заново; скрытые объекты сохраняются через advanced-режим.",
        )
    return _json.dumps(selected, ensure_ascii=False, separators=(",", ":"))


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

    from config import get_settings
    from discord_bot.address_detector.config import KNOWN_KEYS, load_detector_config
    from discord_bot.bot import get_address_detector_status
    try:
        effective_detector = load_detector_config(get_settings(), config)
        detector_effective = {
            **effective_detector.public_status(),
            "monitored_channel_ids": _discord_id_strings(effective_detector.monitored_channel_ids),
            "excluded_channel_ids": _discord_id_strings(effective_detector.excluded_channel_ids),
            "signals_channel_id": effective_detector.signals_channel_id,
            "moderator_role_id": effective_detector.moderator_role_id,
        }
        detector_config_error = None
    except ValueError as exc:
        detector_effective = None
        detector_config_error = str(exc)

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
        "address_detector": {
            "known_keys": list(KNOWN_KEYS),
            "effective": detector_effective,
            "config_error": detector_config_error,
            "runtime": get_address_detector_status(),
        },
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
    source = str(body.get("source") or "").strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="key required")

    from config import get_settings
    from discord_bot.address_detector.config import KNOWN_KEYS, load_detector_config
    # Treat a whitelist save without an explicit advanced marker as a picker
    # save. This also protects against a stale browser tab using the old UI.
    if key == "monitored_channel_ids":
        value = await _validate_picker_whitelist(value, source)
    if key in KNOWN_KEYS:
        proposed = {row.key: row.value for row in db.query(DiscordBotConfig).all()}
        proposed[key] = value
        try:
            detector_config = load_detector_config(get_settings(), proposed)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        detector_config = None

    record = db.query(DiscordBotConfig).filter(DiscordBotConfig.key == key).first()
    if record:
        record.value = value
    else:
        record = DiscordBotConfig(key=key, value=value)
        db.add(record)
    db.commit()
    if key == "monitored_channel_ids":
        logging.getLogger(__name__).info(
            "address_detector config_saved key=%s source=%s monitored_count=%s",
            key, source or "picker", len(detector_config.monitored_channel_ids) if detector_config else 0,
        )
    if key in KNOWN_KEYS:
        try:
            from discord_bot import bot as discord_bot
            if discord_bot.bot.is_ready():
                await discord_bot.reload_address_detector()
        except Exception:
            # Configuration is durable even if the scheduler process is not
            # the one currently holding the Gateway connection.
            logging.getLogger(__name__).exception("address_detector reload after config save failed")
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_bot_config_set", {"key": key})
    effective = None
    if detector_config is not None:
        effective = {
            **detector_config.public_status(),
            "monitored_channel_ids": _discord_id_strings(detector_config.monitored_channel_ids),
            "excluded_channel_ids": _discord_id_strings(detector_config.excluded_channel_ids),
            "signals_channel_id": detector_config.signals_channel_id,
            "moderator_role_id": detector_config.moderator_role_id,
        }
    return {"status": "ok", "key": key, "value": value, "effective": effective}


# ══════════════════════════════════════════════════════════════════════════════
# TICKET SETTINGS — the compatibility API remains here, while the editor lives
# on /admin/tickets. The bot and both frontends consume the same normalized
# config/tickets.json document.
# ══════════════════════════════════════════════════════════════════════════════

def _validate_tickets_config(config: dict) -> Optional[str]:
    from discord_bot.ticket_config import normalize_tickets_config, validate_tickets_config
    return validate_tickets_config(normalize_tickets_config(config))


@panel_router.get("/api/v1/admin/tickets/config")
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
    from discord_bot import bot as discord_bot
    return {
        "config": config,
        "can_manage_config": check_permission(admin, "discord_bot", "manage_config"),
        "panel_state": discord_bot.get_ticket_panel_state(),
    }


@panel_router.put("/api/v1/admin/tickets/config")
@panel_router.put("/api/v1/admin/discord-bot/tickets-config")
async def api_admin_tickets_config_save(request: Request, db: Session = Depends(get_db)):
    """Save policy; publishing Discord messages is an explicit second action."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    body = await request.json()
    config = body.get("config")
    from discord_bot.ticket_config import normalize_tickets_config
    config = normalize_tickets_config(config)
    error = _validate_tickets_config(config)
    if error:
        raise HTTPException(status_code=400, detail=error)

    from discord_bot import data as bot_data
    bot_data.set_tickets_config(config)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_bot_tickets_config_set", {
        "types": list((config.get("types") or {}).keys()),
        "buttons": list((config.get("buttons") or {}).keys()),
    })
    return {"status": "ok", "config": config}


@panel_router.post("/api/v1/admin/tickets/config/apply")
async def api_admin_tickets_config_apply(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    from discord_bot import bot as discord_bot
    result = await discord_bot.publish_ticket_panels()
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_bot_tickets_config_applied", result)
    return {"status": "ok", **result}


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
    if action_type not in _BROADCAST_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported broadcast action_type")

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

    if action_type not in _BROADCAST_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported broadcast action_type")

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
