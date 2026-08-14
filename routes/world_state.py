"""
WorldStateStreamer ingest + "Обзор мира" admin API — see
WORLD_STATE_API_GUIDE.md for the full wire contract this implements.

Two routers:
- `router` (prefix "/api/v1/world", API-key gated like routes/game_api.py) —
  the two endpoints the client-side plugin actually calls: POST /init once
  per round, POST /snapshot every ~3s while it runs. Per the guide: respond
  2xx fast, treat every snapshot as a full upsert of "current state" (never
  an append-only log), never require idempotency/retries.
- `admin_router` (prefix "/api/v1/admin/world", session-gated) — read-only
  endpoints for the React "Обзор мира" page: list of currently-active
  rounds, static geometry (fetched once per round selection), and live
  dynamic state (polled every few seconds). Geometry and state are
  deliberately separate endpoints so a ~3s poll never re-sends the
  (potentially multi-MB) world outline on every tick.
"""

import gzip
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from database import get_db, get_game_db
from security import verify_external_api_key_dependency, verify_client_api_key_dependency, check_permission
from config import get_settings
from models import AdminUser, AdminSession, WorldRound

settings = get_settings()
logger = logging.getLogger(__name__)

# A round with no new snapshot in this long is considered over — the wire
# protocol has no explicit "round ended" signal (see guide §7.7), so this is
# purely a server-side staleness heuristic (~5 missed 3s intervals).
ACTIVE_THRESHOLD_SECONDS = 15

router = APIRouter(
    prefix="/api/v1/world",
    tags=["world-state"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

admin_router = APIRouter()

# Read-only external "client" integrations — gated by the SEPARATE
# client_api_key (see security.py's verify_client_api_key_dependency),
# never the game-server ingest key (`router` above) or an admin session
# (`admin_router`). Kept under its own /api/v1/client prefix so all three
# access tiers are visually distinct in the route table.
client_router = APIRouter(
    prefix="/api/v1/client",
    tags=["client-api"],
    dependencies=[Depends(verify_client_api_key_dependency)],
)


def _ms_to_dt(ms) -> Optional[datetime]:
    if not ms:
        return None
    try:
        return datetime.utcfromtimestamp(float(ms) / 1000.0)
    except (TypeError, ValueError, OverflowError):
        return None


def _looks_gzip(raw: bytes) -> bool:
    return len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B


async def _read_json_body(request: Request) -> dict:
    """Reads and JSON-decodes the request body, transparently gunzipping it
    first when the plugin's `CompressPayload: true` is on. Per the guide
    (§7), the plugin sends `Content-Encoding: gzip` in that case, but
    FastAPI/Starlette only auto-decompress RESPONSE bodies, never incoming
    request bodies — request.json() on a gzipped body just fails trying to
    UTF-8-decode the raw gzip magic bytes. Sniffs the magic bytes (1f 8b) in
    addition to trusting the header, in case it's ever missing/stripped."""
    raw = await request.body()
    content_encoding = (request.headers.get("content-encoding") or "").lower()
    if "gzip" in content_encoding or _looks_gzip(raw):
        try:
            raw = gzip.decompress(raw)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"Invalid gzip body: {e}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# INGEST — called BY the game server's WorldStateStreamer plugin
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/init")
async def world_init(request: Request, db: Session = Depends(get_game_db)):
    """Static round geometry — sent once at round start (and again if the
    plugin hot-reloads mid-round, which just re-upserts the same round_id)."""
    body = await _read_json_body(request)
    round_id = body.get("round_id")
    if not round_id:
        raise HTTPException(status_code=400, detail="round_id required")

    row = db.query(WorldRound).filter(WorldRound.round_id == round_id).first()
    if not row:
        row = WorldRound(round_id=round_id)
        db.add(row)

    row.server_name = body.get("server_name")
    row.level_seed = body.get("level_seed")
    row.level_name = body.get("level_name")
    row.level_type = body.get("level_type")
    row.level_w = body.get("level_w")
    row.level_h = body.get("level_h")
    row.sub_name = body.get("sub_name")
    row.submarines = body.get("submarines") or []
    row.hulls = body.get("hulls") or []
    row.walls = body.get("walls") or []
    row.doors = body.get("doors") or []
    row.outline_w = body.get("outline_w")
    row.outline_h = body.get("outline_h")
    row.outline = body.get("outline") or []
    row.init_sent_at = _ms_to_dt(body.get("sent_at"))
    # world/init alone doesn't mean anything is "live" for the viewer yet
    # (no dynamic state), but it does mean the round exists — bump this so
    # the round shows up immediately even before the first snapshot lands.
    row.last_update_at = datetime.utcnow()
    db.commit()
    _try_queue_ai_world_briefing(db, row)
    return JSONResponse({"status": "ok"})


@router.post("/snapshot")
async def world_snapshot(request: Request, db: Session = Depends(get_game_db)):
    """Dynamic round state — sent every ~SnapshotIntervalSeconds. Always a
    full upsert of "current state"; the plugin never retries a failed send,
    so this must never assume completeness or ordering guarantees beyond a
    single game server's own sequential delivery."""
    body = await _read_json_body(request)
    round_id = body.get("round_id")
    if not round_id:
        raise HTTPException(status_code=400, detail="round_id required")

    row = db.query(WorldRound).filter(WorldRound.round_id == round_id).first()
    if not row:
        # A snapshot can race ahead of its own world/init on first delivery
        # (see guide §4) — create a bare row so this round isn't lost; init
        # fills in the geometry columns whenever it lands.
        row = WorldRound(round_id=round_id)
        db.add(row)

    seq = body.get("seq")
    if seq is not None and row.last_seq is not None and seq < row.last_seq:
        # Stale/reordered write (shouldn't normally happen within one
        # server's sequential delivery, but costs nothing to guard against).
        return JSONResponse({"status": "ok", "ignored": "stale_seq"})

    row.last_seq = seq
    row.last_t = body.get("t")
    row.last_sent_at = _ms_to_dt(body.get("sent_at"))
    row.last_subs = body.get("subs")
    row.last_hulls_state = body.get("hulls")
    row.last_doors_state = body.get("doors")
    row.last_reactors = body.get("reactors")
    row.last_characters = body.get("characters")
    # null here means IncludeItems:false on the plugin side — "don't update
    # items this tick", NOT "all items vanished". Only overwrite on a real value.
    items = body.get("items")
    if items is not None:
        row.last_items = items
    row.last_update_at = datetime.utcnow()
    db.commit()
    _try_queue_ai_world_briefing(db, row)
    return JSONResponse({"status": "ok"})


def _try_queue_ai_world_briefing(db: Session, row: WorldRound) -> None:
    """Best-effort start trigger when the lifecycle endpoint was not called.

    World telemetry is the reliable signal present on every installation.  AI
    delivery must never make ingest fail, so bridge errors stay in the log and
    the snapshot still receives its normal 200 response.
    """

    try:
        # Local import avoids a module cycle: dispatch imports WorldRound, while
        # this route only needs the bridge after both modules are initialized.
        from routes.dispatch import _queue_ai_world_briefing

        _queue_ai_world_briefing(row, db)
    except Exception:
        logger.exception(
            "AI world briefing trigger failed: round_id=%r server=%r",
            row.round_id,
            row.server_name,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN API — "Обзор мира" page (session-gated, read-only)
# ══════════════════════════════════════════════════════════════════════════════

def _get_admin(request: Request, db: Session) -> AdminUser:
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
                # check_permission's "hybrid" tier (site-wide grants attached
                # to a department access level, e.g. how a non-superuser most
                # likely got "Обзор мира" access) reads this attribute — it
                # was missing here, so that grant path silently never worked
                # for this page even though it works everywhere else (every
                # other router's get_current_admin sets it). Superusers were
                # unaffected since they short-circuit check_permission
                # entirely, which is why this only showed up for regular users.
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require_world_state(request: Request, db: Session) -> AdminUser:
    admin = _get_admin(request, db)
    if not check_permission(admin, "world_state", "view"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return admin


def _is_active(row: WorldRound) -> bool:
    if not row.last_update_at:
        return False
    return (datetime.utcnow() - row.last_update_at).total_seconds() < ACTIVE_THRESHOLD_SECONDS


# ══════════════════════════════════════════════════════════════════════════════
# ITEM CATALOG — site-wide identifier -> {name, has_icon} reference data,
# extracted offline from the Barotrauma content files by
# tools/extract_barotrauma_items.py into static/item_catalog.json (icons
# themselves are just plain files under static/item_icons/, served by the
# existing /static mount — see main.py — no route needed for those).
# Not specific to "Обзор мира" — any admin session can read it, so future
# pages (moderation logs, replays, etc.) can reuse the same catalog without
# needing the world_state permission.
# ══════════════════════════════════════════════════════════════════════════════

_ITEM_CATALOG_PATH = os.path.join("static", "item_catalog.json")
_item_catalog_cache: Optional[dict] = None
_item_catalog_mtime: Optional[float] = None


def _load_item_catalog() -> dict:
    global _item_catalog_cache, _item_catalog_mtime
    try:
        mtime = os.path.getmtime(_ITEM_CATALOG_PATH)
    except OSError:
        return {}
    if _item_catalog_cache is None or mtime != _item_catalog_mtime:
        try:
            with open(_ITEM_CATALOG_PATH, "r", encoding="utf-8") as f:
                _item_catalog_cache = json.load(f)
            _item_catalog_mtime = mtime
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to load static/item_catalog.json")
            return _item_catalog_cache or {}
    return _item_catalog_cache


@admin_router.get("/api/v1/admin/items/catalog")
async def api_item_catalog(request: Request, db: Session = Depends(get_db)):
    """{identifier: {name, has_icon}} for every known item — has_icon tells
    the frontend whether to bother requesting /static/item_icons/{id}.png at
    all (ItemIcon falls back to a placeholder either way, but this lets a
    caller skip the request entirely if it wants)."""
    _get_admin(request, db)
    return JSONResponse(_load_item_catalog())


@admin_router.get("/api/v1/admin/world/rounds")
async def api_list_world_rounds(request: Request, db: Session = Depends(get_db)):
    """Currently-active rounds only — lightweight, drives the round picker."""
    _require_world_state(request, db)
    threshold = datetime.utcnow() - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)
    rows = (
        db.query(WorldRound)
        .filter(WorldRound.last_update_at >= threshold)
        .order_by(WorldRound.last_update_at.desc())
        .all()
    )
    return {
        "rounds": [
            {
                "round_id": r.round_id,
                "server_name": r.server_name,
                "level_name": r.level_name,
                "sub_name": r.sub_name,
                "t": r.last_t,
            }
            for r in rows
        ]
    }


@admin_router.get("/api/v1/admin/world/rounds/{round_id}/geometry")
async def api_get_world_geometry(round_id: str, request: Request, db: Session = Depends(get_db)):
    """Static geometry — fetch once when a round is selected, never on the
    polling interval (this is the potentially-multi-MB part, per the
    guide's own warning about outline[] size)."""
    _require_world_state(request, db)
    r = db.query(WorldRound).filter(WorldRound.round_id == round_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Раунд не найден")
    return {
        "round_id": r.round_id,
        "server_name": r.server_name,
        "level_seed": r.level_seed,
        "level_name": r.level_name,
        "level_type": r.level_type,
        "level_w": r.level_w,
        "level_h": r.level_h,
        "sub_name": r.sub_name,
        "submarines": r.submarines or [],
        "hulls": r.hulls or [],
        "walls": r.walls or [],
        "doors": r.doors or [],
        "outline_w": r.outline_w,
        "outline_h": r.outline_h,
        "outline": r.outline or [],
    }


@admin_router.get("/api/v1/admin/world/rounds/{round_id}/state")
async def api_get_world_state(round_id: str, request: Request, db: Session = Depends(get_db)):
    """Dynamic state — the ~3s poll target. Everything the frontend needs to
    redraw entities/hull-fill/door-openness and translate sub-local geometry
    to world coordinates (via `subs[]`), without re-fetching geometry."""
    _require_world_state(request, db)
    r = db.query(WorldRound).filter(WorldRound.round_id == round_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Раунд не найден")
    return {
        "round_id": r.round_id,
        "seq": r.last_seq,
        "t": r.last_t,
        "subs": r.last_subs or [],
        "hulls_state": r.last_hulls_state or [],
        "doors_state": r.last_doors_state or [],
        "reactors": r.last_reactors or [],
        "characters": r.last_characters or [],
        "items": r.last_items or [],
        "last_update_at": r.last_update_at.isoformat() if r.last_update_at else None,
        "is_active": _is_active(r),
    }


def _serialize_round_full(r: WorldRound) -> dict:
    """Geometry + dynamic state combined — unlike the admin API's split
    endpoints above (kept separate so the ~3s poll never re-sends the
    potentially multi-MB outline), an external client fetching a one-shot
    snapshot wants everything in one response.

    Deliberately WITHOUT `items`: this response already carries submarines
    (`submarines`/`subs` — includes beacons, wrecks, outposts/stations, every
    "Submarine" the level has, not just the player sub), their live state
    (`hulls_state`/`doors_state`/`reactors`) and `characters` — the external
    client only ever consumed those. `items` is by far the biggest part of
    every snapshot (every item in the level, on every poll) and was pure dead
    weight for this integration; dropping it is the actual JSON-size win.
    Kept out of `_serialize_round_dynamic` too (admin API) would be wrong —
    that one backs "Обзор мира"'s inventory popups, which need it."""
    return {
        "round_id": r.round_id,
        "server_name": r.server_name,
        "level_seed": r.level_seed,
        "level_name": r.level_name,
        "level_type": r.level_type,
        "level_w": r.level_w,
        "level_h": r.level_h,
        "sub_name": r.sub_name,
        "submarines": r.submarines or [],
        "hulls": r.hulls or [],
        "walls": r.walls or [],
        "doors": r.doors or [],
        "outline_w": r.outline_w,
        "outline_h": r.outline_h,
        "outline": r.outline or [],
        "seq": r.last_seq,
        "t": r.last_t,
        "subs": r.last_subs or [],
        "hulls_state": r.last_hulls_state or [],
        "doors_state": r.last_doors_state or [],
        "reactors": r.last_reactors or [],
        "characters": r.last_characters or [],
        "last_update_at": r.last_update_at.isoformat() if r.last_update_at else None,
        "is_active": _is_active(r),
    }


def _gzip_json_response(payload: dict) -> Response:
    """Manually gzips the body and sets Content-Encoding unconditionally —
    unlike main.py's GZipMiddleware (which only compresses when the CLIENT
    sends Accept-Encoding: gzip), this guarantees a gzip body regardless of
    what the calling client does, per the "в gzip формате" requirement."""
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Encoding": "gzip", "Content-Length": str(len(body))},
    )


@client_router.get("/world/state")
async def api_client_world_state(
    round_id: Optional[str] = None,
    server_name: Optional[str] = None,
    db: Session = Depends(get_game_db),
):
    """Structured world state for external client integrations (dedicated
    client_api_key, see security.py) — always the freshest row currently in
    the database at request time (every /world/snapshot upserts in place,
    see world_snapshot() above, so this is never a stale/cached read).

    Without `round_id`: every currently-active round (see
    ACTIVE_THRESHOLD_SECONDS). With `round_id`: that one round specifically,
    active or not (404 if it doesn't exist at all).

    Response body is gzip-compressed (Content-Encoding: gzip) regardless of
    the caller's Accept-Encoding header.
    """
    normalized_server_name = (server_name or "").strip() or None

    if round_id:
        query = db.query(WorldRound).filter(WorldRound.round_id == round_id)
        if normalized_server_name:
            query = query.filter(WorldRound.server_name == normalized_server_name)
        r = query.first()
        if not r:
            raise HTTPException(status_code=404, detail="Раунд не найден")
        return _gzip_json_response({"rounds": [_serialize_round_full(r)]})

    threshold = datetime.utcnow() - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)
    query = db.query(WorldRound).filter(WorldRound.last_update_at >= threshold)
    if normalized_server_name:
        query = query.filter(WorldRound.server_name == normalized_server_name)
    rows = query.all()
    return _gzip_json_response({"rounds": [_serialize_round_full(r) for r in rows]})
