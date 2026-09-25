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
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db, get_game_db
from security import verify_external_api_key_dependency, verify_client_api_key_dependency, check_permission
from config import get_settings
from models import AICreatureAlert, AdminUser, AdminSession, WorldRound
from services_creature_alerts import (
    LargeCreatureSpawn,
    canonical_creature_name_ru,
    detect_large_creature_spawns,
    find_yasa_position,
    is_configured_large_species,
    is_nonhuman_character,
)

settings = get_settings()
logger = logging.getLogger(__name__)

# A round with no new snapshot in this long is considered over — the wire
# protocol has no explicit "round ended" signal (see guide §7.7), so this is
# purely a server-side staleness heuristic. Production snapshots currently
# arrive about every 8 seconds, making the old 15-second window disappear
# after one missed delivery. Keep roughly five observed intervals instead.
ACTIVE_THRESHOLD_SECONDS = 45
AI_CREATURE_ALERT_LEASE_SECONDS = 300

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


def _resolve_ingest_server_name(body: dict, existing: Optional[str]) -> str:
    """Resolve source server without letting an omitted field erase it."""

    explicit = body.get("server_name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().upper()
    if isinstance(existing, str) and existing.strip():
        return existing.strip().upper()
    return settings.world_default_server_name.strip().upper()


def _merge_id_rows(existing, patch):
    """Merge sparse live rows without losing fields omitted by a delta."""
    if not isinstance(patch, list):
        return existing
    current = {
        row.get("id"): dict(row)
        for row in (existing if isinstance(existing, list) else [])
        if isinstance(row, dict) and row.get("id") is not None
    }
    for row in patch:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        current.setdefault(row["id"], {}).update(row)
    return list(current.values())


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

    row.server_name = _resolve_ingest_server_name(body, row.server_name)
    row.level_seed = body.get("level_seed")
    row.level_name = body.get("level_name")
    row.level_type = body.get("level_type")
    row.level_w = body.get("level_w")
    row.level_h = body.get("level_h")
    row.start_x = body.get("start_x")
    row.end_x = body.get("end_x")
    row.abyss_start_y = body.get("abyss_start_y")
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

    row.server_name = _resolve_ingest_server_name(body, row.server_name)

    seq = body.get("seq")
    if seq is not None and row.last_seq is not None and seq < row.last_seq:
        # Stale/reordered write (shouldn't normally happen within one
        # server's sequential delivery, but costs nothing to guard against).
        return JSONResponse({"status": "ok", "ignored": "stale_seq"})

    static_map_missing = not (
        isinstance(row.sub_name, str)
        and row.sub_name.strip()
        and isinstance(row.submarines, list)
        and row.submarines
    )
    if static_map_missing and (
        row.last_seq is None or (isinstance(seq, int) and seq % 20 == 0)
    ):
        logger.warning(
            "World snapshot received without static map: round_id=%s server=%s "
            "seq=%s; game integration must resend POST /api/v1/world/init",
            row.round_id,
            row.server_name,
            seq,
        )

    claimed_alerts: list[AICreatureAlert] = []
    ai_dispatcher_enabled = False
    if (settings.ai_dispatcher_url or "").strip() and (
        settings.ai_dispatcher_api_key or ""
    ).strip():
        from routes.dispatch import _ai_dispatcher_enabled

        ai_dispatcher_enabled = _ai_dispatcher_enabled(db)
    if ai_dispatcher_enabled:
        characters = body.get("characters")
        current_submarines = body.get("subs")
        spawns = detect_large_creature_spawns(
            current_characters=characters,
            submarines=row.submarines,
            current_submarines=current_submarines,
            large_species=settings.ai_large_creature_species_list,
            abyss_species=settings.ai_abyss_creature_species_list,
        )
        claimed_alerts = _claim_large_creature_alerts(db, row, spawns)
        if spawns or row.last_seq is None or (
            isinstance(seq, int) and seq % 20 == 0
        ):
            character_rows = characters if isinstance(characters, list) else []
            nonhuman_count = sum(
                1
                for character in character_rows
                if is_nonhuman_character(character)
            )
            logger.info(
                "AI contact scan: round_id=%s server=%s seq=%s characters=%s "
                "nonhuman=%s yasa_found=%s candidates=%s leased=%s",
                row.round_id,
                row.server_name,
                seq,
                len(character_rows),
                nonhuman_count,
                find_yasa_position(row.submarines, current_submarines) is not None,
                len(spawns),
                len(claimed_alerts),
            )

    row.last_seq = seq
    row.last_t = body.get("t")
    row.last_sent_at = _ms_to_dt(body.get("sent_at"))
    # Delta clients omit unchanged streams. Preserve their last state instead
    # of turning an omitted field into an empty live-world component.
    if "subs" in body:
        row.last_subs = body.get("subs")
    if "hulls" in body:
        row.last_hulls_state = body.get("hulls")
    if "doors" in body:
        row.last_doors_state = body.get("doors")
    if "reactors" in body:
        row.last_reactors = body.get("reactors")
    if "characters" in body:
        characters = body.get("characters")
        if isinstance(characters, list):
            characters = [
                {key: value for key, value in character.items() if key != "oxygen"}
                for character in characters
                if isinstance(character, dict)
            ]
        merged_characters = _merge_id_rows(row.last_characters, characters)
        row.last_characters = [
            {key: value for key, value in character.items() if key != "oxygen"}
            for character in (merged_characters or [])
            if isinstance(character, dict)
        ]
    # null here means IncludeItems:false on the plugin side — "don't update
    # items this tick", NOT "all items vanished". Only overwrite on a real value.
    items = body.get("items")
    if items is not None:
        row.last_items = items
    row.last_update_at = datetime.utcnow()
    db.commit()
    # Operational warnings enter the per-server FIFO before the briefing
    # triggered by the same snapshot, so an initial contact is reported first.
    _try_queue_ai_large_creature_alert(db, row, claimed_alerts)
    _try_queue_ai_world_briefing(db, row)
    return JSONResponse({"status": "ok"})


def _claim_large_creature_alerts(
    db: Session,
    row: WorldRound,
    spawns: list[LargeCreatureSpawn],
) -> list[AICreatureAlert]:
    """Lease new tracked contacts across snapshots and web workers.

    Character IDs provide exact once-per-entity durability. A second layer
    treats new IDs of the same species at approximately the same range from
    Yasa as members of an already observed contact. Groups which the game
    instantiates over several snapshots therefore produce one warning.
    """

    now = datetime.utcnow()
    expired_before = now - timedelta(seconds=AI_CREATURE_ALERT_LEASE_SECONDS)
    current: list[tuple[LargeCreatureSpawn, AICreatureAlert]] = []
    for spawn in spawns:
        # A configured large contact is reported exactly once for its game
        # character ID during the round. Crossing Yasa's perimeter later must
        # not create a second warning for the same spawn. Unclassified small
        # contacts are only visible inside the perimeter, so their namespace
        # remains separate from the large-contact key space.
        alert_character_id = (
            f"yasa:{spawn.character_id}"
            if spawn.contact_class != "large" and spawn.yasa_perimeter
            else spawn.character_id
        )
        alert = AICreatureAlert(
            round_id=str(row.round_id),
            character_id=alert_character_id,
            species=spawn.species,
            distance_from_yasa_m_approx=spawn.distance_from_yasa_m_approx,
            yasa_perimeter=spawn.yasa_perimeter,
            last_seen_at=now,
        )
        try:
            with db.begin_nested():
                db.add(alert)
                db.flush()
        except IntegrityError:
            # The durable row already exists. It can still be reclaimed if a
            # previous worker died or exhausted delivery retries.
            pass

        alert = (
            db.query(AICreatureAlert)
            .filter(
                AICreatureAlert.round_id == str(row.round_id),
                AICreatureAlert.character_id == alert_character_id,
            )
            .first()
        )
        if alert is None:
            continue
        # Follow the live range of exact entity IDs. This does not emit a new
        # warning; it only keeps later members attached to the current group.
        alert.distance_from_yasa_m_approx = spawn.distance_from_yasa_m_approx
        alert.last_seen_at = now
        current.append((spawn, alert))

    candidates = [
        (spawn, alert)
        for spawn, alert in current
        if alert.delivered_at is None
        and (alert.queued_at is None or alert.queued_at < expired_before)
    ]
    candidate_ids = {int(alert.id) for _, alert in candidates if alert.id is not None}

    # A delivered or actively queued member which is still present keeps its
    # track alive regardless of age. Recently announced contacts also remain
    # anchors briefly after disappearing, covering staggered spawns and entity
    # ID recycling without hiding a materially different range or species.
    anchors = [alert for _, alert in current if int(alert.id or 0) not in candidate_ids]
    tracking_seconds = settings.ai_creature_alert_tracking_seconds
    if tracking_seconds > 0:
        tracked_after = now - timedelta(seconds=tracking_seconds)
        recent = (
            db.query(AICreatureAlert)
            .filter(
                AICreatureAlert.round_id == str(row.round_id),
                or_(
                    AICreatureAlert.last_seen_at >= tracked_after,
                    AICreatureAlert.delivered_at >= tracked_after,
                    AICreatureAlert.queued_at >= tracked_after,
                ),
            )
            .all()
        )
        anchor_ids = {int(alert.id) for alert in anchors if alert.id is not None}
        anchors.extend(
            alert
            for alert in recent
            if alert.id is not None
            and int(alert.id) not in candidate_ids
            and int(alert.id) not in anchor_ids
        )

    suppressed: list[AICreatureAlert] = []
    pending: list[AICreatureAlert] = []
    for spawn, alert in candidates:
        if any(_same_tracked_contact(spawn, anchor) for anchor in anchors):
            # The durable outbox has consumed this entity through an existing
            # track, so it must never surface later as a delayed duplicate.
            alert.delivered_at = now
            alert.queued_at = None
            suppressed.append(alert)
        else:
            pending.append(alert)

    if suppressed:
        db.flush()
        logger.info(
            "AI contact tracking: round_id=%s absorbed=%s active_tracks=%s species=%s",
            row.round_id,
            len(suppressed),
            len(anchors),
            ",".join(sorted({alert.species for alert in suppressed})),
        )

    claimed: list[AICreatureAlert] = []
    for alert in pending:
        leased = (
            db.query(AICreatureAlert)
            .filter(
                AICreatureAlert.id == alert.id,
                AICreatureAlert.delivered_at.is_(None),
                or_(
                    AICreatureAlert.queued_at.is_(None),
                    AICreatureAlert.queued_at < expired_before,
                ),
            )
            .update(
                {AICreatureAlert.queued_at: now},
                synchronize_session=False,
            )
        )
        if leased:
            db.flush()
            db.refresh(alert)
            claimed.append(alert)

    if spawns:
        logger.info(
            "AI large-contact detection: round_id=%s detected=%s leased=%s species=%s",
            row.round_id,
            len(spawns),
            len(claimed),
            ",".join(sorted({spawn.species for spawn in spawns})),
        )
    return claimed


def _same_tracked_contact(
    spawn: LargeCreatureSpawn,
    anchor: AICreatureAlert,
) -> bool:
    """Return whether a new entity belongs to an announced sonar contact."""

    anchor_yasa_perimeter = _alert_is_yasa_perimeter(anchor)
    anchor_contact_class = (
        "large"
        if is_configured_large_species(
            anchor.species,
            settings.ai_large_creature_species_list,
        )
        else "unclassified"
    )
    spawn_yasa_perimeter = spawn.yasa_perimeter
    return (
        anchor_contact_class == spawn.contact_class
        and anchor_yasa_perimeter == spawn_yasa_perimeter
        and _species_tracking_key(anchor.species) == _species_tracking_key(spawn.species)
        and abs(
            int(anchor.distance_from_yasa_m_approx)
            - int(spawn.distance_from_yasa_m_approx)
        )
        <= settings.ai_creature_alert_tracking_distance_meters
    )


def _species_tracking_key(species: str) -> str:
    value = str(species).strip().casefold()
    for suffix in ("_male", "_female", "_m", "_f"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _alert_is_yasa_perimeter(alert: AICreatureAlert) -> bool:
    # Prefix compatibility covers rows created before the explicit column was
    # added; all new rows, including large close contacts, use the boolean.
    return bool(alert.yasa_perimeter) or str(alert.character_id).startswith("yasa:")


def _try_queue_ai_large_creature_alert(
    db: Session,
    row: WorldRound,
    alerts: list[AICreatureAlert],
) -> None:
    if not alerts:
        return
    try:
        from routes.dispatch import _queue_ai_large_creature_alert

        _queue_ai_large_creature_alert(row, alerts, db)
    except Exception:
        logger.exception(
            "AI large-creature alert queue failed: round_id=%s alert_count=%s",
            row.round_id,
            len(alerts),
        )


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
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


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


@admin_router.get("/api/v1/admin/world/servers")
async def api_list_world_servers(request: Request, db: Session = Depends(get_db)):
    """Distinct server_names for the server picker — includes all known servers, not only active."""
    _require_world_state(request, db)
    rows = (
        db.query(WorldRound.server_name)
        .filter(WorldRound.server_name.isnot(None))
        .distinct()
        .all()
    )
    servers = sorted({r[0].strip().upper() for r in rows if r[0] and r[0].strip()})
    # Always expose the known game servers even before they have sent any world data
    # or when their last round is currently inactive — otherwise the picker would
    # hide ASPER2 completely while it is offline (the exact bug report: "Для ASPER2
    # нет активной карты раунда").
    for known in [settings.world_default_server_name.strip().upper(), "ASPER1", "ASPER2"]:
        if known and known not in servers:
            servers.append(known)
    servers = sorted(set(servers))
    return {"servers": servers}


@admin_router.get("/api/v1/admin/world/rounds")
async def api_list_world_rounds(
    request: Request,
    server_name: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Currently-active rounds only — lightweight, drives the round picker. Optional server_name filter for multiservers."""
    _require_world_state(request, db)
    threshold = datetime.utcnow() - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)
    q = db.query(WorldRound).filter(WorldRound.last_update_at >= threshold)
    normalized = (server_name or "").strip().upper() or None
    if normalized:
        q = q.filter(WorldRound.server_name == normalized)
    rows = q.order_by(WorldRound.last_update_at.desc()).all()
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
        "start_x": r.start_x,
        "end_x": r.end_x,
        "abyss_start_y": r.abyss_start_y,
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


def _serialize_ai_tracked_contacts(db: Session, r: WorldRound) -> list[dict]:
    """Expose durable contact movement facts to the AI-only client payload."""

    tracked_after = datetime.utcnow() - timedelta(
        seconds=max(
            settings.ai_creature_alert_tracking_seconds,
            ACTIVE_THRESHOLD_SECONDS,
        )
    )
    try:
        rows = (
            db.query(AICreatureAlert)
            .filter(
                AICreatureAlert.round_id == str(r.round_id),
                AICreatureAlert.last_seen_at >= tracked_after,
            )
            .order_by(AICreatureAlert.last_seen_at.desc(), AICreatureAlert.id)
            .all()
        )
    except SQLAlchemyError:
        # Keep the client endpoint available during a rolling schema update.
        db.rollback()
        return []
    result: list[dict] = []
    for row in rows:
        distance = int(row.distance_from_yasa_m_approx)
        reported_distance = (
            int(row.reported_distance_from_yasa_m_approx)
            if row.reported_distance_from_yasa_m_approx is not None
            else None
        )
        change = distance - reported_distance if reported_distance is not None else None
        if change is None:
            movement = "unknown"
        elif change <= -25:
            movement = "closer"
        elif change >= 25:
            movement = "farther"
        else:
            movement = "stable"
        yasa_perimeter = distance <= 250
        result.append(
            {
                "contact_id": str(row.character_id).removeprefix("yasa:"),
                "contact_class": (
                    "large"
                    if is_configured_large_species(
                        row.species,
                        settings.ai_large_creature_species_list,
                    )
                    else "unclassified"
                ),
                "yasa_perimeter": yasa_perimeter,
                "distance_from_yasa_m_approx": distance,
                "reported_distance_from_yasa_m_approx": reported_distance,
                "distance_change_from_reported_m": change,
                "movement_relative_to_yasa": movement,
                "contact_names": (
                    [canonical_creature_name_ru(row.species)]
                    if yasa_perimeter
                    else []
                ),
                "last_seen_at": (
                    row.last_seen_at.isoformat() if row.last_seen_at else None
                ),
            }
        )
    return result


def _serialize_round_full(
    r: WorldRound,
    *,
    tracked_contacts: Optional[list[dict]] = None,
) -> dict:
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
        "tracked_contacts": tracked_contacts or [],
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
        return _gzip_json_response(
            {
                "rounds": [
                    _serialize_round_full(
                        r,
                        tracked_contacts=_serialize_ai_tracked_contacts(db, r),
                    )
                ]
            }
        )

    threshold = datetime.utcnow() - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)
    query = db.query(WorldRound).filter(WorldRound.last_update_at >= threshold)
    if normalized_server_name:
        query = query.filter(WorldRound.server_name == normalized_server_name)
    rows = query.all()
    return _gzip_json_response(
        {
            "rounds": [
                _serialize_round_full(
                    r,
                    tracked_contacts=_serialize_ai_tracked_contacts(db, r),
                )
                for r in rows
            ]
        }
    )
