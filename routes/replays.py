"""
Round replays routes:
- API: HTTP upload of .barodemo / .barodemo.zip from game servers (API key).
- Admin panel: list, player page, download, delete, settings.

The download endpoint streams the COMPRESSED archive directly to the browser,
which unpacks and parses it client-side (minimal server load).
"""

import os
import secrets
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import (
    APIRouter, Depends, HTTPException, Request, Query, Form, UploadFile, File,
)
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session, aliased, joinedload

from database import get_db
from security import verify_external_api_key_dependency, check_permission
from config import get_settings
from models import AdminUser, AdminSession, RoundEvent, RoundReplay
import services_replays
from services_replays import ReplayService
from services_replays import (
    build_replay_timeline_overview_rows,
    classify_replay_timeline_lane,
    extract_replay_timeline_item,
)

settings = get_settings()
logger = logging.getLogger(__name__)

# ── API router (game server → NexusData, API key protected) ───────────────────
api_router = APIRouter(
    prefix="/api/v1/replays",
    tags=["replays"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# ── Admin panel router (session protected) ────────────────────────────────────
panel_router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)


def _fmt_size(n: Optional[int]) -> str:
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _normalise_server_name(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def _replay_log_timezone_name(server_name: Optional[str], config=None) -> str:
    """Return the IANA timezone used by one server's text log clock.

    The helper accepts an explicit config object to stay deterministic and
    straightforward to unit-test. Empty per-server overrides intentionally
    inherit the project-wide default.
    """
    config = config or settings
    default_name = (getattr(config, "logs_default_timezone", "") or "UTC").strip()
    requested_server = _normalise_server_name(server_name)
    for index in (1, 2):
        configured_server = _normalise_server_name(
            getattr(config, f"sftp_server_{index}_name", "")
        )
        override = (
            getattr(config, f"sftp_server_{index}_logs_timezone", "") or ""
        ).strip()
        if requested_server and requested_server == configured_server and override:
            return override
    return default_name or "UTC"


def _utc_anchor_to_log_clock(
    value: datetime,
    server_name: Optional[str],
    config=None,
) -> tuple[datetime, str]:
    """Convert a UTC replay anchor to the naive wall clock used in text logs.

    Database replay timestamps are historically naive UTC. Aware timestamps
    are accepted as well and normalised to UTC first. The returned datetime is
    deliberately naive because imported Barotrauma log timestamps are stored
    in that legacy form.
    """
    timezone_name = _replay_log_timezone_name(server_name, config)
    try:
        log_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "replay.logs.invalid_timezone server=%r timezone=%r fallback=UTC",
            server_name,
            timezone_name,
        )
        timezone_name = "UTC"
        log_timezone = ZoneInfo("UTC")

    utc_value = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return utc_value.astimezone(log_timezone).replace(tzinfo=None), timezone_name


templates.env.globals.update(fmt_size=_fmt_size)


def _get_admin(request: Request, db: Session) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


async def _ensure_csrf(request: Request, db: Session):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    session_csrf = request.session.get("csrf_token")
    if csrf_token and session_csrf and secrets.compare_digest(str(csrf_token), str(session_csrf)):
        return
    token = request.cookies.get("admin_token")
    if token:
        now = datetime.utcnow()
        s = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if s and (s.expires_at is None or s.expires_at > now):
            return
    raise HTTPException(status_code=403, detail="Invalid CSRF token")


# ══════════════════════════════════════════════════════════════════════════════
# API: HTTP upload from game server
# ══════════════════════════════════════════════════════════════════════════════

@api_router.post("/upload")
async def upload_replay(
    request: Request,
    file: Optional[UploadFile] = File(None, description=".barodemo или .barodemo.zip"),
    server_name: Optional[str] = Query(None, description="Имя сервера-отправителя"),
    notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Upload a round replay file.

    Accepts both:
    - multipart/form-data with field "file"
    - raw binary body (application/octet-stream) for simpler game server clients

    Streams to disk in chunks — does NOT load the whole file into RAM.
    """
    svc = ReplayService(db)

    # Determine upload source: multipart file or raw body
    content_type = request.headers.get("content-type", "")

    if file and file.filename:
        # Standard multipart upload
        logger.info(
            "Replay upload (multipart): filename=%s, content_type=%s, server=%s",
            file.filename, file.content_type, server_name,
        )
        upload_source = file
    elif "multipart" not in content_type:
        # Raw binary body (application/octet-stream or similar)
        logger.info(
            "Replay upload (raw body): content_type=%s, server=%s, content_length=%s",
            content_type, server_name, request.headers.get("content-length", "?"),
        )
        # Wrap raw body as a file-like object
        from io import BytesIO
        body = await request.body()
        if not body:
            return JSONResponse(status_code=400, content={"success": False, "error": "Пустое тело запроса"})

        class _FakeUpload:
            def __init__(self, data, fname):
                self._data = data
                self._pos = 0
                self.filename = fname
                self.content_type = content_type

            async def read(self, size=-1):
                if size < 0:
                    chunk = self._data[self._pos:]
                    self._pos = len(self._data)
                else:
                    chunk = self._data[self._pos:self._pos + size]
                    self._pos += len(chunk)
                return chunk

        # Try to get filename from Content-Disposition header or query
        disp = request.headers.get("content-disposition", "")
        fname = "upload.barodemo"
        if 'filename="' in disp:
            fname = disp.split('filename="')[1].split('"')[0]
        elif 'filename=' in disp:
            fname = disp.split('filename=')[1].split(';')[0].strip()

        upload_source = _FakeUpload(body, fname)
    else:
        # Multipart but no file field — log and reject
        logger.warning(
            "Replay upload: multipart request but no 'file' field. Headers: %s",
            dict(request.headers),
        )
        return JSONResponse(status_code=400, content={"success": False, "error": "Поле 'file' не найдено в multipart запросе"})

    try:
        replay = await svc.save_upload_stream(upload_source, server_name=server_name, notes=notes)
    except HTTPException as he:
        db.rollback()
        logger.warning("Replay upload rejected: %s (filename=%s)", he.detail, getattr(upload_source, "filename", None))
        return JSONResponse(status_code=he.status_code, content={"success": False, "error": str(he.detail)})
    except OSError as oe:
        logger.exception("Replay upload OS error (disk full / permissions?): %s", oe)
        db.rollback()
        return JSONResponse(status_code=507, content={"success": False, "error": f"Ошибка диска: {oe}"})
    except Exception as e:
        logger.exception("Replay upload failed")
        db.rollback()
        return JSONResponse(status_code=500, content={"success": False, "error": f"Ошибка сохранения реплея: {e}"})

    return {
        "success": True,
        "status": "ok",
        "replay_id": replay.id,
        "stored_filename": replay.stored_filename,
        "file_size": replay.file_size,
        "has_voice": bool(replay.has_voice),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Admin panel: list page
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.get("/adminold/replays", response_class=HTMLResponse)
async def replays_list(
    request: Request,
    page: int = Query(1, ge=1),
    server: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = ReplayService(db)
    page_size = 30
    rows, total = svc.get_all(limit=page_size, offset=(page - 1) * page_size, server_name=server)
    total_pages = (total + page_size - 1) // page_size if total else 1

    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)

    retention_days = services_replays.get_retention_days(db)
    upload_dir = services_replays.get_upload_dir(db)

    return templates.TemplateResponse("admin/replays.html", {
        "request": request,
        "admin": admin,
        "replays": rows,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "selected_server": server,
        "retention_days": retention_days,
        "upload_dir": upload_dir,
        "flashes": flashes,
    })


@panel_router.get("/adminold/replays/{replay_id}/view", response_class=HTMLResponse)
async def replay_player_page(request: Request, replay_id: int, db: Session = Depends(get_db)):
    """Web player page for a single replay."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    replay = svc.get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")

    # recorded_at — наивный UTC (services_replays.py парсит его из заголовка
    # самой демки через utcfromtimestamp) — .timestamp() трактовал бы его как
    # локальное время ЭТОГО процесса, что на сервере с не-UTC TZ сдвинуло бы
    # часы в плеере. epoch считаем вручную, без допущений о локальной зоне.
    recorded_at_ms = None
    if replay.recorded_at:
        recorded_at_ms = int((replay.recorded_at - datetime(1970, 1, 1)).total_seconds() * 1000)

    return templates.TemplateResponse("admin/replay_player.html", {
        "request": request,
        "admin": admin,
        "replay": replay,
        "recorded_at_ms": recorded_at_ms,
    })


# ══════════════════════════════════════════════════════════════════════════════
# JSON API (for the React SPA)
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_replay(r) -> dict:
    return {
        "id": r.id, "server_name": r.server_name, "submarine_name": r.submarine_name,
        "level_name": r.level_name, "filename": r.filename, "file_size": r.file_size,
        "duration_ms": r.duration_ms, "has_voice": bool(r.has_voice),
        "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        "notes": r.notes,
    }


def _resolve_round_players(db: Session, players: list) -> list:
    """Overlay forced_display_name onto the raw per-round player snapshot
    (list of {steamid, player_name} dicts as reported live by the game server
    at round start/end — see RoundStartIn/RoundEndIn) so admins see the
    assigned nickname instead of the raw one. The original name is kept under
    "real_name" so the UI can still show it (e.g. as a tooltip).

    Also attaches player_id — the round snapshot only carries steamid, but the
    UI links every name straight to that player's profile (/players/{id}), and
    resolving the id here reuses the lookup this function already does."""
    steamids = [p.get("steamid") for p in players if isinstance(p, dict) and p.get("steamid")]
    forced_map = {}
    id_map = {}
    if steamids:
        from models import PlayerData
        rows = (
            db.query(PlayerData.player_steamid, PlayerData.forced_display_name, PlayerData.player_id)
            .filter(PlayerData.player_steamid.in_(steamids))
            .all()
        )
        forced_map = {sid: name for sid, name, _ in rows if name}
        id_map = {sid: pid for sid, _, pid in rows}

    resolved = []
    for p in players:
        if not isinstance(p, dict):
            resolved.append(p)
            continue
        forced = forced_map.get(p.get("steamid"))
        entry = dict(p)
        if forced:
            entry["real_name"] = p.get("player_name")
            entry["player_name"] = forced
        entry["player_id"] = id_map.get(p.get("steamid"))
        resolved.append(entry)
    return resolved


def _merge_round_players(db: Session, start: Optional[RoundEvent], end_event: RoundEvent) -> list:
    """Все, кто участвовал в раунде, а не только дожившие до его конца.

    Раньше брался ОДИН из двух списков (стартовый, если он есть), поэтому
    вышедшие в середине либо пропадали из отчёта (если брали конечный), либо
    были неотличимы от досидевших (если брали стартовый). Для разбора жалоб
    важно ровно обратное: видеть всех и понимать, кто ушёл и когда.

    Итог: объединение обоих списков. Тот, кого нет в конечном, помечается
    left=True, а время выхода берётся из его игровой сессии, попавшей в
    границы раунда (player_sessions — тот же источник, что и в аналитике).
    Сессии может не быть (игрок остался онлайн, но вышел из раунда), тогда
    left_at=None и интерфейс покажет просто «вышел».
    """
    start_players = (start.players if start and start.players else None) or []
    end_players = end_event.players or []

    def key(entry) -> Optional[str]:
        return entry.get("steamid") if isinstance(entry, dict) else None

    end_ids = {key(p) for p in end_players if key(p)}

    merged: dict = {}
    order: list = []
    # Конечный список — источник истины по должности: за раунд её могли сменить.
    for entry in list(start_players) + list(end_players):
        sid = key(entry)
        if not sid:
            continue
        if sid not in merged:
            order.append(sid)
            merged[sid] = dict(entry)
        else:
            merged[sid].update({k: v for k, v in entry.items() if v is not None})

    missing = [sid for sid in order if sid not in end_ids]
    left_at = _resolve_leave_times(db, missing, start, end_event) if missing else {}

    result = []
    for sid in order:
        entry = merged[sid]
        if sid not in end_ids:
            entry["left"] = True
            moment = left_at.get(sid)
            entry["left_at"] = moment.isoformat() if moment else None
        result.append(entry)
    return result


def _resolve_leave_times(db: Session, steamids: list, start: Optional[RoundEvent],
                         end_event: RoundEvent) -> dict:
    """steamid → время выхода из игры в границах раунда (или None)."""
    from models import PlayerData, PlayerSession

    if not steamids:
        return {}
    started = start.created_at if start else None
    ended = end_event.created_at
    if not started or not ended:
        return {}

    rows = (
        db.query(PlayerData.player_steamid, PlayerSession.logout_time)
        .join(PlayerSession, PlayerSession.player_id == PlayerData.player_id)
        .filter(
            PlayerData.player_steamid.in_(steamids),
            PlayerSession.logout_time.isnot(None),
            PlayerSession.logout_time >= started,
            PlayerSession.logout_time <= ended,
        )
        .all()
    )
    # Выходов за раунд может быть несколько (перезаход) — берём последний:
    # именно после него игрока уже не было в раунде на момент его конца.
    out: dict = {}
    for steamid, logout_time in rows:
        if steamid not in out or logout_time > out[steamid]:
            out[steamid] = logout_time
    return out


def _serialize_round(db: Session, end_event: RoundEvent, replay=None) -> dict:
    start = end_event.paired_start
    players = _merge_round_players(db, start, end_event)
    players = _resolve_round_players(db, players)
    return {
        "id": end_event.id,
        "server_name": end_event.server_name,
        "submarine_name": start.submarine_name if start else None,
        "mission": start.mission if start else None,
        "location": start.location if start else None,
        "started_at": start.created_at.isoformat() if start else None,
        "ended_at": end_event.created_at.isoformat() if end_event.created_at else None,
        "mission_completed": bool(end_event.mission_completed) if end_event.mission_completed is not None else None,
        "reached_station": bool(end_event.reached_station) if end_event.reached_station is not None else None,
        "players": players,
        "player_count": len(players),
        "player_left_count": sum(1 for p in players if isinstance(p, dict) and p.get("left")),
        "replay": _serialize_replay(replay) if replay else None,
    }


def _find_matching_replay(db: Session, end_event: RoundEvent, start: Optional[RoundEvent]) -> Optional[RoundReplay]:
    """Best-effort replay<->round match. A manual link (set via
    /rounds/{id}/link-replay, the escape hatch for whatever this heuristic
    can't resolve) always wins. Otherwise: matched by RoundReplay.uploaded_at
    (THIS server's own clock at the moment the file arrived) falling shortly
    after the round's end — NOT by RoundReplay.recorded_at, which is parsed
    from the demo file's own self-reported header (services_replays.py's
    _parse_demo_header): that field can be None outright if header parsing
    fails, and even when present it depends on the game server's own
    clock/timezone being correct, which is exactly what was silently
    breaking almost every match (the demo's declared recording-start time
    landing hours away from this server's round-end timestamp). Comparing
    two timestamps written by the SAME server avoids that class of bug
    entirely. The window's wide upper bound accounts for demos that don't
    finish uploading immediately when the round ends."""
    manual = (
        db.query(RoundReplay)
        .filter(RoundReplay.manual_round_event_id == end_event.id)
        .first()
    )
    if manual:
        return manual

    if not end_event.created_at:
        return None
    # A couple of minutes of slack before round-end covers clock skew/a
    # demo upload that lands just ahead of the end-event webhook; up to an
    # hour after covers a slow/delayed upload.
    window_start = end_event.created_at - timedelta(minutes=2)
    window_end = end_event.created_at + timedelta(minutes=60)

    replay = (
        db.query(RoundReplay)
        .filter(
            RoundReplay.server_name == end_event.server_name,
            RoundReplay.uploaded_at >= window_start,
            RoundReplay.uploaded_at <= window_end,
        )
        .order_by(RoundReplay.uploaded_at.asc())
        .first()
    )
    if replay:
        return replay

    return (
        db.query(RoundReplay)
        .filter(
            RoundReplay.uploaded_at >= window_start,
            RoundReplay.uploaded_at <= window_end,
        )
        .order_by(RoundReplay.uploaded_at.asc())
        .first()
    )


def _is_captain(role) -> bool:
    """Та же трактовка, что и в интерфейсе: должность считается капитанской,
    если в её идентификаторе есть «captain»."""
    return isinstance(role, str) and "captain" in role.lower()


def _like_any(column, term: str):
    """Регистронезависимый LIKE, не полагающийся на collation колонки.

    Простой `lower(col) LIKE lower(:term)` работает для латиницы, но для
    кириллицы зависит от того, какой collation стоит у конкретной колонки
    (у JSON, приведённого к CHAR, он вообще берётся из настроек соединения).
    Поэтому подстрока ищется сразу в нескольких вариантах регистра: это
    дешевле, чем гадать про collation, и одинаково ведёт себя везде. Точное
    совпадение всё равно перепроверяется в Python."""
    variants = {term, term.lower(), term.upper(), term.capitalize(), term.title()}
    return or_(*[column.like(f"%{v}%") for v in variants if v])


def _steamids_by_name(db: Session, term: str) -> set:
    """steamid'ы игроков, чьё имя (последнее известное или принудительное)
    похоже на term. Нужно, потому что в снимке раунда лежит ник НА МОМЕНТ
    РАУНДА: искать по нынешнему имени иначе бы не получалось."""
    from models import PlayerData
    rows = (
        db.query(PlayerData.player_steamid)
        .filter(
            or_(
                _like_any(PlayerData.player_last_time_name, term),
                _like_any(PlayerData.forced_display_name, term),
                PlayerData.player_steamid == term,
            )
        )
        .limit(500)
        .all()
    )
    return {r[0] for r in rows if r[0]}


def _player_matches(entry, term: str, extra_steamids: set) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("steamid") and entry["steamid"] in extra_steamids:
        return True
    for key in ("player_name", "real_name", "steamid"):
        value = entry.get(key)
        if isinstance(value, str) and term in value.lower():
            return True
    return False


def _round_players_raw(end_event: RoundEvent) -> list:
    """Сырое объединение стартового и конечного списков — для фильтрации.
    Полное слияние (_merge_round_players) ходит в базу за временем выхода и
    для отбора кандидатов избыточно."""
    start = end_event.paired_start
    players = list((start.players if start and start.players else None) or [])
    players += list(end_event.players or [])
    return players


# Потолок на выборку при поиске по игрокам: точное совпадение приходится
# проверять в Python (список игроков лежит в JSON-колонке), поэтому SQL сначала
# грубо сужает набор по подстроке, а сюда доезжает уже немного строк.
_PLAYER_FILTER_CANDIDATE_LIMIT = 3000


@panel_router.get("/api/v1/admin/replays/rounds")
async def api_rounds_list(
    request: Request,
    page: int = Query(1, ge=1),
    server: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Общий поиск: корабль, миссия, локация, игрок"),
    player: Optional[str] = Query(None, description="Ник или steamid участника раунда"),
    captain: Optional[str] = Query(None, description="Ник или steamid капитана"),
    submarine: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD, включительно"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD, включительно"),
    result: Optional[str] = Query(None, description="completed | failed | reached | not_reached"),
    db: Session = Depends(get_db),
):
    """Past finished rounds (paired start/end round_events), each with its
    matching demo recording attached when one exists.

    Фильтры по кораблю/дате/итогу отрабатывают в SQL. Поиск по игрокам и
    капитанам — двухступенчатый: SQL сужает набор по подстроке внутри
    JSON-колонки players, а точное совпадение (и, для капитана, проверка
    должности) досчитывается в Python по уже суженной выборке."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    page_size = 20
    start_alias = aliased(RoundEvent)
    query = (
        db.query(RoundEvent)
        .join(start_alias, RoundEvent.paired_start_id == start_alias.id)
        .options(joinedload(RoundEvent.paired_start))
        .filter(RoundEvent.event_type == "end", RoundEvent.paired_start_id.isnot(None))
    )

    if server:
        query = query.filter(RoundEvent.server_name == server)
    if submarine:
        query = query.filter(_like_any(start_alias.submarine_name, submarine.strip()))

    parsed_from = _parse_date(date_from)
    parsed_to = _parse_date(date_to)
    if parsed_from:
        query = query.filter(RoundEvent.created_at >= parsed_from)
    if parsed_to:
        # Верхняя граница включительно: дата без времени означает «весь день».
        query = query.filter(RoundEvent.created_at < parsed_to + timedelta(days=1))

    if result == "completed":
        query = query.filter(RoundEvent.mission_completed == 1)
    elif result == "failed":
        query = query.filter(RoundEvent.mission_completed == 0)
    elif result == "reached":
        query = query.filter(RoundEvent.reached_station == 1)
    elif result == "not_reached":
        query = query.filter(RoundEvent.reached_station == 0)

    # Текстовый поиск. `q` ищет везде сразу, `player`/`captain` — только среди
    # участников раунда. В SQL уходит введённая строка как есть (_like_any сам
    # перебирает регистры), а в питоновскую доводку — приведённая к нижнему.
    text_terms: list[tuple[str, set]] = []
    q_raw = (q or "").strip()
    player_raw = (player or "").strip()
    captain_raw = (captain or "").strip()
    q_term = q_raw.lower()
    player_term = player_raw.lower()
    captain_term = captain_raw.lower()

    # Список игроков лежит в JSON-колонке: приводим её к тексту и ищем
    # подстроку — точное совпадение всё равно перепроверяется в Python ниже.
    players_json = cast(RoundEvent.players, String)
    start_players_json = cast(start_alias.players, String)

    def player_conditions(term: str, ids: set) -> list:
        conditions = [_like_any(players_json, term), _like_any(start_players_json, term)]
        for steamid in list(ids)[:50]:
            conditions.append(players_json.like(f"%{steamid}%"))
            conditions.append(start_players_json.like(f"%{steamid}%"))
        return conditions

    if q_raw:
        ids = _steamids_by_name(db, q_raw)
        conditions = [
            _like_any(start_alias.submarine_name, q_raw),
            _like_any(start_alias.mission, q_raw),
            _like_any(start_alias.location, q_raw),
            _like_any(RoundEvent.server_name, q_raw),
            *player_conditions(q_raw, ids),
        ]
        query = query.filter(or_(*conditions))
        text_terms.append(("q", ids))

    for term, kind in ((player_raw, "player"), (captain_raw, "captain")):
        if not term:
            continue
        ids = _steamids_by_name(db, term)
        query = query.filter(or_(*player_conditions(term, ids)))
        text_terms.append((kind, ids))

    query = query.order_by(RoundEvent.created_at.desc())

    # Точный отбор по участникам делается в Python, поэтому и пагинация в этом
    # случае считается по отфильтрованному списку, а не по SQL COUNT.
    needs_python_filter = bool(player_term or captain_term)
    if needs_python_filter:
        candidates = query.limit(_PLAYER_FILTER_CANDIDATE_LIMIT).all()
        matched = []
        for end_event in candidates:
            entries = _round_players_raw(end_event)
            if player_term:
                ids = next(i for k, i in text_terms if k == "player")
                if not any(_player_matches(e, player_term, ids) for e in entries):
                    continue
            if captain_term:
                ids = next(i for k, i in text_terms if k == "captain")
                if not any(
                    _is_captain(e.get("role")) and _player_matches(e, captain_term, ids)
                    for e in entries if isinstance(e, dict)
                ):
                    continue
            matched.append(end_event)
        total = len(matched)
        rows = matched[(page - 1) * page_size: page * page_size]
        truncated = len(candidates) >= _PLAYER_FILTER_CANDIDATE_LIMIT
    else:
        total = query.count()
        rows = query.offset((page - 1) * page_size).limit(page_size).all()
        truncated = False

    total_pages = (total + page_size - 1) // page_size if total else 1
    rounds = [_serialize_round(db, end_event, _find_matching_replay(db, end_event, end_event.paired_start)) for end_event in rows]

    servers = [
        row[0] for row in
        db.query(RoundEvent.server_name)
        .filter(RoundEvent.event_type == "end", RoundEvent.server_name.isnot(None))
        .distinct().order_by(RoundEvent.server_name).all()
        if row[0]
    ]

    return {
        "rounds": rounds, "total": total, "page": page, "total_pages": total_pages,
        "selected_server": server, "servers": servers,
        # Поиск по игрокам упёрся в потолок выборки — показанный список неполон.
        "truncated": truncated,
    }


def _parse_date(value: Optional[str]):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


@panel_router.get("/api/v1/admin/replays/rounds/{round_id}")
async def api_round_detail(round_id: int, request: Request, db: Session = Depends(get_db)):
    """Single round, untruncated player list — backs the round-detail page."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    end_event = (
        db.query(RoundEvent)
        .options(joinedload(RoundEvent.paired_start))
        .filter(RoundEvent.id == round_id, RoundEvent.event_type == "end")
        .first()
    )
    if not end_event:
        raise HTTPException(status_code=404, detail="Раунд не найден")

    replay = _find_matching_replay(db, end_event, end_event.paired_start)
    data = _serialize_round(db, end_event, replay)
    data["manually_linked"] = bool(replay and replay.manual_round_event_id == end_event.id)
    return data


@panel_router.post("/api/v1/admin/replays/rounds/{round_id}/link-replay")
async def api_link_replay_to_round(round_id: int, request: Request, db: Session = Depends(get_db)):
    """Manually link (or clear, with replay_id=null) a demo recording to a
    round the automatic matcher couldn't resolve."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    end_event = db.query(RoundEvent).filter(RoundEvent.id == round_id, RoundEvent.event_type == "end").first()
    if not end_event:
        raise HTTPException(status_code=404, detail="Раунд не найден")

    body = await request.json()
    replay_id = body.get("replay_id")

    # Clear any existing manual link pointing at this round first — only one
    # replay can be manually linked to a given round at a time.
    db.query(RoundReplay).filter(RoundReplay.manual_round_event_id == round_id).update({"manual_round_event_id": None})

    if replay_id is not None:
        replay = db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if not replay:
            raise HTTPException(status_code=404, detail="Демозапись не найдена")
        replay.manual_round_event_id = round_id

    db.commit()
    return {"status": "ok"}


@panel_router.get("/api/v1/admin/replays")
async def api_replays_list(
    request: Request, page: int = Query(1, ge=1), server: Optional[str] = Query(None), db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    page_size = 30
    rows, total = svc.get_all(limit=page_size, offset=(page - 1) * page_size, server_name=server)
    total_pages = (total + page_size - 1) // page_size if total else 1

    return {
        "replays": [_serialize_replay(r) for r in rows],
        "total": total, "page": page, "total_pages": total_pages, "selected_server": server,
        "retention_days": services_replays.get_retention_days(db),
        "upload_dir": services_replays.get_upload_dir(db),
        "can_delete": check_permission(admin, "replays", "delete"),
        "is_superuser": admin.is_superuser,
    }


@panel_router.get("/api/v1/admin/replays/{replay_id}")
async def api_replay_detail(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    replay = svc.get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")
    return {"replay": _serialize_replay(replay)}


def _round_for_replay(db: Session, replay) -> Optional[RoundEvent]:
    """Событие конца раунда, которому соответствует эта демозапись.

    Ручная привязка (manual_round_event_id) всегда главнее эвристики — она
    для того и заведена. Иначе ищем раунд тем же способом, что и список
    прошедших раундов, только с другой стороны: среди недавних раундов того
    же сервера тот, для которого _find_matching_replay возвращает эту запись.
    """
    if replay.manual_round_event_id:
        return db.query(RoundEvent).filter(RoundEvent.id == replay.manual_round_event_id).first()

    # A replay can finish uploading hours after the round (large voice demos,
    # a temporary network outage, or a manual retry).  The old reverse lookup
    # considered only uploaded_at and then delegated to a strict +60 minute
    # upload window, so a perfectly valid header timestamp still failed to
    # link and the timeline queried the wrong clock.  Prefer a same-server
    # lifecycle start close to the recording start before using upload time.
    if replay.recorded_at:
        start_event = aliased(RoundEvent)
        candidates = (
            db.query(RoundEvent)
            .join(start_event, RoundEvent.paired_start_id == start_event.id)
            .filter(
                RoundEvent.event_type == "end",
                start_event.event_type == "start",
                # Historic recorder builds sometimes wrote server-local wall
                # time into the nominal Unix field. Keep a bounded window so
                # we can also test the configured server UTC offset below.
                start_event.created_at >= replay.recorded_at - timedelta(hours=15),
                start_event.created_at <= replay.recorded_at + timedelta(hours=15),
            )
        )
        if replay.server_name:
            candidates = candidates.filter(RoundEvent.server_name == replay.server_name)
        nearby = candidates.limit(100).all()
        if nearby:
            def start_time(event):
                return event.paired_start.created_at if event.paired_start else event.created_at

            def choose(values, distance):
                plausible = [event for event in values if distance(event) <= 15 * 60]
                if not plausible:
                    return None
                replay_sub = (replay.submarine_name or "").strip().casefold()
                return min(
                    plausible,
                    key=lambda event: (
                        0 if replay_sub and (event.paired_start.submarine_name or "").strip().casefold() == replay_sub else 1,
                        distance(event),
                    ),
                )

            direct = choose(nearby, lambda event: abs((start_time(event) - replay.recorded_at).total_seconds()))
            if direct:
                return direct

            # Fallback for the legacy local-clock-as-UTC header. A direct
            # match always wins, preventing a different round exactly one
            # timezone offset away from stealing a valid modern replay.
            try:
                log_zone = ZoneInfo(_replay_log_timezone_name(replay.server_name))
                aware_recorded = replay.recorded_at.replace(tzinfo=timezone.utc)
                server_offset = aware_recorded.astimezone(log_zone).utcoffset() or timedelta(0)
            except (ZoneInfoNotFoundError, ValueError):
                server_offset = timedelta(0)
            shifted = choose(
                nearby,
                lambda event: abs((start_time(event) + server_offset - replay.recorded_at).total_seconds()),
            )
            if shifted:
                return shifted

    q = db.query(RoundEvent).filter(RoundEvent.event_type == "end")
    if replay.server_name:
        q = q.filter(RoundEvent.server_name == replay.server_name)
    anchor = replay.uploaded_at or replay.recorded_at
    if anchor:
        q = q.filter(
            RoundEvent.created_at >= anchor - timedelta(hours=12),
            RoundEvent.created_at <= anchor + timedelta(hours=12),
        )
    for end_event in q.order_by(RoundEvent.created_at.desc()).limit(50).all():
        found = _find_matching_replay(db, end_event, end_event.paired_start)
        if found and found.id == replay.id:
            return end_event
    return None


def _timeline_unavailable_payload(
    reason: str,
    *,
    round_id: Optional[int] = None,
    server_name: Optional[str] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    duration_ms: Optional[int] = None,
    duration_source: Optional[str] = None,
    anchor_source: Optional[str] = None,
    log_timezone: Optional[str] = None,
    window_from_ms: int = 0,
    window_to_ms: Optional[int] = None,
) -> dict:
    """Return one stable contract when replay logs cannot be queried.

    The demo remains playable, but clients can distinguish an unavailable
    index from a valid timeline containing zero events.
    """
    return {
        "status": "unavailable",
        "events": [],
        "overview": [],
        "total": 0,
        "total_is_capped": False,
        "has_more": False,
        "loaded": 0,
        "round_id": round_id,
        "server_name": server_name,
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
        "duration_ms": duration_ms,
        "duration_source": duration_source,
        "anchor_source": anchor_source,
        "log_timezone": log_timezone,
        "window_from_ms": window_from_ms,
        "window_to_ms": duration_ms if window_to_ms is None else window_to_ms,
        "players": [],
        "action_types": [],
        "unavailable_reason": reason,
    }


@panel_router.get("/api/v1/admin/replays/{replay_id}/logs")
async def api_replay_logs(
    request: Request,
    replay_id: int,
    player: Optional[str] = Query(None),
    action_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    # Preserve the classic panel's historical default; V2 requests its own
    # explicit window limits (100 overview / 10,000 exact rows).
    limit: int = Query(2000, ge=100, le=25000),
    from_ms: int = Query(0, ge=0, le=12 * 60 * 60 * 1000),
    to_ms: Optional[int] = Query(None, ge=1, le=12 * 60 * 60 * 1000),
    overview_only: bool = Query(
        False,
        description="Вернуть агрегированные маркеры всего раунда без строк логов.",
    ),
    duration_ms: Optional[int] = Query(
        None,
        ge=1,
        le=12 * 60 * 60 * 1000,
        description="Точная длительность, полученная браузером после разбора демо.",
    ),
    player_scope: str = Query(
        "related",
        pattern="^(actor|related)$",
        description="actor — только действия игрока; related — также упоминания в тексте.",
    ),
    db: Session = Depends(get_db),
):
    """Логи сервера за время раунда этой демозаписи, с привязкой к таймингу.

    Каждому событию считается offset_ms — сдвиг от начала раунда, по нему
    просмотрщик подсвечивает строки синхронно с воспроизведением. Границы
    берутся по событиям раунда, а если начала нет — по времени записи демо и
    её длительности.

    Фильтры повторяют вкладку «Логи», кроме диапазона времени: он здесь задан
    самим раундом, иначе привязка к таймингу теряет смысл.
    """
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # Логи — отдельная по чувствительности вещь (там переписка и консоль),
    # поэтому мало права на просмотр демо: нужно и право на логи.
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403, detail="Нет доступа к логам сервера")

    replay = ReplayService(db).get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")
    if not replay.server_name:
        resolved_duration = duration_ms or replay.duration_ms
        return JSONResponse(_timeline_unavailable_payload(
            "У записи не указан сервер — серверные логи нельзя привязать безопасно.",
            duration_ms=resolved_duration,
            duration_source="request" if duration_ms else ("replay" if replay.duration_ms else None),
            window_to_ms=resolved_duration,
        ))

    end_event = _round_for_replay(db, replay)
    start_event = end_event.paired_start if end_event else None

    # The demo header is the most precise anchor when the game server wrote a
    # sane Unix timestamp. Some historic files encoded a local/server clock as
    # if it were UTC and landed hours away from the matching ND lifecycle
    # event. In that case prefer the paired round start: a few seconds of
    # webhook latency are vastly safer than an empty, hours-shifted timeline.
    recorded_anchor = replay.recorded_at
    lifecycle_anchor = start_event.created_at if start_event else None
    anchor_source = "recording" if recorded_anchor else ("round" if lifecycle_anchor else None)
    if recorded_anchor and lifecycle_anchor:
        anchor_skew = abs((recorded_anchor - lifecycle_anchor).total_seconds())
        if anchor_skew > 10 * 60:
            utc_anchor = lifecycle_anchor
            anchor_source = "round_clock_fallback"
        else:
            utc_anchor = recorded_anchor
    else:
        utc_anchor = recorded_anchor or lifecycle_anchor

    # RoundReplay/RoundEvent timestamps are stored as naive UTC, while the
    # imported Barotrauma text logs are naive server-local wall-clock values.
    # Resolve that clock from configuration instead of the NexusData process
    # timezone: the container and game server do not have to share a TZ.
    started_at = None
    log_timezone = _replay_log_timezone_name(replay.server_name)
    if utc_anchor:
        started_at, log_timezone = _utc_anchor_to_log_clock(
            utc_anchor,
            replay.server_name,
        )

    # duration_ms historically stayed NULL because the upload service parses
    # only the header.  Falling back to five minutes silently chopped almost
    # every round.  The V2 player sends the exact parsed duration; old clients
    # still get a sensible value from the paired round or upload timestamp.
    effective_duration_ms: Optional[int] = duration_ms or replay.duration_ms
    duration_source = "request" if duration_ms else ("replay" if replay.duration_ms else None)
    if effective_duration_ms is None and start_event and end_event:
        estimated = int((end_event.created_at - start_event.created_at).total_seconds() * 1000)
        if 1_000 <= estimated <= 12 * 60 * 60 * 1000:
            effective_duration_ms = estimated
            duration_source = "round"
    if effective_duration_ms is None and utc_anchor and replay.uploaded_at:
        estimated = int((replay.uploaded_at - utc_anchor).total_seconds() * 1000)
        if 1_000 <= estimated <= 12 * 60 * 60 * 1000:
            effective_duration_ms = estimated
            duration_source = "upload"
    if effective_duration_ms is None:
        return JSONResponse(_timeline_unavailable_payload(
            "Не удалось определить длительность этой записи.",
            round_id=end_event.id if end_event else None,
            server_name=replay.server_name,
            started_at=started_at,
            duration_source=duration_source,
            anchor_source=anchor_source,
            log_timezone=log_timezone,
        ))
    ended_at = started_at + timedelta(milliseconds=effective_duration_ms) if started_at else None

    if not started_at or not ended_at:
        return JSONResponse(_timeline_unavailable_payload(
            "Не удалось определить раунд этой записи — привяжите её к раунду вручную.",
            round_id=end_event.id if end_event else None,
            server_name=replay.server_name,
            duration_ms=effective_duration_ms,
            duration_source=duration_source,
            anchor_source=anchor_source,
            log_timezone=log_timezone,
        ))

    range_start_ms = min(from_ms, effective_duration_ms)
    range_end_ms = min(to_ms if to_ms is not None else effective_duration_ms, effective_duration_ms)
    if range_end_ms <= range_start_ms and not overview_only:
        raise HTTPException(status_code=422, detail="to_ms должен быть больше from_ms")
    # Text server logs store whole seconds. Pad the SQL bounds by one second
    # and mark the result precision explicitly instead of presenting `.000`
    # as millisecond-accurate data.
    query_started_at = started_at + timedelta(milliseconds=range_start_ms - 1_000)
    query_ended_at = started_at + timedelta(milliseconds=range_end_ms + 1_000)

    from services_logs import (
        LogDataUnavailable,
        LogSearchTooSlow,
        get_log_stats,
        get_log_timeline_overview,
        search_logs,
    )

    def load_pages():
        """Read and enrich one exact visible window in the worker thread."""
        loaded = []
        cursor = None
        first_total = None
        first_total_is_capped = False
        has_more = False
        while len(loaded) < limit:
            page_size = min(5000, limit - len(loaded))
            result = search_logs(
                server_name=replay.server_name or None,
                player_name=player or None,
                action_type=action_type or None,
                search_text=search or None,
                time_from=query_started_at,
                time_to=query_ended_at,
                category=category or None,
                limit=page_size,
                offset=0,
                cursor=cursor,
                # The timeline only needs the loaded rows and has_more. A
                # full COUNT over a dense round can be slower than all keyset
                # pages combined and used to surface as a 503 in production.
                count_total=False,
                player_scope=player_scope,
                raise_on_unavailable=True,
            )
            if cursor is None:
                first_total = result.total
                first_total_is_capped = result.total_is_capped
            loaded.extend(result.events)
            has_more = result.has_more
            cursor = result.next_cursor
            if not has_more or not cursor or not result.events:
                break
        rows = []
        player_counts: Counter[str] = Counter()
        action_counts: Counter[str] = Counter()
        allowed_start_ms = max(0, range_start_ms - 1_000)
        allowed_end_ms = min(effective_duration_ms, range_end_ms + 1_000)
        for event in sorted(loaded, key=lambda value: (value.timestamp, value.row_id)):
            raw_offset_ms = int((event.timestamp - started_at).total_seconds() * 1000)
            if raw_offset_ms < allowed_start_ms or raw_offset_ms > allowed_end_ms:
                continue
            if event.player_name:
                player_counts[event.player_name] += 1
            action_counts[event.action_type] += 1
            rows.append({
                "event_id": event.row_id,
                "timestamp": event.timestamp.isoformat(),
                "offset_ms": max(0, min(effective_duration_ms, raw_offset_ms)),
                "player_name": event.player_name,
                "action_type": event.action_type,
                "details": event.details,
                "source_file": event.source_file,
                "lane": classify_replay_timeline_lane(event.action_type),
                "item": extract_replay_timeline_item(event.action_type, event.details),
                "precision_ms": 1_000,
            })
        return rows, first_total, first_total_is_capped, has_more, player_counts, action_counts

    def load_overview():
        # About 600 columns is enough to preserve the shape of a full round
        # while remaining much smaller than its exact event stream.
        bucket_ms = max(1_000, (effective_duration_ms + 599) // 600)
        raw = get_log_timeline_overview(
            server_name=replay.server_name or "",
            time_from=started_at,
            time_to=ended_at,
            bucket_ms=bucket_ms,
            player_name=player if player_scope == "actor" else None,
        )
        overview_rows, action_counts = build_replay_timeline_overview_rows(
            raw.buckets,
            effective_duration_ms,
        )
        for row in overview_rows:
            row["precision_ms"] = bucket_ms
        return overview_rows, raw.players, action_counts

    try:
        if overview_only:
            overview_rows, overview_players, action_counts = await run_in_threadpool(load_overview)
            rows = []
            total = sum(item["aggregate_count"] for item in overview_rows)
            total_is_capped = False
            has_more = False
            player_counts = Counter({item["name"]: item["event_count"] for item in overview_players})
        else:
            rows, total, total_is_capped, has_more, player_counts, action_counts = await run_in_threadpool(load_pages)
            overview_rows = []
    except LogSearchTooSlow:
        unavailable_reason = "Слишком тяжёлый запрос к логам — уточните фильтры."
    except LogDataUnavailable:
        logger.exception(
            "Replay timeline log index is unavailable (replay_id=%s, server=%s)",
            replay.id,
            replay.server_name,
        )
        unavailable_reason = "Серверные логи временно недоступны. Демозапись можно просматривать без них."
    else:
        unavailable_reason = None

    # A normal Barotrauma round cannot produce literally zero server-log
    # lines. Treat that result as an incomplete/misaligned index instead of
    # presenting it as evidence that no actions happened. This is especially
    # common during the short gap between demo upload and SFTP log rotation.
    if overview_only and not player and not unavailable_reason and total == 0:
        stats = await run_in_threadpool(get_log_stats)
        server_stats = stats.get(replay.server_name or "", {})
        newest_raw = server_stats.get("newest_event")
        oldest_raw = server_stats.get("oldest_event")
        try:
            newest_event = datetime.fromisoformat(newest_raw) if newest_raw else None
            oldest_event = datetime.fromisoformat(oldest_raw) if oldest_raw else None
        except (TypeError, ValueError):
            newest_event = oldest_event = None
        if newest_event and newest_event < ended_at:
            unavailable_reason = "Лог этого раунда ещё не поступил в индекс. Повторная проверка выполняется автоматически."
        elif oldest_event and started_at < oldest_event:
            unavailable_reason = "Лог этого раунда уже находится вне срока хранения индекса."
        elif not end_event:
            unavailable_reason = "Демозапись не удалось связать с раундом, поэтому время серверных логов нельзя подтвердить."
        else:
            unavailable_reason = "В интервале раунда серверные записи не найдены. Проверьте привязку демозаписи к раунду."
        logger.warning(
            "replay.timeline.empty replay_id=%s server=%s round_id=%s anchor=%s "
            "started_at=%s ended_at=%s oldest_log=%s newest_log=%s",
            replay.id,
            replay.server_name,
            end_event.id if end_event else None,
            anchor_source,
            started_at,
            ended_at,
            oldest_raw,
            newest_raw,
        )

    if unavailable_reason:
        return JSONResponse(_timeline_unavailable_payload(
            unavailable_reason,
            round_id=end_event.id if end_event else None,
            server_name=replay.server_name,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=effective_duration_ms,
            duration_source=duration_source,
            anchor_source=anchor_source,
            log_timezone=log_timezone,
            window_from_ms=range_start_ms,
            window_to_ms=range_end_ms,
        ))

    return JSONResponse({
        "status": "ok",
        "events": rows,
        "overview": overview_rows,
        # services_logs intentionally caps an expensive COUNT. Never report a
        # total smaller than the keyset pages we have actually loaded.
        "total": max(total or 0, len(rows)),
        "total_is_capped": bool(total_is_capped or has_more),
        "has_more": has_more,
        "loaded": len(rows),
        "round_id": end_event.id if end_event else None,
        "server_name": replay.server_name,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_ms": effective_duration_ms,
        "duration_source": duration_source,
        "anchor_source": anchor_source,
        "log_timezone": log_timezone,
        "window_from_ms": range_start_ms,
        "window_to_ms": range_end_ms,
        "players": [
            {"name": name, "event_count": count}
            for name, count in sorted(player_counts.items(), key=lambda pair: (-pair[1], pair[0].casefold()))
        ],
        "action_types": [
            {"name": name, "event_count": count}
            for name, count in sorted(action_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "unavailable_reason": None,
    })


@panel_router.delete("/api/v1/admin/replays/{replay_id}")
async def api_replay_delete(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    if not svc.delete(replay_id):
        raise HTTPException(status_code=404, detail="Реплей не найден")

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_delete", {"replay_id": replay_id})
    return {"status": "ok"}


@panel_router.post("/api/v1/admin/replays/upload")
async def api_replay_manual_upload(
    request: Request, file: UploadFile = File(...), server_name: Optional[str] = Form(None),
    notes: Optional[str] = Form(None), db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может загружать реплеи вручную")

    svc = ReplayService(db)
    replay = await svc.save_upload_stream(file, server_name=(server_name or None), notes=(notes or "Загружен вручную через сайт"))
    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_manual_upload", {"replay_id": replay.id})
    return {"status": "ok", "replay": _serialize_replay(replay)}


@panel_router.post("/api/v1/admin/replays/settings")
async def api_replay_settings(
    request: Request, db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может менять настройки")

    body = await request.json()
    retention_days = max(1, int(body.get("retention_days", 1)))
    upload_dir = (body.get("upload_dir") or "").strip()

    services_replays.set_setting(db, services_replays.SETTING_RETENTION_DAYS, str(retention_days))
    if upload_dir:
        services_replays.set_setting(db, services_replays.SETTING_UPLOAD_DIR, upload_dir)
        os.makedirs(upload_dir, exist_ok=True)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_settings_update", {"retention_days": retention_days, "upload_dir": upload_dir})
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# Download (streams compressed archive to browser)
# ══════════════════════════════════════════════════════════════════════════════

def _replay_media_type(stored_filename: str) -> str:
    if stored_filename.endswith(".zip"):
        return "application/zip"
    if stored_filename.endswith(".gz"):
        return "application/gzip"
    return "application/octet-stream"


@panel_router.get("/api/v1/admin/replays/{replay_id}/download")
async def api_replay_download(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    replay = svc.get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")

    path = svc.get_file_path(replay)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    # Stream the (possibly compressed) archive directly. The browser unpacks it client-side.
    return FileResponse(
        path=path,
        filename=replay.stored_filename,
        media_type=_replay_media_type(replay.stored_filename),
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@panel_router.get("/adminold/replays/{replay_id}/download")
async def replay_download(request: Request, replay_id: int, db: Session = Depends(get_db)):
    return await api_replay_download(request, replay_id, db)


# ══════════════════════════════════════════════════════════════════════════════
# Delete
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.post("/adminold/replays/{replay_id}/delete")
async def replay_delete(request: Request, replay_id: int, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await _ensure_csrf(request, db)

    svc = ReplayService(db)
    if not svc.delete(replay_id):
        raise HTTPException(status_code=404, detail="Реплей не найден")

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_delete", {"replay_id": replay_id})

    flashes = request.session.get("_flashes", [])
    flashes.append({"level": "success", "message": "Реплей удалён"})
    request.session["_flashes"] = flashes
    return RedirectResponse(url="/adminold/replays", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# Manual upload via website (superuser only)
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.post("/adminold/replays/upload")
async def replay_manual_upload(
    request: Request,
    file: UploadFile = File(...),
    server_name: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Manually upload a replay file through the website. Superuser only."""
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может загружать реплеи вручную")
    await _ensure_csrf(request, db)

    svc = ReplayService(db)
    try:
        replay = await svc.save_upload_stream(
            file,
            server_name=(server_name or None),
            notes=(notes or "Загружен вручную через сайт"),
        )
        from routes.admin import log_admin_action
        log_admin_action(db, admin.id, "replay_manual_upload", {"replay_id": replay.id})
        flashes = request.session.get("_flashes", [])
        flashes.append({"level": "success", "message": f"Реплей #{replay.id} загружен"})
        request.session["_flashes"] = flashes
    except HTTPException as e:
        flashes = request.session.get("_flashes", [])
        flashes.append({"level": "error", "message": e.detail})
        request.session["_flashes"] = flashes

    return RedirectResponse(url="/adminold/replays", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# Settings (superuser only): retention days + storage dir
# ══════════════════════════════════════════════════════════════════════════════

@panel_router.post("/adminold/replays/settings")
async def replay_settings(
    request: Request,
    retention_days: int = Form(...),
    upload_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только суперпользователь может менять настройки")
    await _ensure_csrf(request, db)

    if retention_days < 1:
        retention_days = 1
    services_replays.set_setting(db, services_replays.SETTING_RETENTION_DAYS, str(retention_days))
    if upload_dir.strip():
        services_replays.set_setting(db, services_replays.SETTING_UPLOAD_DIR, upload_dir.strip())
        os.makedirs(upload_dir.strip(), exist_ok=True)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "replay_settings_update", {
        "retention_days": retention_days, "upload_dir": upload_dir.strip(),
    })

    flashes = request.session.get("_flashes", [])
    flashes.append({"level": "success", "message": "Настройки реплеев сохранены"})
    request.session["_flashes"] = flashes
    return RedirectResponse(url="/adminold/replays", status_code=302)
