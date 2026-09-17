"""
Server Moderation module.

API routes (for the game server):
  POST /api/v1/server/players          – replace online players snapshot
  POST /api/v1/server/anticheat        – report anticheat event
  GET  /api/v1/server/actions          – get pending action queue
  POST /api/v1/server/actions/{id}/ack – acknowledge executed action

Admin panel routes:
  GET  /admin/moderation               – main page with 4 tabs
  POST /admin/moderation/action        – enqueue kick / ban / message
  POST /admin/moderation/anticheat/{id}/review – mark AC event reviewed
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional, List
import secrets
from datetime import datetime, timedelta

from database import get_db, get_game_db
from security import check_permission, check_moderation_action_permission, csrf_token_from_request
from services_player_privacy import can_view_player_ips, redact_anomaly_evidence, redact_sauron_results
from config import get_settings
from security import verify_external_api_key_dependency
from services import (
    PunishmentService,
    ServerModerationService,
    PlayerService,
    prepare_admin_message,
    format_admin_issuer_name,
    LabelCategoryService, can_manage_secret_labels,
    LabelFolderService, filter_visible_label_categories, filter_visible_label_folders,
)
from services_ai_dossier import get_dossier_triage_map
from schemas import (
    ServerPlayersReport, ServerPlayersReportResult,
    AnticheatEventIn, AnticheatEventOut,
    ServerActionOut, ServerActionAck,
    QueueActionIn, QueueActionResult,
)
from models import AdminUser, AdminSession, LabelCategory, PlayerLabel, PlayerData, DisguiseProfile
import logging

settings = get_settings()
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)

# ── Reusable helpers (copied from admin.py pattern) ──────────────────────────

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


async def ensure_csrf(request: Request, db: Session):
    if request.url.path.startswith("/api/") and getattr(request.state, "nd_auth_type", None) == "login_key":
        return
    """Validate the CSRF header used by JSON clients or the legacy form field."""
    csrf_token = csrf_token_from_request(request)
    if not csrf_token:
        form = await request.form()
        csrf_token = form.get("csrf_token")
    session_csrf = request.session.get("csrf_token")
    if csrf_token and session_csrf and secrets.compare_digest(str(csrf_token), str(session_csrf)):
        return
    raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _fmt_playtime(seconds: Optional[int]) -> str:
    if not seconds:
        return "0ч 0м"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}ч {m}м"


# ════════════════════════════════════════════════════════════════════════════
# API ROUTER  (for the game server, protected by external API key)
# ════════════════════════════════════════════════════════════════════════════

api_router = APIRouter(
    prefix="/api/v1/server",
    tags=["server-moderation"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)


@api_router.post("/players", response_model=ServerPlayersReportResult)
def report_online_players(
    report: ServerPlayersReport,
    server_name: Optional[str] = Query(None, description="Имя сервера-отправителя"),
    db: Session = Depends(get_game_db),
):
    """Replace the full snapshot of currently online players for a server.

    Uses per-player TTL tracking — players age out individually, so one
    server's report cannot wipe another server's online players.
    """
    svc = ServerModerationService(db)
    count = svc.replace_online_players(report.players, server_name=server_name)
    _update_server_live_state(db, server_name, report.submarine_health, report.supply_shuttle_eta)
    if server_name:
        from routes.dispatch import _queue_round_status_refresh
        _queue_round_status_refresh(db, server_name)
    return ServerPlayersReportResult(status="ok", count=count)


def _update_server_live_state(
    db: Session,
    server_name: Optional[str],
    submarine_health: Optional[int],
    supply_shuttle_eta: Optional[int],
):
    """Upsert the live per-server state (submarine health, supply shuttle ETA).

    Stored as an absolute UTC arrival timestamp (not the raw seconds-remaining)
    so consumers (the Discord round-status embed) can compute an accurate
    countdown regardless of when they last refreshed, rather than a value that
    goes stale between the game server's periodic reports.
    """
    if submarine_health is None and supply_shuttle_eta is None:
        return
    from models import ServerLiveState

    sn = (server_name or "UNKNOWN").strip()
    row = db.query(ServerLiveState).filter(ServerLiveState.server_name == sn).first()
    if not row:
        row = ServerLiveState(server_name=sn)
        db.add(row)

    if submarine_health is not None:
        row.submarine_health = submarine_health
    if supply_shuttle_eta is not None:
        row.supply_shuttle_eta_seconds = supply_shuttle_eta
        row.shuttle_arrival_at = datetime.utcnow() + timedelta(seconds=supply_shuttle_eta)
    else:
        row.supply_shuttle_eta_seconds = None
        row.shuttle_arrival_at = None

    db.commit()


@api_router.post("/anticheat", response_model=AnticheatEventOut)
def report_anticheat_event(
    event: AnticheatEventIn,
    db: Session = Depends(get_game_db),
):
    """Record a single anticheat trigger from the game server."""
    svc = ServerModerationService(db)
    ev = svc.add_anticheat_event(event)
    return ev


@api_router.get("/actions")
def get_pending_actions(
    server_name: Optional[str] = Query(None, description="Имя сервера — получить только свои задачи"),
    db: Session = Depends(get_game_db),
):
    """Return pending actions for the specified server."""
    svc = ServerModerationService(db)
    rows = svc.get_pending_actions(server_name=server_name)
    return [ServerActionOut.from_v2(r) for r in rows]


@api_router.post("/actions/{action_id}/ack")
def acknowledge_action(
    action_id: int,
    ack: ServerActionAck,
    db: Session = Depends(get_game_db),
):
    """Acknowledge that the game server executed (or failed) a queued action.
    Include server_name in the body so NexusData can track per-server completion for broadcast actions."""
    svc = ServerModerationService(db)
    ok = svc.acknowledge_action(action_id, ack.success, ack.error_message, server_name=ack.server_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Action not found")
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL ROUTER  (HTML pages, protected by admin session)
# ════════════════════════════════════════════════════════════════════════════

panel_router = APIRouter()


def _moderation_action_permission(action_type: Optional[str]) -> str:
    """Maps a kick/ban/unban/message action to its own registered permission
    (permissions_registry.py) instead of the generic "edit"."""
    return {"kick": "kick", "ban": "ban", "unban": "ban",
            "message": "message", "msg_box": "message"}.get(action_type, "edit")


# Сообщение игроку из панели ставится в очередь как msg_box, а не message:
# message игровой сервер шлёт обычным чатом (Game.SendMessage) — оно тонет в
# потоке игрового чата ровно тогда, когда важнее всего, чтобы его прочитали.
# msg_box исполняется как `msg_box_to <steamid> "<text>"` и показывает
# модальное окно поверх игры — тем же способом, которым игрок уже получает
# уведомления о предупреждениях и выговорах (см. services.py,
# _notify_player_about_punishment). Тип действия остаётся "message" во всём
# внешнем контракте (форма, JSON-API, права) — подменяется только то, что
# кладётся в очередь серверу.
_MESSAGE_QUEUE_ACTION = "msg_box"


def _queued_action_type(action_type: str) -> str:
    return _MESSAGE_QUEUE_ACTION if action_type == "message" else action_type


def _resolve_message_server(db: Session, steamid: str, chosen: Optional[str]) -> Optional[str]:
    """Сервер, на который ставить окно с сообщением, если админ не выбрал его
    явно: тот, где игрок сейчас онлайн. Иначе задачу заберут ОБА сервера, и
    тот, где игрока нет, отчитается ошибкой «игрок не найден» — в панели это
    выглядит как сбой, хотя всё в порядке. Игрок оффлайн → None: задача
    останется в очереди, и окно покажет тот сервер, на который он зайдёт (тот
    же приём, что и в services.py::_notify_player_about_punishment)."""
    if chosen:
        return chosen
    try:
        for info in ServerModerationService(db).get_online_players():
            if info.get("player_steamid") == steamid:
                return info.get("server_name")
    except Exception:
        pass
    return None


def _effective_server_name(db: Session, action_type: str, target_steamid: str,
                           server_name: Optional[str]) -> Optional[str]:
    """Нормализует выбранный админом сервер; для сообщений дополнительно
    подставляет тот, где игрок сейчас онлайн (см. _resolve_message_server)."""
    chosen = server_name.strip().upper() if server_name and server_name.strip() else None
    if action_type in ("message", "msg_box"):
        return _resolve_message_server(db, target_steamid, chosen)
    return chosen


def _prefix_admin_message(text: Optional[str]) -> Optional[str]:
    """Сообщение игроку с сайта должно быть узнаваемо как официальное — иначе
    в игровом чате/окне не отличить от розыгрыша другим игроком. Проверка на
    уже проставленный префикс — на случай повторной отправки (сеть, ретрай),
    чтобы префикс не задваивался."""
    return prepare_admin_message(text)


def _queued_message_or_400(text: Optional[str]) -> Optional[str]:
    try:
        return _prefix_admin_message(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@panel_router.get("/adminold/moderation", response_class=HTMLResponse)
async def moderation_page(
    request: Request,
    tab: str = Query("status", pattern=r"^(status|banhammer|logs|commands|anticheat|sauron)$"),
    server: Optional[str] = Query(None, description="Фильтр по серверу: ASPER1 или ASPER2"),
    search: Optional[str] = Query(None, min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Main server moderation page with five tabs."""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = ServerModerationService(db)

    online_players = svc.get_online_players(server_name=server)
    online_count = len(online_players)
    online_servers = svc.get_online_servers()
    can_view_disguise_details = check_permission(current_admin, "disguise", "view_details")
    _enrich_online_disguise(
        db,
        online_players,
        include_details=can_view_disguise_details,
    )
    selected_server = server

    # Round state: last start event per server
    from routes.dispatch import get_round_state
    round_states = {}
    for sn in (["ASPER1", "ASPER2"] if not server else [server]):
        round_states[sn] = get_round_state(db, sn)

    # Banhammer: online + recently offline + search
    recent_offline = svc.get_recent_offline_players(limit=50)

    # Enrich recent_offline with last session logout time
    if recent_offline:
        from models import PlayerSession
        pid_list = [p.player_id for p in recent_offline]
        last_sessions = (
            db.query(PlayerSession)
            .filter(
                PlayerSession.player_id.in_(pid_list),
                PlayerSession.logout_time.isnot(None),
            )
            .order_by(PlayerSession.logout_time.desc())
            .all()
        )
        last_logout_map: dict = {}
        for s in last_sessions:
            if s.player_id not in last_logout_map:
                last_logout_map[s.player_id] = s.logout_time
        
        # Convert times to Moscow timezone and attach
        from datetime import timezone, timedelta
        MSK = timezone(timedelta(hours=3))
        for p in recent_offline:
            logout_dt = last_logout_map.get(p.player_id)
            if logout_dt and logout_dt.tzinfo is None:
                # Assume UTC if naive, convert to MSK
                logout_dt = logout_dt.replace(tzinfo=timezone.utc).astimezone(MSK)
            p.last_session_logout = logout_dt
            
            # Also convert last_login_time to MSK
            if p.last_login_time and p.last_login_time.tzinfo is None:
                p.last_login_time = p.last_login_time.replace(tzinfo=timezone.utc).astimezone(MSK)

    # Search across all players (for Banhammer)
    search_results = []
    if search:
        player_svc = PlayerService(db)
        search_results = player_svc.search_players(search, limit=50)

    # Anticheat
    ac_events = svc.get_anticheat_events(limit=200, reviewed=None)
    # Enrich AC events with player_id for profile link
    ac_enriched = []
    for ev in ac_events:
        ac_enriched.append({
            "id": ev.id,
            "player_steamid": ev.player_steamid,
            "player_name": ev.player_name,
            "player_id": ev.player_id,
            "event_type": ev.event_type,
            "details": ev.details,
            "occurred_at": ev.occurred_at,
            "reviewed": ev.reviewed,
        })

    return templates.TemplateResponse("admin/moderation.html", {
        "request": request,
        "admin": current_admin,
        "active_tab": tab,
        "online_players": online_players,
        "online_count": online_count,
        "online_servers": online_servers,
        "selected_server": selected_server,
        "round_states": round_states,
        "recent_offline": recent_offline,
        "search_results": search_results,
        "search_query": search,
        "ac_events": ac_enriched,
        "fmt_playtime": _fmt_playtime,
        "can_view_disguise_details": can_view_disguise_details,
        "csrf_token": request.session.get("csrf_token"),
    })


@panel_router.post("/adminold/moderation/action")
async def enqueue_moderation_action(
    request: Request,
    action_type: str = Form(...),
    target_steamid: str = Form(...),
    reason: Optional[str] = Form(None),
    duration_minutes: Optional[int] = Form(None),
    message_text: Optional[str] = Form(None),
    server_name: Optional[str] = Form(None),
    redirect_tab: str = Form("status"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Enqueue a kick / ban / unban / message action from the admin panel."""
    if not check_moderation_action_permission(current_admin, _moderation_action_permission(action_type)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    await ensure_csrf(request, db)

    if action_type not in ("kick", "ban", "unban", "message", "msg_box"):
        raise HTTPException(status_code=400, detail="Invalid action_type")

    from schemas import QueueActionIn
    action_in = QueueActionIn(
        action_type=_queued_action_type(action_type),
        target_steamid=target_steamid,
        reason=reason or None,
        duration_minutes=duration_minutes or None,
        message_text=_queued_message_or_400(message_text) if action_type in ("message", "msg_box") else (message_text or None),
        server_name=_effective_server_name(db, action_type, target_steamid, server_name),
    )

    svc = ServerModerationService(db)
    action = svc.enqueue_action(action_in, admin_id=current_admin.id)
    voided_bans = 0
    if action_type == "unban":
        voided_bans = PunishmentService(db).set_all_active_bans_void(target_steamid)
    logger.info(
        "Admin %s queued %s for %s on server=%s (action_id=%s voided_bans=%s)",
        current_admin.discord_username, action_type, target_steamid,
        server_name or "all", action.id, voided_bans,
    )

    # For bans — also create a punishment record
    if action_type == "ban":
        from models import PlayerData, PlayerPunishment
        from services import _now_msk
        player = db.query(PlayerData).filter(PlayerData.player_steamid == target_steamid).first()
        punishment = PlayerPunishment(
            player_id=player.player_id if player else 0,
            player_steamid=target_steamid,
            player_ip=player.last_ip if player else None,
            type="ban",
            reason=reason or None,
            issued_by_admin_id=current_admin.id,
            issued_by_name=format_admin_issuer_name(db, current_admin),
            issued_by_was_superuser=current_admin.is_superuser,
            issued_at=_now_msk(),
            expires_at=None,
            is_active=1,
        )
        db.add(punishment)
        db.commit()
        # Queue Discord notification for the ban
        from routes.game_api import _queue_punishment_discord_notification
        _queue_punishment_discord_notification(db, punishment)

    from routes.admin import log_admin_action, push_flash
    log_admin_action(db, current_admin.id, f"moderation.{action_type}", {
        "target_steamid": target_steamid,
        "reason": reason,
        "server_name": server_name,
        "action_id": action.id,
    })
    srv_label = f" → {server_name}" if server_name else " → все серверы"
    push_flash(request, "success", f"Действие «{action_type}» поставлено в очередь для {target_steamid}{srv_label}")
    return RedirectResponse(url=f"/adminold/moderation?tab={redirect_tab}", status_code=302)


@panel_router.post("/adminold/moderation/anticheat/{event_id}/review")
async def review_anticheat_event(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Mark an anticheat event as reviewed."""
    if not check_permission(current_admin, "moderation", "manage_anticheat"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await ensure_csrf(request, db)
    svc = ServerModerationService(db)
    if not svc.mark_reviewed(event_id):
        raise HTTPException(status_code=404, detail="Event not found")

    from routes.admin import push_flash
    push_flash(request, "success", "Событие отмечено как проверенное")
    return RedirectResponse(url="/adminold/moderation?tab=anticheat", status_code=302)


# ════════════════════════════════════════════════════════════════════════════
# MODERATION JSON API (for the React SPA — mirrors moderation_page/enqueue_moderation_action)
# ════════════════════════════════════════════════════════════════════════════

def _serialize_label_ids(player) -> list[int]:
    raw_labels = player.get("label_ids", []) if isinstance(player, dict) else getattr(player, "labels", [])
    result = []
    for value in raw_labels or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _serialize_online_player(info: dict, dossier_triage: Optional[dict] = None) -> dict:
    flags = info.get("player_flags", {})
    public_name = info.get("public_display_name") or info.get("disguise_alias") or "Маскировка"
    displayed_name = (
        info.get("player_name")
        if info.get("include_disguise_details") or not info.get("is_disguised")
        else public_name
    )
    displayed_forced_name = (
        info.get("forced_display_name")
        if info.get("include_disguise_details") or not info.get("is_disguised")
        else None
    )
    return {
        "player_steamid": info.get("player_steamid"),
        "player_name": displayed_name,
        "forced_display_name": displayed_forced_name,
        "character_name": info.get("character_name"),
        "job_id": info.get("job_id"),
        "ping": info.get("ping"),
        "player_id": info.get("player_id"),
        "rank_id": info.get("rank_id"),
        "server_name": info.get("server_name"),
        "join_time": info["join_time"].isoformat() if info.get("join_time") else None,
        "last_seen": info["last_seen"].isoformat() if info.get("last_seen") else None,
        "total_play_time": (
            info.get("total_play_time")
            if info.get("include_disguise_details") or not info.get("is_disguised")
            else None
        ),
        "is_new_player": info.get("is_new_player"),
        "is_superuser": bool(info.get("is_superuser")),
        "is_disguised": bool(info.get("is_disguised")),
        "disguise_alias": info.get("disguise_alias"),
        "public_display_name": info.get("public_display_name"),
        "disguise_mode": info.get("disguise_mode"),
        "disguise_owner_name": info.get("disguise_owner_name"),
        "disguise_real_name": info.get("disguise_real_name"),
        "has_discord_link": bool(info.get("has_discord_link")),
        "label_ids": _serialize_label_ids(info),
        "has_labels": bool(flags.get("has_labels")),
        "has_notes": bool(flags.get("has_notes")),
        "has_active_punishments": bool(flags.get("has_active_punishments")),
        "has_open_anomalies": bool(flags.get("has_open_anomalies")),
        "has_previous_names": bool(flags.get("has_previous_names")),
        "has_ip_history": bool(flags.get("has_ip_history")),
        "last_activity": info.get("last_activity") or (info["last_seen"].isoformat() if info.get("last_seen") else None) or (info["join_time"].isoformat() if info.get("join_time") else None),
        "dossier_triage": dossier_triage,
    }


def _serialize_offline_player(p, flags: Optional[dict] = None, dossier_triage: Optional[dict] = None, is_superuser: bool = False) -> dict:
    flags = flags or {}
    return {
        "player_id": p.player_id,
        "player_steamid": p.player_steamid,
        "player_last_time_name": p.player_last_time_name,
        "forced_display_name": p.forced_display_name,
        "rank_id": p.rank_id,
        "first_login": p.first_login.isoformat() if p.first_login else None,
        "last_login_time": p.last_login_time.isoformat() if p.last_login_time else None,
        "last_session_logout": p.last_session_logout.isoformat() if getattr(p, "last_session_logout", None) else None,
        "play_time_hours": p.play_time_hours,
        "has_discord_link": bool(getattr(p, "discord_id", None)),
        "is_superuser": bool(is_superuser),
        "label_ids": flags.get("label_ids", _serialize_label_ids(p)),
        "has_labels": bool(flags.get("has_labels")),
        "has_notes": bool(flags.get("has_notes")),
        "has_active_punishments": bool(flags.get("has_active_punishments")),
        "has_open_anomalies": bool(flags.get("has_open_anomalies")),
        "has_previous_names": bool(flags.get("has_previous_names")),
        "has_ip_history": bool(flags.get("has_ip_history")),
        "last_activity": p.last_login_time.isoformat() if p.last_login_time else None,
        # Same "under 2 hours total playtime" threshold as the online-players
        # is_new_player flag (services.py) — kept in sync so a player looks
        # equally "new" whether they're currently online or not.
        "is_new_player": p.play_time_hours < 2,
        "dossier_triage": dossier_triage,
    }


def _serialize_search_player(p, flags: Optional[dict] = None, dossier_triage: Optional[dict] = None, is_superuser: bool = False) -> dict:
    flags = flags or {}
    """Serialize a player search result using the moderation table contract."""
    return {
        "player_id": p.player_id,
        "player_steamid": p.player_steamid,
        "player_last_time_name": p.player_last_time_name,
        "forced_display_name": p.forced_display_name,
        "rank_id": p.rank_id,
        "first_login": p.first_login.isoformat() if p.first_login else None,
        "last_login_time": p.last_login_time.isoformat() if p.last_login_time else None,
        "last_session_logout": None,
        "play_time_hours": p.play_time_hours,
        "is_new_player": p.play_time_hours < 2,
        "is_superuser": bool(is_superuser),
        "has_discord_link": bool(getattr(p, "discord_id", None)),
        "label_ids": flags.get("label_ids", _serialize_label_ids(p)),
        "has_labels": bool(flags.get("has_labels")),
        "has_notes": bool(flags.get("has_notes")),
        "has_active_punishments": bool(flags.get("has_active_punishments")),
        "has_open_anomalies": bool(flags.get("has_open_anomalies")),
        "has_previous_names": bool(flags.get("has_previous_names")),
        "has_ip_history": bool(flags.get("has_ip_history")),
        "last_activity": p.last_login_time.isoformat() if p.last_login_time else None,
        "dossier_triage": dossier_triage,
    }


def _serialize_round_state_json(rs: dict) -> dict:
    last_end = rs.get("last_end")
    return {
        "active": rs.get("active", False),
        "location": rs.get("location"),
        "submarine_name": rs.get("submarine_name"),
        "mission": rs.get("mission"),
        "started_at": rs["started_at"].isoformat() if rs.get("started_at") else None,
        "players": rs.get("players") or [],
        "last_end_at": last_end.created_at.isoformat() if last_end and last_end.created_at else None,
        "last_end_mission_completed": bool(last_end.mission_completed) if last_end else None,
    }


def _enrich_online_disguise(db: Session, online_players: list[dict], *, include_details: bool = False) -> None:
    """Attach moderation-only disguise metadata using the real SteamID key."""
    steamids = [str(info.get("player_steamid")) for info in online_players if info.get("player_steamid")]
    if not steamids:
        return
    players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steamids)).all()
    by_steamid = {str(player.player_steamid): player for player in players}
    disguised = [player for player in players if player.disguise_enabled]
    profiles_by_player: dict[int, list[DisguiseProfile]] = {}
    if disguised:
        profiles = (
            db.query(DisguiseProfile)
            .filter(DisguiseProfile.player_id.in_([player.player_id for player in disguised]))
            .order_by(DisguiseProfile.sort_order, DisguiseProfile.id)
            .all()
        )
        for profile in profiles:
            profiles_by_player.setdefault(profile.player_id, []).append(profile)

    for info in online_players:
        player = by_steamid.get(str(info.get("player_steamid")))
        if not player or not player.disguise_enabled:
            info.update({
                "is_disguised": False,
                "disguise_alias": None,
                "public_display_name": None,
                "disguise_mode": None,
                "disguise_owner_name": None,
                "disguise_real_name": None,
            })
            continue
        selected = next(
            (profile for profile in profiles_by_player.get(player.player_id, []) if profile.id == player.disguise_profile_id),
            None,
        )
        if selected is None and player.disguise_fixed_alias:
            selected = next(
                (profile for profile in profiles_by_player.get(player.player_id, [])
                 if profile.alias.casefold() == player.disguise_fixed_alias.casefold()),
                None,
            )
        owner = player.linked_admin_user
        owner_name = (owner.discord_username or "").strip() if owner else ""
        # The game server deliberately reports the authenticated/original
        # name to NexusData. Resolve a public alias only for fixed profiles;
        # random selection is local to the game session and is not in the
        # current server payload.
        public_alias = (
            selected.alias if player.disguise_mode == "fixed" and selected
            else player.disguise_fixed_alias if player.disguise_mode == "fixed" else info.get("public_player_name") or "Маскировка"
        )
        info["include_disguise_details"] = include_details
        info.update({
            "is_disguised": True,
            # The alias is public game-facing data and must remain visible to
            # ordinary moderators; only the identity behind it is restricted.
            "disguise_alias": public_alias,
            "public_display_name": public_alias,
            "disguise_mode": (player.disguise_mode or "random") if include_details else None,
            "disguise_owner_name": (owner_name or f"Пользователь #{player.linked_admin_id}") if include_details else None,
            "disguise_real_name": player.display_name if include_details else None,
        })


@panel_router.get("/api/v1/admin/moderation/overview")
async def api_moderation_overview(
    server: Optional[str] = Query(None),
    search: Optional[str] = Query(None, min_length=2),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Combined status/banhammer/anticheat data — mirrors moderation_page's eager loads."""
    from routes.dispatch import get_round_state

    svc = ServerModerationService(db)
    online_players = svc.get_online_players(server_name=server)
    online_servers = svc.get_online_servers()
    if check_permission(current_admin, "moderation", "view"):
        can_view_disguise_details = check_permission(current_admin, "disguise", "view_details")
        _enrich_online_disguise(
            db,
            online_players,
            include_details=can_view_disguise_details,
        )
    else:
        can_view_disguise_details = False

    round_states = {}
    for sn in (["ASPER1", "ASPER2"] if not server else [server]):
        round_states[sn] = _serialize_round_state_json(get_round_state(db, sn))

    recent_offline = svc.get_recent_offline_players(limit=50)
    if recent_offline:
        from models import PlayerSession
        pid_list = [p.player_id for p in recent_offline]
        last_sessions = (
            db.query(PlayerSession)
            .filter(PlayerSession.player_id.in_(pid_list), PlayerSession.logout_time.isnot(None))
            .order_by(PlayerSession.logout_time.desc())
            .all()
        )
        last_logout_map = {}
        for s in last_sessions:
            if s.player_id not in last_logout_map:
                last_logout_map[s.player_id] = s.logout_time
        from datetime import timezone
        MSK = timezone(timedelta(hours=3))
        for p in recent_offline:
            logout_dt = last_logout_map.get(p.player_id)
            if logout_dt and logout_dt.tzinfo is None:
                logout_dt = logout_dt.replace(tzinfo=timezone.utc).astimezone(MSK)
            p.last_session_logout = logout_dt
            if p.last_login_time and p.last_login_time.tzinfo is None:
                p.last_login_time = p.last_login_time.replace(tzinfo=timezone.utc).astimezone(MSK)

    search_results = []
    if search:
        raw_results = PlayerService(db).search_players(search, limit=50)
        search_results = raw_results

    all_label_categories = LabelCategoryService(db).get_all()
    label_categories = filter_visible_label_categories(all_label_categories, current_admin)
    label_folders = filter_visible_label_folders(LabelFolderService(db).get_all(), all_label_categories, current_admin)
    visible_label_ids = {category.id for category in label_categories}
    from repositories import PlayerRepository
    all_player_ids = [p.get("player_id") for p in online_players if p.get("player_id")]
    all_player_ids.extend(p.player_id for p in recent_offline if p.player_id)
    all_player_ids.extend(p.player_id for p in search_results if p.player_id)
    player_flags = PlayerRepository(db).get_player_flags(
        list(dict.fromkeys(all_player_ids)),
        visible_label_ids=visible_label_ids,
    )
    dossier_player_ids = list(dict.fromkeys(all_player_ids))
    if not can_manage_secret_labels(current_admin) and dossier_player_ids:
        secret_player_ids = {
            player_id for player_id, in db.query(PlayerLabel.player_id).join(
                LabelCategory, PlayerLabel.category_id == LabelCategory.id,
            ).filter(
                PlayerLabel.player_id.in_(dossier_player_ids),
                LabelCategory.is_secret.is_(True),
            ).distinct().all()
        }
        dossier_player_ids = [player_id for player_id in dossier_player_ids if player_id not in secret_player_ids]
    dossier_triage = get_dossier_triage_map(db, dossier_player_ids)
    # The rank warning is useful for ordinary players, but it is noise for a
    # superuser's linked game profile. Resolve this once for every row instead
    # of making the frontend guess from the current viewer's permissions.
    from models import AdminUser, PlayerData
    moderation_steamids = {
        str(info.get("player_steamid"))
        for info in online_players
        if info.get("player_steamid")
    }
    moderation_steamids.update(str(p.player_steamid) for p in recent_offline if p.player_steamid)
    moderation_steamids.update(str(p.player_steamid) for p in search_results if p.player_steamid)
    superuser_steamids = set()
    if moderation_steamids:
        superuser_steamids = {
            str(steamid)
            for steamid, in db.query(PlayerData.player_steamid)
            .join(AdminUser, PlayerData.linked_admin_id == AdminUser.id)
            .filter(
                PlayerData.player_steamid.in_(moderation_steamids),
                AdminUser.is_superuser.is_(True),
            )
            .all()
        }
    for info in online_players:
        flags = player_flags.get(info.get("player_id"), {})
        info["player_flags"] = flags
        info["label_ids"] = flags.get("label_ids", info.get("label_ids", []))
        info["is_superuser"] = str(info.get("player_steamid")) in superuser_steamids

    ac_events = svc.get_anticheat_events(limit=200, reviewed=None)
    ac_enriched = [
        {
            "id": ev.id, "player_steamid": ev.player_steamid, "player_name": ev.player_name,
            "player_id": ev.player_id, "event_type": ev.event_type, "details": ev.details,
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None, "reviewed": bool(ev.reviewed),
        }
        for ev in ac_events
    ]

    return {
        "online_players": [_serialize_online_player(p, dossier_triage.get(p.get("player_id"))) for p in online_players],
        "online_count": len(online_players),
        "online_servers": online_servers,
        "selected_server": server,
        "round_states": round_states,
        "recent_offline": [_serialize_offline_player(p, player_flags.get(p.player_id), dossier_triage.get(p.player_id), str(p.player_steamid) in superuser_steamids) for p in recent_offline],
        "search_results": [_serialize_search_player(p, player_flags.get(p.player_id), dossier_triage.get(p.player_id), str(p.player_steamid) in superuser_steamids) for p in search_results],
        "search_query": search,
        "ac_events": ac_enriched,
        "ranks": settings.ranks_mapping,
        "label_categories": [{"id": c.id, "name": c.name, "color": c.color, "icon": c.icon, "is_secret": bool(c.is_secret), "folder_id": c.folder_id, "sort_order": c.sort_order if can_manage_secret_labels(current_admin) else index} for index, c in enumerate(label_categories)],
        "label_folders": [{"id": folder.id, "name": folder.name, "sort_order": folder.sort_order if can_manage_secret_labels(current_admin) else index} for index, folder in enumerate(label_folders)],
        "can_edit": check_permission(current_admin, "moderation", "edit"),
        "can_delete": check_permission(current_admin, "moderation", "delete"),
        "can_message": check_moderation_action_permission(current_admin, "message"),
        "can_kick": check_moderation_action_permission(current_admin, "kick"),
        "can_ban": check_moderation_action_permission(current_admin, "ban"),
        "can_view_disguise_details": can_view_disguise_details,
        # Быстрая выдача предупреждения прямо из списка онлайна — право то же,
        # что и на обычную выдачу наказаний, а не moderation:edit.
        "can_issue_punishment": check_permission(current_admin, "punishments", "issue"),
    }


@panel_router.post("/api/v1/admin/moderation/action")
async def api_enqueue_moderation_action(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """JSON variant of enqueue_moderation_action — same side effects, JSON in/out."""
    await ensure_csrf(request, db)
    body = await request.json()
    action_type = body.get("action_type")

    if not check_moderation_action_permission(current_admin, _moderation_action_permission(action_type)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    target_steamid = body.get("target_steamid")
    reason = body.get("reason")
    duration_minutes = body.get("duration_minutes")
    message_text = body.get("message_text")
    server_name = body.get("server_name")

    if action_type not in ("kick", "ban", "unban", "message", "msg_box"):
        raise HTTPException(status_code=400, detail="Invalid action_type")
    if not target_steamid:
        raise HTTPException(status_code=400, detail="target_steamid required")

    from schemas import QueueActionIn
    action_in = QueueActionIn(
        action_type=_queued_action_type(action_type),
        target_steamid=target_steamid,
        reason=reason or None,
        duration_minutes=duration_minutes or None,
        message_text=_queued_message_or_400(message_text) if action_type in ("message", "msg_box") else (message_text or None),
        server_name=_effective_server_name(db, action_type, target_steamid, server_name),
    )

    svc = ServerModerationService(db)
    action = svc.enqueue_action(action_in, admin_id=current_admin.id)
    voided_bans = 0
    if action_type == "unban":
        voided_bans = PunishmentService(db).set_all_active_bans_void(target_steamid)
    logger.info(
        "Admin %s queued %s for %s on server=%s (action_id=%s voided_bans=%s)",
        current_admin.discord_username, action_type, target_steamid,
        server_name or "all", action.id, voided_bans,
    )

    if action_type == "ban":
        from models import PlayerData, PlayerPunishment
        from services import _now_msk
        player = db.query(PlayerData).filter(PlayerData.player_steamid == target_steamid).first()
        punishment = PlayerPunishment(
            player_id=player.player_id if player else 0,
            player_steamid=target_steamid,
            player_ip=player.last_ip if player else None,
            type="ban",
            reason=reason or None,
            issued_by_admin_id=current_admin.id,
            issued_by_name=format_admin_issuer_name(db, current_admin),
            issued_by_was_superuser=current_admin.is_superuser,
            issued_at=_now_msk(),
            expires_at=None,
            is_active=1,
        )
        db.add(punishment)
        db.commit()
        from routes.game_api import _queue_punishment_discord_notification
        _queue_punishment_discord_notification(db, punishment)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, f"moderation.{action_type}", {
        "target_steamid": target_steamid, "reason": reason, "server_name": server_name, "action_id": action.id,
    })
    return {"status": "ok", "action_id": action.id}


@panel_router.post("/api/v1/admin/moderation/anticheat/{event_id}/review")
async def api_review_anticheat_event(
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "moderation", "manage_anticheat"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    svc = ServerModerationService(db)
    if not svc.mark_reviewed(event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════════════════
# SAURON'S EYE — API endpoints (loaded lazily via AJAX)
# ════════════════════════════════════════════════════════════════════════════

@panel_router.get("/api/v1/sauron/twinks")
async def sauron_twinks(
    request: Request,
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse

    try:
        from task_scheduler import scheduler, Priority
        from workers import task_sauron_twinks
        task_id = "sauron_twinks"
        result = scheduler.get_result(task_id)
        if result and result.status == "done" and result.finished_at:
            from datetime import datetime
            age = (datetime.utcnow() - result.finished_at).total_seconds()
            if age < 600:
                return JSONResponse(redact_sauron_results(
                    result.data.get("results", []),
                    allow=can_view_player_ips(admin),
                ))
        scheduler.submit(task_sauron_twinks, priority=Priority.NORMAL, task_id=task_id, timeout=90.0)
        return JSONResponse({"status": "running", "message": "Анализ запущен, обновите через несколько секунд"}, status_code=202)
    except ImportError:
        import services_sauron
        results = services_sauron.detect_all_twink_pairs(db, days=days)
        return JSONResponse(redact_sauron_results(
            results,
            allow=can_view_player_ips(admin),
        ))


@panel_router.get("/api/v1/sauron/deviant")
async def sauron_deviant(
    request: Request,
    days: int = Query(30, ge=7, le=180),
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse

    try:
        from task_scheduler import scheduler, Priority
        from workers import task_sauron_deviant
        task_id = "sauron_deviant"
        result = scheduler.get_result(task_id)
        if result and result.status == "done" and result.finished_at:
            from datetime import datetime
            age = (datetime.utcnow() - result.finished_at).total_seconds()
            if age < 600:
                return JSONResponse(result.data.get("results", []))
        scheduler.submit(task_sauron_deviant, priority=Priority.NORMAL, task_id=task_id, timeout=90.0)
        return JSONResponse({"status": "running", "message": "Анализ запущен, обновите через несколько секунд"}, status_code=202)
    except ImportError:
        import services_sauron
        results = services_sauron.detect_deviant_behavior(db, days=days, min_incidents=2)
        return JSONResponse(results)


@panel_router.get("/api/v1/sauron/summary")
async def sauron_summary(
    request: Request,
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403)

    from fastapi.responses import JSONResponse

    try:
        from task_scheduler import scheduler, Priority
        from workers import task_sauron_summary
        task_id = "sauron_summary"
        result = scheduler.get_result(task_id)
        if result and result.status == "done" and result.data:
            from datetime import datetime
            age = (datetime.utcnow() - result.finished_at).total_seconds() if result.finished_at else 9999
            if age < 120:
                return JSONResponse(result.data)
        scheduler.submit(task_sauron_summary, priority=Priority.NORMAL, task_id=task_id, timeout=30.0)
        return JSONResponse({"twink_pairs_total": "…", "deviant_total": "…", "twink_high_risk": "…", "deviant_high_risk": "…"})
    except ImportError:
        import services_sauron
        return JSONResponse(services_sauron.get_sauron_summary(db))


# ════════════════════════════════════════════════════════════════════════════
# ANOMALIES (Moderation → Аномалии) — stored findings from the background
# scan (workers.task_scan_anomalies, every 30 min — see services_anomaly.py).
# Unlike the on-demand /api/v1/sauron/* endpoints above, this reads
# persisted PlayerAnomaly rows — nothing here triggers a scan itself.
# ════════════════════════════════════════════════════════════════════════════

def _serialize_anomaly_player(db: Session, player_id: Optional[int]) -> Optional[dict]:
    if not player_id:
        return None
    from models import PlayerData
    p = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not p:
        return {"player_id": player_id, "name": None, "real_name": None, "steamid": None}
    return {
        "player_id": p.player_id,
        "name": p.forced_display_name or p.player_last_time_name,
        "real_name": p.player_last_time_name if p.forced_display_name else None,
        "steamid": p.player_steamid,
    }


def _serialize_anomaly(db: Session, a, *, allow_ip: bool = False) -> dict:
    return {
        "id": a.id,
        "category": a.category,
        "subtype": a.subtype,
        "player": _serialize_anomaly_player(db, a.player_id),
        "related_player": _serialize_anomaly_player(db, a.related_player_id),
        "score": a.score,
        "evidence": redact_anomaly_evidence(a.evidence or {}, allow=allow_ip),
        "detected_at": a.detected_at.isoformat() if a.detected_at else None,
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        "status": a.status,
        "auto_banned": a.auto_banned,
    }


@panel_router.get("/api/v1/admin/moderation/anomalies")
async def api_list_anomalies(
    request: Request,
    category: Optional[str] = Query(None, description="twink | behavior"),
    status: Optional[str] = Query("new", description="new | reviewed | dismissed | all"),
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "manage_anticheat"):
        raise HTTPException(status_code=403)

    from models import PlayerAnomaly
    q = db.query(PlayerAnomaly)
    if category:
        q = q.filter(PlayerAnomaly.category == category)
    if status and status != "all":
        q = q.filter(PlayerAnomaly.status == status)
    rows = q.order_by(PlayerAnomaly.score.desc(), PlayerAnomaly.last_seen_at.desc()).limit(300).all()

    from fastapi.responses import JSONResponse
    return JSONResponse({
        "anomalies": [
            _serialize_anomaly(db, a, allow_ip=can_view_player_ips(admin))
            for a in rows
        ],
        "can_manage": check_permission(admin, "moderation", "manage_anticheat"),
        "nd_autoban_enabled": __import__("discord_bot.data", fromlist=["data"]).is_nd_autoban_enabled(),
    })


@panel_router.put("/api/v1/admin/moderation/anomalies/autoban")
async def api_set_nd_autoban(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "manage_anticheat"):
        raise HTTPException(status_code=403)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    from discord_bot import data as bot_data
    bot_data.set_nd_autoban_enabled(enabled)
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "nd_autoban_set", {"enabled": enabled})
    return {"status": "ok", "enabled": enabled}


@panel_router.post("/api/v1/admin/moderation/anomalies/{anomaly_id}/review")
async def api_review_anomaly(
    anomaly_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request, db)
    if not check_permission(admin, "moderation", "manage_anticheat"):
        raise HTTPException(status_code=403)

    from models import PlayerAnomaly
    row = db.query(PlayerAnomaly).filter(PlayerAnomaly.id == anomaly_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Не найдено")

    body = await request.json()
    new_status = body.get("status")
    if new_status not in ("reviewed", "dismissed", "new"):
        raise HTTPException(status_code=400, detail="Некорректный статус")

    row.status = new_status
    row.reviewed_by_admin_id = admin.id
    row.reviewed_at = datetime.utcnow()
    db.commit()

    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "ok"})


# ════════════════════════════════════════════════════════════════════════════
# КОНСОЛЬ ИГРОВОГО СЕРВЕРА (Pterodactyl) — прямой доступ к консоли боевого
# сервера, поэтому это самая чувствительная часть страницы «Модерирование».
# Меры безопасности:
#   1. Отдельное право НА КАЖДЫЙ сервер (moderation:console_server1/2) —
#      никакого общего "console", которое давало бы доступ ко всем сразу.
#   2. API-ключ Pterodactyl никогда не покидает бэкенд — команда идёт
#      исключительно server-to-server (httpx), в браузер токен не отдаётся
#      ни в каком виде (в т.ч. отдельный websocket-токен для живого вывода
#      консоли сюда сознательно не реализован — см. GAME_SERVER_API_GUIDE.md
#      про то, почему это отдельная, более объёмная задача).
#   3. Каждая отправленная команда пишется в AdminActionLog ПОЛНЫМ текстом —
#      это, по сути, RCE над игровым сервером, и должно быть безусловно
#      разбираемо постфактум "кто, что и когда ввёл".
#   4. Простой троттлинг на команду (см. _console_cooldowns) — защита от
#      одной скомпрометированной сессии, заливающей консоль командами на
#      машинной скорости.
#   5. Валидация длины/пустоты на входе — то, что долетает до Pterodactyl,
#      уже прошло через строгую проверку.
# ════════════════════════════════════════════════════════════════════════════

import time as _time

_CONSOLE_SERVERS = {
    "ASPER1": ("console_server1", lambda s: s.pterodactyl_server1_uuid),
    "ASPER2": ("console_server2", lambda s: s.pterodactyl_server2_uuid),
}
_CONSOLE_COMMAND_MAX_LEN = 500
_CONSOLE_COOLDOWN_SECONDS = 1.5
_console_cooldowns: dict = {}  # (admin_id, server) -> monotonic timestamp последней команды


def _console_target(server: str, admin: AdminUser):
    """Проверяет право на КОНКРЕТНЫЙ сервер и настройку UUID. Возвращает
    (panel_url, api_key, server_uuid) или бросает HTTPException."""
    server = (server or "").strip().upper()
    entry = _CONSOLE_SERVERS.get(server)
    if not entry:
        raise HTTPException(status_code=400, detail="Неизвестный сервер (ожидается ASPER1 или ASPER2)")
    perm_key, uuid_getter = entry
    if not check_permission(admin, "moderation", perm_key):
        raise HTTPException(status_code=403, detail="Нет доступа к консоли этого сервера")

    cfg = get_settings()
    if not cfg.pterodactyl_panel_url or not cfg.pterodactyl_api_key:
        raise HTTPException(status_code=503, detail="Интеграция с Pterodactyl не настроена")
    server_uuid = uuid_getter(cfg)
    if not server_uuid:
        raise HTTPException(status_code=400, detail=f"UUID сервера {server} не настроен (pterodactyl_server{'1' if server=='ASPER1' else '2'}_uuid)")
    return cfg.pterodactyl_panel_url, cfg.pterodactyl_api_key, server_uuid


@panel_router.get("/api/v1/admin/moderation/console/permissions")
async def api_console_permissions(request: Request, db: Session = Depends(get_db)):
    """Какие консоли реально доступны текущему админу — фронтенд по этому
    списку решает, что показывать (не полагаясь на 403 постфактум)."""
    admin = get_current_admin(request, db)
    return {
        "can_manage_settings": bool(admin.is_superuser),
        "servers": [
            {"server": s, "enabled": check_permission(admin, "moderation", perm_key)}
            for s, (perm_key, _) in _CONSOLE_SERVERS.items()
        ],
    }


@panel_router.get("/api/v1/admin/moderation/console/settings")
async def api_console_settings(request: Request, db: Session = Depends(get_db)):
    """Console bindings are sensitive deployment settings, never shown to non-SUs."""
    admin = get_current_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403)
    cfg = get_settings()
    return {
        "panel_url": cfg.pterodactyl_panel_url,
        "api_key": "********" if cfg.pterodactyl_api_key else "",
        "server1_uuid": cfg.pterodactyl_server1_uuid,
        "server2_uuid": cfg.pterodactyl_server2_uuid,
    }


@panel_router.put("/api/v1/admin/moderation/console/settings")
async def api_console_settings_save(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403)
    body = await request.json()
    mapping = {
        "panel_url": "PTERODACTYL_PANEL_URL", "api_key": "PTERODACTYL_API_KEY",
        "server1_uuid": "PTERODACTYL_SERVER1_UUID", "server2_uuid": "PTERODACTYL_SERVER2_UUID",
    }
    from config import update_env_file
    updates = {}
    for field, env_key in mapping.items():
        if field not in body:
            continue
        value = str(body[field] or "").strip()
        if field == "api_key" and value == "********":
            continue
        updates[env_key] = value
    update_env_file(".env", updates)
    from config import reset_settings_cache
    reset_settings_cache()
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "console_settings_save", {"servers": ["ASPER1", "ASPER2"]})
    return {"status": "ok"}


@panel_router.get("/api/v1/admin/moderation/console/{server}/status")
async def api_console_status(server: str, request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    panel_url, api_key, server_uuid = _console_target(server, admin)

    import httpx
    url = f"{panel_url.rstrip('/')}/api/client/servers/{server_uuid}/resources"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json().get("attributes", {})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ошибка обращения к Pterodactyl: {e}")

    resources = data.get("resources", {})
    return {
        "state": data.get("current_state"),
        "cpu_percent": resources.get("cpu_absolute"),
        "memory_bytes": resources.get("memory_bytes"),
        "memory_limit_bytes": resources.get("memory_limit_bytes"),
    }


@panel_router.post("/api/v1/admin/moderation/console/{server}/command")
async def api_console_command(server: str, request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    panel_url, api_key, server_uuid = _console_target(server, admin)
    server_key = server.strip().upper()

    now = _time.monotonic()
    cooldown_key = (admin.id, server_key)
    last = _console_cooldowns.get(cooldown_key)
    if last is not None and (now - last) < _CONSOLE_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail="Слишком часто — подождите немного между командами")

    body = await request.json()
    command = (body.get("command") or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="Пустая команда")
    if len(command) > _CONSOLE_COMMAND_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"Слишком длинная команда (максимум {_CONSOLE_COMMAND_MAX_LEN} символов)")

    from routes.admin import log_admin_action
    import httpx
    url = f"{panel_url.rstrip('/')}/api/client/servers/{server_uuid}/command"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json={"command": command})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        # Команду не логируем как выполненную, но само НАМЕРЕНИЕ её отправить
        # (кто, что, на какой сервер) — обязательно, даже если Pterodactyl
        # не принял запрос: это тоже часть аудита действий с консолью.
        log_admin_action(db, admin.id, "console_command_failed", {"server": server_key, "command": command, "error": str(e)})
        raise HTTPException(status_code=502, detail=f"Ошибка обращения к Pterodactyl: {e}")

    _console_cooldowns[cooldown_key] = now
    log_admin_action(db, admin.id, "console_command", {"server": server_key, "command": command})
    return {"status": "ok"}


@panel_router.get("/api/v1/admin/moderation/console/{server}/history")
async def api_console_history(server: str, request: Request, db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    """Последние отправленные на этот сервер команды — журнал того, что
    реально вводилось (не живой вывод консоли, см. докстринг раздела выше)."""
    admin = get_current_admin(request, db)
    _console_target(server, admin)  # проверка права, значения панели не нужны
    server_key = server.strip().upper()

    from sqlalchemy.orm import joinedload
    from models import AdminActionLog
    rows = (
        db.query(AdminActionLog)
        .filter(AdminActionLog.action.in_(["console_command", "console_command_failed"]))
        .order_by(AdminActionLog.created_at.desc())
        .limit(500)  # действия разных серверов вперемешку — фильтруем ниже по details
        .all()
    )
    admin_ids = {r.admin_id for r in rows if r.admin_id}
    admins = {a.id: a.discord_username for a in db.query(AdminUser).filter(AdminUser.id.in_(admin_ids)).all()} if admin_ids else {}

    out = []
    for r in rows:
        details = r.details or {}
        if details.get("server") != server_key:
            continue
        out.append({
            "id": r.id,
            "admin_name": admins.get(r.admin_id, f"#{r.admin_id}"),
            "command": details.get("command"),
            "failed": r.action == "console_command_failed",
            "error": details.get("error"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
        if len(out) >= limit:
            break
    return {"history": out}
