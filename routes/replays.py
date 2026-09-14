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
import asyncio
import shutil
import struct
import threading
import time
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import (
    APIRouter, Depends, HTTPException, Request, Query, Form, UploadFile, File, WebSocket,
)
from starlette.websockets import WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session, aliased, joinedload

from database import get_db, GameSessionLocal
from security import verify_external_api_key_dependency, check_permission
from config import get_settings
from models import AdminUser, AdminSession, RoundEvent, RoundReplay, ReplayVoiceSegment, ReplayVoiceSpeaker
import services_replays
from services_replays import ReplayService
from services_replays import (
    build_replay_timeline_overview_rows,
    classify_replay_timeline_lane,
    extract_replay_timeline_item,
    extract_replay_timeline_target,
    iter_replay_download_archive,
)

settings = get_settings()
logger = logging.getLogger(__name__)

# ── API router (game server → NexusData, API key protected) ───────────────────
api_router = APIRouter(
    prefix="/api/v1/replays",
    tags=["replays"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)
ws_router = APIRouter(prefix="/api/v1/replays", tags=["replays-stream"])

STREAM_ROOT = os.path.join(settings.replays_upload_dir, ".streaming")
STREAM_MAX_CHUNK = 2 * 1024 * 1024
STREAM_STALE_SECONDS = 60
# A stream that has seen no new chunk for this long is considered abandoned:
# it is assembled into a *broken* demo in the common catalog and its chunk
# directory is deleted. The 60s snapshot above keeps the directory for a
# possible resume; this threshold is the final, irreversible step.
STREAM_FINALIZE_SECONDS = 5 * 60
_live_cache = {}
_live_cache_lock = threading.Lock()


def _safe_stream_id(value: str) -> str:
    value = (value or "").strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not value or len(value) > 100 or any(c not in allowed for c in value):
        raise HTTPException(status_code=400, detail="Некорректный stream_id")
    return value


def _stream_dir(stream_id: str) -> str:
    return os.path.join(STREAM_ROOT, _safe_stream_id(stream_id))


def _expire_stale_streams() -> None:
    """Materialize disconnected streams so they remain playable after an hour."""
    if not os.path.isdir(STREAM_ROOT):
        return
    cutoff = time.time() - STREAM_STALE_SECONDS
    for stream_id in os.listdir(STREAM_ROOT):
        directory = os.path.join(STREAM_ROOT, stream_id)
        if not os.path.isdir(directory) or os.path.getmtime(directory) > cutoff:
            continue
        # A directory without sequence zero is an orphan, not a playable
        # stream. Leave it for a later retry instead of emitting an exception
        # on every incoming request.
        if not os.path.exists(os.path.join(directory, "00000000.bin")):
            continue
        try:
            _assemble_stream(directory, b"", None, broken=True, keep_directory=True)
        except Exception:
            logger.exception("Could not preserve stale replay stream %s", stream_id)


def _stream_last_activity(directory: str) -> float:
    """Newest mtime inside a stream directory — i.e. when the last chunk landed.

    Directory mtime alone is not the signal: creating a chunk bumps it, but a
    stream whose chunks stopped arriving keeps a stale directory mtime that
    says nothing about the newest chunk. The max over the *.bin files is the
    actual "last chunk created" time — including for streams whose watcher
    tick was never scheduled (missed ticks, restarts, crashes).
    """
    try:
        newest = os.path.getmtime(directory)
    except OSError:
        return 0.0
    try:
        for name in os.listdir(directory):
            if not name.endswith(".bin"):
                continue
            try:
                mtime = os.path.getmtime(os.path.join(directory, name))
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    except OSError:
        pass
    return newest


def finalize_expired_streams(
    max_age_seconds: float = STREAM_FINALIZE_SECONDS,
    root: Optional[str] = None,
) -> dict:
    """Pack abandoned streams into broken demos and delete their chunk folders.

    A stream directory whose newest chunk is older than `max_age_seconds` is
    assembled via _assemble_stream(broken=True) — which registers the demo in
    the common catalog with is_broken — and its chunk directory is removed
    (keep_directory=False). The mtime check runs immediately before each
    assembly, so a stream that resumes mid-scan is left alone.

    Restart-resilience is by construction: there is no in-memory queue and no
    per-stream timer. Every call re-derives the work set from chunk mtimes on
    disk, so streams orphaned by a restart, a missed tick, or a crashed
    assembly are picked up on the next run. Failed assemblies are logged with
    the stream id and reported in "errors".
    """
    base = root or STREAM_ROOT
    if not os.path.isdir(base):
        return {"finalized": [], "skipped": [], "errors": {}}
    try:
        threshold = max(0.0, float(max_age_seconds))
    except (TypeError, ValueError):
        threshold = STREAM_FINALIZE_SECONDS
    cutoff = time.time() - threshold
    finalized: list = []
    skipped: list = []
    errors: dict = {}
    for stream_id in sorted(os.listdir(base)):
        directory = os.path.join(base, stream_id)
        if not os.path.isdir(directory):
            continue
        if _stream_last_activity(directory) > cutoff:
            skipped.append(stream_id)
            continue
        # A directory without sequence zero is an orphan, not a playable
        # stream — same rule as _expire_stale_streams: leave it alone.
        if not os.path.exists(os.path.join(directory, "00000000.bin")):
            skipped.append(stream_id)
            continue
        try:
            _assemble_stream(directory, b"", None, broken=True, keep_directory=False)
        except Exception as exc:
            logger.exception("Could not finalize expired replay stream %s", stream_id)
            errors[stream_id] = str(exc)
            continue
        finalized.append(stream_id)
    return {"finalized": finalized, "skipped": skipped, "errors": errors}


async def _read_stream_chunk(request: Request) -> bytes:
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Пустой бинарный чанк")
    if len(body) > STREAM_MAX_CHUNK:
        raise HTTPException(status_code=413, detail="Чанк слишком большой")
    return body


@api_router.post("/stream/start")
async def start_replay_stream(request: Request, stream_id: str = Query(...), server_name: Optional[str] = Query(None),
                              replay_kind: str = Query("round"), after_round_stream_id: Optional[str] = Query(None)):
    await asyncio.to_thread(_expire_stale_streams)
    data = await _read_stream_chunk(request)
    directory = _stream_dir(stream_id)
    await asyncio.to_thread(os.makedirs, directory, exist_ok=True)
    # A reconnect is a continuation, not a new stream. The first chunk is
    # sequence zero and is accepted only when it is absent or identical.
    await asyncio.to_thread(_write_stream_file, os.path.join(directory, "00000000.bin"), data)
    if replay_kind not in ("round", "lobby"):
        raise HTTPException(status_code=400, detail="Некорректный replay_kind")
    await asyncio.to_thread(_write_stream_meta, directory, replay_kind, after_round_stream_id)
    if replay_kind == "round":
        await asyncio.to_thread(_consume_stream_chunk, stream_id, data, server_name)
    await asyncio.to_thread(_mark_stream_live, stream_id)
    return {"status": "ok"}


@api_router.post("/stream/chunk")
async def append_replay_stream(request: Request, stream_id: str = Query(...), seq: int = Query(..., ge=1)):
    await asyncio.to_thread(_expire_stale_streams)
    data = await _read_stream_chunk(request)
    directory = _stream_dir(stream_id)
    if not os.path.isdir(directory):
        raise HTTPException(status_code=404, detail="Поток не запущен")
    await asyncio.to_thread(_write_stream_file, os.path.join(directory, f"{seq:08d}.bin"), data)
    replay_kind, _ = await asyncio.to_thread(_read_stream_meta, directory)
    if replay_kind == "round":
        await asyncio.to_thread(_consume_stream_chunk, stream_id, data, None)
    await asyncio.to_thread(_mark_stream_live, stream_id)
    return {"status": "ok", "seq": seq}


@api_router.post("/stream/finish")
async def finish_replay_stream(request: Request, stream_id: str = Query(...), server_name: Optional[str] = Query(None),
                               replay_kind: str = Query("round"), after_round_stream_id: Optional[str] = Query(None)):
    await asyncio.to_thread(_expire_stale_streams)
    end = await _read_stream_chunk(request)
    directory = _stream_dir(stream_id)
    if not os.path.isdir(directory):
        raise HTTPException(status_code=404, detail="Поток не запущен")
    stored_kind, stored_after_round = await asyncio.to_thread(_read_stream_meta, directory)
    replay_kind = stored_kind or replay_kind
    after_round_stream_id = stored_after_round or after_round_stream_id
    if replay_kind == "round":
        await asyncio.to_thread(_consume_stream_chunk, stream_id, end, server_name)
    return await asyncio.to_thread(_assemble_stream, directory, end, server_name, False, False,
                                   replay_kind, after_round_stream_id)


@api_router.post("/stream/dump")
async def dump_lobby_stream(request: Request, stream_id: str = Query(...)):
    directory = _stream_dir(stream_id)
    if not os.path.isdir(directory):
        raise HTTPException(status_code=404, detail="Поток лобби не найден")
    replay_kind, after_round_stream_id = await asyncio.to_thread(_read_stream_meta, directory)
    if replay_kind != "lobby":
        raise HTTPException(status_code=409, detail="Текущий поток не является записью лобби")
    body = await request.json()
    try:
        duration_ms = max(0, min(0xFFFFFFFF, int(body.get("duration_ms", 0))))
        action_id = int(body["action_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные параметры дампа")
    if action_id <= 0:
        raise HTTPException(status_code=400, detail="Некорректный action_id")
    end = struct.pack("<BI", 0xFF, duration_ms)
    return await asyncio.to_thread(
        _assemble_stream, directory, end, body.get("server_name"), False, True,
        "lobby_dump", after_round_stream_id,
        f"{stream_id}-dump-{action_id}", action_id,
        body.get("dumped_by_admin_id"), body.get("dumped_by_name"),
    )


def _write_stream_meta(directory: str, replay_kind: str, after_round_stream_id: Optional[str]) -> None:
    with open(os.path.join(directory, "stream-meta.json"), "w", encoding="utf-8") as handle:
        json.dump({"replay_kind": replay_kind, "after_round_stream_id": after_round_stream_id}, handle)


def _read_stream_meta(directory: str) -> tuple[Optional[str], Optional[str]]:
    try:
        with open(os.path.join(directory, "stream-meta.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        replay_kind = data.get("replay_kind")
        return (replay_kind if replay_kind in ("round", "lobby") else None,
                data.get("after_round_stream_id"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None, None


def _websocket_api_key_is_valid(websocket: WebSocket) -> bool:
    if not settings.require_auth:
        return True
    supplied = websocket.headers.get("x-api-key", "")
    expected = getattr(settings, "api_key", "") or ""
    return bool(expected and secrets.compare_digest(supplied, expected))


@ws_router.websocket("/stream/ws")
async def replay_stream_websocket(websocket: WebSocket, stream_id: str = Query(...), server_name: Optional[str] = Query(None)):
    """Ordered binary replay transport.

    The first binary message is the .barodemo header/static section. Every
    following binary message is one frame batch. The final text message must
    be ``finish``; the last binary batch already contains FRAME_END.
    """
    if not _websocket_api_key_is_valid(websocket):
        await websocket.close(code=1008, reason="invalid api key")
        return
    try:
        safe_id = _safe_stream_id(stream_id)
    except HTTPException:
        await websocket.close(code=1008, reason="invalid stream id")
        return

    await websocket.accept()
    directory = _stream_dir(safe_id)
    await asyncio.to_thread(os.makedirs, directory, exist_ok=True)
    existing = await asyncio.to_thread(_stream_sequence_state, directory)
    seq = existing[0]
    started = existing[1]
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                data = message["bytes"]
                if not data or len(data) > STREAM_MAX_CHUNK:
                    await websocket.close(code=1009, reason="chunk too large")
                    return
                path = os.path.join(directory, f"{seq:08d}.bin")
                await asyncio.to_thread(_write_stream_file, path, data)
                await asyncio.to_thread(_consume_stream_chunk, safe_id, data, server_name)
                await asyncio.to_thread(_mark_stream_live, safe_id)
                started = True
                seq += 1
                continue
            text = (message.get("text") or "").strip().lower()
            if text == "finish":
                if not started:
                    await websocket.close(code=1008, reason="empty stream")
                    return
                result = await asyncio.to_thread(_assemble_stream, directory, b"", server_name)
                await websocket.send_json(result)
                await websocket.close(code=1000)
                return
            await websocket.close(code=1003, reason="expected binary chunk or finish")
            return
    except WebSocketDisconnect:
        return


def _write_stream_file(path: str, data: bytes) -> None:
    if os.path.exists(path):
        with open(path, "rb") as existing:
            if hashlib.sha256(existing.read()).digest() == hashlib.sha256(data).digest():
                return
        raise ValueError("sequence number already contains different data")
    try:
        with open(path, "xb") as f:
            f.write(data)
    except FileExistsError:
        # A retried HTTP request may race with the original request.  Chunks
        # are addressed by sequence number, so accepting the existing file is
        # safe and keeps the transport idempotent.
        return


def _stream_sequence_state(directory: str) -> tuple[int, bool]:
    names = sorted(n for n in os.listdir(directory) if n.endswith(".bin"))
    expected = [f"{i:08d}.bin" for i in range(len(names))]
    if names != expected:
        return len(names), bool(names)
    return len(names), bool(names)


def _mark_stream_live(stream_id: str) -> None:
    from database import SessionLocal
    db = SessionLocal()
    try:
        from models import RoundReplay
        row = db.query(RoundReplay).filter(RoundReplay.stream_id == stream_id).first()
        if row and row.is_broken:
            row.is_broken = False
            row.broken_at = None
            db.commit()
    finally:
        db.close()


class _DemoTruncated(Exception):
    """A chunk ended mid-frame.

    Huge static frames (geometry/outline) may span several HTTP chunks, so a
    single chunk is not always self-contained. The replay file itself is still
    preserved verbatim; only the live extraction stops at the cut point.
    """


def _need(data: bytes, offset: int, size: int) -> None:
    if size < 0 or offset + size > len(data):
        raise _DemoTruncated()


def _read_u16(data: bytes, offset: int) -> tuple[int, int]:
    _need(data, offset, 2)
    return struct.unpack_from("<H", data, offset)[0], offset + 2


def _read_u32(data: bytes, offset: int) -> tuple[int, int]:
    _need(data, offset, 4)
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    _need(data, offset, 4)
    return struct.unpack_from("<i", data, offset)[0], offset + 4


def _read_str(data: bytes, offset: int) -> tuple[str, int]:
    length, offset = _read_u16(data, offset)
    _need(data, offset, length)
    return data[offset:offset + length].decode("utf-8", errors="replace"), offset + length


def _read_bytes(data: bytes, offset: int, size: int) -> tuple[bytes, int]:
    _need(data, offset, size)
    return data[offset:offset + size], offset + size


# Mirror of the game plugin's outline cap (WorldDemoStreamer/DemoRecorder):
# static geometry beyond this is dropped from live state, never from the file.
_DEMO_STATIC_POLY_CAP = 20000


def _parse_demo_chunk(data: bytes, demo_version: int = 7, base_origin: tuple = (0, 0)) -> dict:
    """Extract live state from a demo frame batch.

    Never raises: truncation (a static frame split across chunks) yields
    whatever was fully decoded; anything else unexpected skips the chunk.
    The assembled replay file is always preserved verbatim by the caller.

    `demo_version` selects version-dependent layouts (entity oxygen byte,
    spawn job, inventory owner kinds, hull-assign frame — all v7+); it is
    overridden by the stream header whenever the chunk carries one.
    `base_origin` resolves coords in chunks that start mid-stream, before
    their first 0x02 origin frame.

    Keys produced: header, static (hulls/walls/doors/outline), origin,
    spawns, despawns, hull_assign, t, characters, doors, subs, items.
    """
    result: dict = {}
    try:
        _parse_demo_chunk_into(data, result, demo_version, base_origin)
    except _DemoTruncated:
        pass
    except Exception:
        logger.warning("Demo live parse failed, chunk skipped", exc_info=True)
        return {}
    return result


def _parse_demo_chunk_into(data: bytes, result: dict, demo_version: int, base_origin: tuple) -> None:
    offset = 0
    origin_x, origin_y = base_origin
    version = demo_version
    if data[:4] == b"BDMO":
        _need(data, 0, 17)
        version, offset = _read_u16(data, 4)
        flags, offset = _read_u16(data, offset)
        _need(data, offset, 1 + 8)
        tick_rate = data[offset]
        offset += 1
        start_ms = struct.unpack_from("<q", data, offset)[0]
        offset += 8
        server, offset = _read_str(data, offset)
        seed, offset = _read_str(data, offset)
        level, offset = _read_str(data, offset)
        sub, offset = _read_str(data, offset)
        _need(data, offset, 4)
        offset += 4
        result["header"] = {
            "version": version, "flags": flags, "tick_rate": tick_rate,
            "start_ms": start_ms, "server": server, "seed": seed,
            "level": level, "sub": sub,
        }
        # `version` above came from the header itself and now governs the
        # version-dependent frame layouts below for the rest of this chunk.
    while offset < len(data):
        _need(data, offset, 1)
        frame = data[offset]
        offset += 1
        if frame == 0x10:
            hulls, offset = _read_u16(data, offset)
            _need(data, offset, hulls * 20)
            hull_rows = []
            for _ in range(hulls):
                sub_id, p = _read_u16(data, offset)
                hull_id, p = _read_u16(data, p)
                x, p = _read_i32(data, p)
                y, p = _read_i32(data, p)
                w, p = _read_i32(data, p)
                h, p = _read_i32(data, p)
                offset = p
                hull_rows.append({"id": hull_id, "sub": sub_id, "r": [x, y, w, h]})
            walls, offset = _read_u16(data, offset)
            _need(data, offset, walls * 19)
            wall_rows = []
            for _ in range(walls):
                sub_id, p = _read_u16(data, offset)
                x, p = _read_i32(data, p)
                y, p = _read_i32(data, p)
                w, p = _read_i32(data, p)
                h, p = _read_i32(data, p)
                _need(data, p, 1)
                wall_flags = data[p]
                offset = p + 1
                wall_rows.append({"sub": sub_id, "r": [x, y, w, h],
                                  "h": bool(wall_flags & 1), "p": bool(wall_flags & 2)})
            door_count, offset = _read_u16(data, offset)
            _need(data, offset, door_count * 21)
            door_rows = []
            for _ in range(door_count):
                sub_id, p = _read_u16(data, offset)
                door_id, p = _read_u16(data, p)
                x, p = _read_i32(data, p)
                y, p = _read_i32(data, p)
                w, p = _read_i32(data, p)
                h, p = _read_i32(data, p)
                _need(data, p, 1)
                door_flags = data[p]
                offset = p + 1
                door_rows.append({"id": door_id, "sub": sub_id, "r": [x, y, w, h],
                                  "h": bool(door_flags & 1)})
            result.setdefault("static", {}).update(
                {"hulls": hull_rows, "walls": wall_rows, "doors": door_rows})
        elif frame == 0x11:
            level_w, offset = _read_i32(data, offset)
            level_h, offset = _read_i32(data, offset)
            polygons, offset = _read_u16(data, offset)
            outline = []
            for _ in range(min(polygons, _DEMO_STATIC_POLY_CAP)):
                points, offset = _read_u16(data, offset)
                _need(data, offset, points * 8)
                poly = []
                for _ in range(points):
                    x, p = _read_i32(data, offset)
                    y, p = _read_i32(data, p)
                    offset = p
                    poly.append([x, y])
                if len(poly) >= 3:
                    outline.append(poly)
            result.setdefault("static", {}).update(
                {"outline": outline, "outline_w": level_w, "outline_h": level_h})
        elif frame == 0x02:
            _need(data, offset, 8)
            origin_x, origin_y = struct.unpack_from("<ii", data, offset)
            offset += 8
            result["origin"] = (origin_x, origin_y)
        elif frame == 0x03:
            _need(data, offset, 4)
            offset += 4
            character_id, offset = _read_u16(data, offset)
            species, offset = _read_str(data, offset)
            name, offset = _read_str(data, offset)
            _need(data, offset, 1)
            team = struct.unpack_from("<b", data, offset)[0]
            offset += 1
            # v7+: job identifier, sent once per entity (2 bytes when empty).
            job, offset = _read_str(data, offset) if version >= 7 else ("", offset)
            result.setdefault("spawns", {})[character_id] = {
                "name": name, "species": species,
                "team": {1: "Team1", 2: "Team2", 3: "FriendlyNPC"}.get(team, "None"),
                "job": job or None,
            }
        elif frame == 0x04:
            # Despawn (format v6+): stale spawn/name entries must go, otherwise
            # recycled entity IDs keep showing the wrong name on the live page.
            _need(data, offset, 4 + 2)
            offset += 4
            despawn_id, offset = _read_u16(data, offset)
            result.setdefault("despawns", []).append(despawn_id)
        elif frame == 0x0A:
            # v7+ sparse hull assignments: only characters whose hull changed
            # since the previous snapshot are listed (usually a few or zero).
            _need(data, offset, 4)
            offset += 4
            assign_count, offset = _read_u16(data, offset)
            _need(data, offset, assign_count * 4)
            assign = {}
            for _ in range(assign_count):
                assign_id, offset = _read_u16(data, offset)
                assign_hull, offset = _read_u16(data, offset)
                assign[assign_id] = assign_hull
            result.setdefault("hull_assign", {}).update(assign)
        elif frame == 0x01:
            time_ms, offset = _read_u32(data, offset)
            count, offset = _read_u16(data, offset)
            ent_size = 7 if version >= 7 else 6  # v7 appends 1 oxygen byte
            _need(data, offset, count * ent_size)
            characters = []
            for _ in range(count):
                character_id, offset = _read_u16(data, offset)
                _need(data, offset, ent_size)
                x, y, flags, health = struct.unpack_from("<hhBB", data, offset)
                offset += 6
                if version >= 7:
                    oxygen = data[offset]
                    offset += 1
                else:
                    oxygen = 0
                characters.append({
                    "id": character_id,
                    "name": "",
                    "species": "",
                    "human": bool(flags & 4),
                    "team": "None",
                    "job": None,
                    "steamid": None,
                    "x": origin_x + x * 4,
                    "y": origin_y + y * 4,
                    "hp": health * 200.0 / 255.0,
                    "oxygen": oxygen,
                    "dead": bool(flags & 1),
                    "water": bool(flags & 8),
                    "ragdoll": bool(flags & 16),
                    "dir": -1 if flags & 2 else 1,
                    "hull": None,
                })
            door_count, offset = _read_u16(data, offset)
            _need(data, offset, door_count * 3)
            doors = []
            for _ in range(door_count):
                door_id, offset = _read_u16(data, offset)
                opened = data[offset]
                offset += 1
                doors.append({"id": door_id, "open": opened * 100.0 / 255.0})
            water_count, offset = _read_u16(data, offset)
            _need(data, offset, water_count * 3)
            offset += water_count * 3
            sub_count, offset = _read_u16(data, offset)
            _need(data, offset, sub_count * 10)
            subs = []
            for _ in range(sub_count):
                sub_id, offset = _read_u16(data, offset)
                _need(data, offset, 8)
                x, y = struct.unpack_from("<ii", data, offset)
                offset += 8
                subs.append({"id": sub_id, "x": x, "y": y})
            result.update({"t": time_ms / 1000.0, "characters": characters, "doors": doors, "subs": subs})
        elif frame == 0x06:
            _need(data, offset, 4 + 1 + 2 + 2 + 4 + 4 + 4)
            offset += 4 + 1 + 2 + 2 + 4 + 4 + 4
            _, offset = _read_str(data, offset)
        elif frame == 0x05:
            # Chat (DemoRecorder): time + sender + type + author + text.
            _need(data, offset, 4 + 2 + 1)
            offset += 4 + 2 + 1
            _, offset = _read_str(data, offset)
            _, offset = _read_str(data, offset)
        elif frame == 0x07:
            _need(data, offset, 4 + 2)
            offset += 4 + 2
            length, offset = _read_u16(data, offset)
            _need(data, offset, length)
            offset += length
        elif frame == 0x09:
            # Voice with channel marker (WorldDemoStreamer): time + speaker +
            # radio + payload. Previously fell into the unknown-frame branch
            # and silently dropped the whole rest of the chunk from live state
            # on every voice-active tick.
            _need(data, offset, 4 + 2 + 1)
            offset += 4 + 2 + 1
            length, offset = _read_u16(data, offset)
            _need(data, offset, length)
            offset += length
        elif frame == 0x08:
            count, offset = _read_u16(data, offset)
            items = []
            for _ in range(count):
                item_id, offset = _read_u16(data, offset)
                if version >= 7:
                    # v7: direct owner kind (0 world, 1 character, 2 item
                    # container, 3 hull floor). Coords only for unowned items,
                    # origin-relative i16 like entities (4 bytes, not 8).
                    _need(data, offset, 1)
                    owner_kind = data[offset]
                    offset += 1
                    owner_id, offset = _read_u16(data, offset)
                else:
                    owner_id, offset = _read_u16(data, offset)
                    owner_kind = 1 if owner_id else 0
                identifier, offset = _read_str(data, offset)
                _need(data, offset, 1)
                condition = data[offset]
                offset += 1
                if version >= 7 and owner_kind in (0, 3):
                    _need(data, offset, 4)
                    ix, iy = struct.unpack_from("<hh", data, offset)
                    offset += 4
                    ix = origin_x + ix * 4
                    iy = origin_y + iy * 4
                else:
                    ix, iy = 0, 0
                items.append({
                    "id": item_id, "item": identifier, "x": ix, "y": iy,
                    "cond": condition * 100.0 / 255.0,
                    "owner_t": {0: "world", 1: "character", 2: "item", 3: "hull"}.get(owner_kind, "world"),
                    "owner_id": owner_id or None,
                })
            result["items"] = items
        elif frame == 0xFF:
            _need(data, offset, 4)
            offset += 4
            break
        else:
            # Genuinely newer frame type: stop this chunk, keep the file.
            break


def _apply_parsed_to_state(state: dict, parsed: dict) -> None:
    """Merge one parsed chunk into the per-stream live cache (pure, no I/O)."""
    if "header" in parsed:
        state["header"] = parsed["header"]
        state["demo_version"] = parsed["header"].get("version", 7)
    if "origin" in parsed:
        state["origin"] = parsed["origin"]
    if "spawns" in parsed:
        state.setdefault("spawns", {}).update(parsed["spawns"])
    despawns = parsed.get("despawns") or []
    if despawns:
        gone = set(despawns)
        spawns = state.get("spawns")
        if spawns:
            for character_id in gone:
                spawns.pop(character_id, None)
        hulls = state.get("hull_by_char")
        if hulls:
            for character_id in gone:
                hulls.pop(character_id, None)
        if isinstance(state.get("characters"), list):
            state["characters"] = [c for c in state["characters"] if c.get("id") not in gone]
    if "hull_assign" in parsed:
        state.setdefault("hull_by_char", {}).update(parsed["hull_assign"])
    if "characters" in parsed:
        spawns = state.get("spawns", {})
        hulls = state.get("hull_by_char", {})
        for character in parsed["characters"]:
            character.update(spawns.get(character["id"], {}))
            hull_id = hulls.get(character["id"])
            character["hull"] = hull_id if hull_id else None
    for key in ("t", "characters", "doors", "subs", "items"):
        if key in parsed:
            state[key] = parsed[key]
    if "static" in parsed:
        state["static"] = parsed["static"]


def _fill_static_from_demo(row, parsed: dict, payload: dict) -> bool:
    """Fill empty static-geometry columns from the demo stream itself.

    `/world/init` stays authoritative and always overwrites these columns;
    this is only a fallback so live monitoring works even when init was lost.
    Returns True when anything was filled.
    """
    filled = False
    static = parsed.get("static") or {}
    header = parsed.get("header") or {}

    def _fill(attr: str, value) -> None:
        nonlocal filled
        if value is not None and not getattr(row, attr, None):
            setattr(row, attr, value)
            filled = True

    if static:
        _fill("hulls", static.get("hulls"))
        _fill("walls", static.get("walls"))
        _fill("doors", static.get("doors"))
        _fill("outline", static.get("outline"))
        if static.get("outline") and (not row.outline_w or not row.outline_h):
            row.outline_w = static.get("outline_w")
            row.outline_h = static.get("outline_h")
            filled = True
    if header:
        _fill("sub_name", (header.get("sub") or "").strip() or None)
        _fill("level_seed", (header.get("seed") or "").strip() or None)
        _fill("level_name", (header.get("level") or "").strip() or None)
        if not getattr(row, "submarines", None):
            sub_ids = {h.get("sub") for h in (static.get("hulls") or []) if h.get("sub")}
            sub_ids.update(s.get("id") for s in (payload.get("subs") or []) if s.get("id") is not None)
            if sub_ids:
                row.submarines = [{"id": sid, "name": ""} for sid in sorted(sub_ids)]
                filled = True
    return filled


# Bound the per-stream live cache: one small entry per round, but rounds are
# unbounded over process lifetime.
_LIVE_CACHE_MAX_STREAMS = 64


def _consume_stream_chunk(stream_id: str, data: bytes, server_name: Optional[str] = None) -> None:
    # Version and origin are per-stream incremental state: the header and
    # 0x02 frames only ride the first chunks, later chunks need them for
    # the v7 layouts and origin-relative coords.
    with _live_cache_lock:
        cached = _live_cache.get(stream_id, {})
        demo_version = cached.get("demo_version", 7)
        base_origin = cached.get("origin", (0, 0))
    parsed = _parse_demo_chunk(data, demo_version, base_origin)
    if not parsed:
        return
    now = datetime.utcnow()
    with _live_cache_lock:
        state = _live_cache.get(stream_id)
        if state is None:
            if len(_live_cache) >= _LIVE_CACHE_MAX_STREAMS:
                _live_cache.pop(next(iter(_live_cache)))
            state = _live_cache.setdefault(stream_id, {"pending": 0, "seq": 0})
        _apply_parsed_to_state(state, parsed)
        if "t" in parsed:
            state["seq"] += 1
        # Live "Обзор мира" polls state at 3 Hz and must see every ingested
        # tick: persist on EVERY chunk carrying new live data, never batch
        # several ticks into one DB write. The old `pending < 3` gate turned
        # a ~3s ingest cadence into a ~10s visible refresh rate.
        static_pending = "static" in parsed or "header" in parsed
        payload = dict(state)
        payload["__static"] = static_pending
    db = GameSessionLocal()
    try:
        from models import WorldRound
        row = db.query(WorldRound).filter(WorldRound.round_id == stream_id).first()
        if row is None:
            row = WorldRound(round_id=stream_id, server_name=server_name)
            db.add(row)
        if row:
            if payload.pop("__static", False):
                _fill_static_from_demo(row, parsed, payload)
            row.last_seq = payload.get("seq")
            row.last_t = payload.get("t", 0)
            row.last_sent_at = now
            row.last_subs = payload.get("subs", [])
            row.last_hulls_state = []
            row.last_doors_state = payload.get("doors", [])
            row.last_reactors = []
            row.last_characters = payload.get("characters", [])
            row.last_items = payload.get("items", [])
            row.last_update_at = now
            db.commit()
    except Exception:
        # Live monitoring is auxiliary to replay ingest.  Never turn a DB,
        # serialization, or schema problem here into a failed binary chunk:
        # the chunk has already been persisted and can still be assembled.
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Replay live state update failed for stream %s", stream_id)
    finally:
        db.close()


def _assemble_stream(directory: str, end: bytes, server_name: Optional[str],
                    broken: bool = False, keep_directory: bool = False,
                    replay_kind: Optional[str] = None,
                    after_round_stream_id: Optional[str] = None,
                    stream_id_override: Optional[str] = None,
                    dump_action_id: Optional[int] = None,
                    dumped_by_admin_id: Optional[int] = None,
                    dumped_by_name: Optional[str] = None) -> dict:
    import uuid
    from database import SessionLocal
    try:
        with open(os.path.join(directory, "stream-meta.json"), encoding="utf-8") as handle:
            stream_meta = json.load(handle)
        replay_kind = replay_kind or stream_meta.get("replay_kind")
        after_round_stream_id = after_round_stream_id or stream_meta.get("after_round_stream_id")
    except (OSError, ValueError, TypeError):
        pass
    replay_kind = replay_kind if replay_kind in ("round", "lobby", "lobby_dump") else "round"
    chunk_names = sorted(n for n in os.listdir(directory) if n.endswith(".bin"))
    expected = [f"{index:08d}.bin" for index in range(len(chunk_names))]
    if chunk_names != expected:
        if broken:
            # Preserve the longest valid prefix. The missing chunks can still
            # arrive later and the next assembly will replace this snapshot.
            prefix = []
            for index in range(len(chunk_names) + 1):
                name = f"{index:08d}.bin"
                if name not in chunk_names:
                    break
                prefix.append(name)
            chunk_names = prefix
        else:
            missing = [name for name in expected if name not in chunk_names]
            raise HTTPException(status_code=409, detail={
                "message": "Поток демки содержит пропущенные чанки",
                "missing": missing[:20],
                "received": chunk_names[-5:],
            })
    if not chunk_names:
        missing = [name for name in expected if name not in chunk_names]
        raise HTTPException(status_code=409, detail={
            "message": "Поток демки содержит пропущенные чанки",
            "missing": missing[:20],
            "received": chunk_names[-5:],
        })
    if sum(os.path.getsize(os.path.join(directory, name)) for name in chunk_names) + len(end) > 2 * 1024 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Демка превышает лимит 2 ГБ")
    raw_path = os.path.join(
        directory,
        f"round_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.barodemo",
    )
    with open(raw_path, "wb") as out:
        for name in chunk_names:
            with open(os.path.join(directory, name), "rb") as src:
                shutil.copyfileobj(src, out, length=1024 * 1024)
        out.write(end)
    db = SessionLocal()
    try:
        replay = ReplayService(db).save_staged_demo(
            raw_path, os.path.basename(raw_path), server_name=server_name,
            notes=("Собрано потоковым WorldDemoStreamer; поток прерван более минуты"
                   if broken else "Собрано потоковым WorldDemoStreamer"),
            stream_id=stream_id_override or os.path.basename(directory), is_broken=broken,
            replay_kind=replay_kind, after_round_stream_id=after_round_stream_id,
            dump_action_id=dump_action_id, dumped_by_admin_id=dumped_by_admin_id,
            dumped_by_name=dumped_by_name,
        )
        return {"success": True, "replay_id": replay.id, "stored_filename": replay.stored_filename,
                "broken": broken}
    finally:
        db.close()
        if keep_directory:
            try:
                os.remove(raw_path)
            except OSError:
                pass
        else:
            shutil.rmtree(directory, ignore_errors=True)

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
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


async def _ensure_csrf(request: Request, db: Session):
    if request.url.path.startswith("/api/") and getattr(request.state, "nd_auth_type", None) == "login_key":
        return
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
        "is_broken": bool(getattr(r, "is_broken", False)),
        "replay_kind": getattr(r, "replay_kind", "round") or "round",
        "after_round_stream_id": getattr(r, "after_round_stream_id", None),
        "dump_action_id": getattr(r, "dump_action_id", None),
        "dumped_by_admin_id": getattr(r, "dumped_by_admin_id", None),
        "dumped_by_name": getattr(r, "dumped_by_name", None),
    }


def _serialize_replay_voice_speaker(row) -> dict:
    return {
        "speaker_id": int(row.speaker_id),
        "names": list(row.names or []),
        "steam_id": row.steam_id,
        "player_id": row.player_id,
        "resolution_status": row.resolution_status,
    }


def _serialize_replay_voice_segment(row) -> dict:
    return {
        "speaker_id": int(row.speaker_id),
        "start_ms": int(row.start_ms),
        "end_ms": int(row.end_ms),
        # Persisted segments currently aggregate channel statistics. Keep the
        # flag conservative: a mixed segment must not be presented as radio.
        "radio": bool(row.radio_packet_count and row.packet_count and row.radio_packet_count == row.packet_count),
        "packet_count": int(row.packet_count or 0),
        "radio_packet_count": int(row.radio_packet_count or 0),
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
    lobby_replay = None
    if replay and replay.stream_id:
        lobby_replay = db.query(RoundReplay).filter(
            RoundReplay.replay_kind.in_(("lobby", "lobby_dump")),
            RoundReplay.after_round_stream_id == replay.stream_id,
        ).order_by(RoundReplay.replay_kind.asc(), RoundReplay.uploaded_at.desc()).first()
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
        "lobby_replay": _serialize_replay(lobby_replay) if lobby_replay else None,
        "lobby_replay_status": "ready" if lobby_replay else "waiting",
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
            or_(RoundReplay.replay_kind.is_(None), RoundReplay.replay_kind == "round"),
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
            or_(RoundReplay.replay_kind.is_(None), RoundReplay.replay_kind == "round"),
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
        if getattr(replay, "replay_kind", "round") == "lobby":
            raise HTTPException(status_code=400, detail="Демо лобби нельзя назначить демо раунда")
        replay.manual_round_event_id = round_id

    db.commit()
    return {"status": "ok"}


@panel_router.post("/api/v1/admin/replays/lobby-dump")
async def api_request_lobby_dump(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    body = await request.json()
    server_name = str(body.get("server_name") or "").strip().upper()
    if server_name not in ("ASPER1", "ASPER2"):
        raise HTTPException(status_code=400, detail="Неизвестный сервер")

    from routes.dispatch import get_round_state
    if get_round_state(db, server_name)["active"]:
        raise HTTPException(status_code=409, detail="На выбранном сервере сейчас идёт раунд")

    from services import ServerModerationService
    from routes.admin import log_admin_action
    requested_by_name = admin.discord_username or f"Администратор #{admin.id}"
    action = ServerModerationService(db).enqueue_action(
        {"action_type": "lobby_replay_dump", "target_steamid": "system"},
        admin_id=admin.id,
        server_name=server_name,
        payload={
            "requested_by_admin_id": admin.id,
            "requested_by_name": requested_by_name,
        },
    )
    log_admin_action(db, admin.id, "replay_lobby_dump_requested", {
        "server_name": server_name,
        "action_id": action.id,
    })
    return {"status": "queued", "action_id": action.id, "server_name": server_name}


@panel_router.get("/api/v1/admin/replays/lobby-dump/options")
async def api_lobby_dump_options(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    from routes.dispatch import get_round_state
    return {
        "can_request": check_permission(admin, "replays", "edit"),
        "servers": [
            {"server_name": server_name, "round_active": bool(get_round_state(db, server_name)["active"])}
            for server_name in ("ASPER1", "ASPER2")
        ],
    }


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
    if getattr(replay, "has_voice", False):
        from services_voice_profiles import VoiceProfileService

        VoiceProfileService(db).ensure_replay_voice_activity(replay_id)
    payload = _serialize_replay(replay)
    payload["voice_speakers"] = [
        _serialize_replay_voice_speaker(row)
        for row in db.query(ReplayVoiceSpeaker)
        .filter(ReplayVoiceSpeaker.replay_id == replay_id)
        .order_by(ReplayVoiceSpeaker.speaker_id.asc())
        .all()
    ]
    payload["voice_segments"] = [
        _serialize_replay_voice_segment(row)
        for row in db.query(ReplayVoiceSegment)
        .filter(ReplayVoiceSegment.replay_id == replay_id)
        .order_by(ReplayVoiceSegment.start_ms.asc(), ReplayVoiceSegment.speaker_id.asc())
        .all()
    ]
    return {"replay": payload}


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
    round_search: bool = Query(
        False,
        description="Индексированный literal-поиск по всему временному диапазону раунда.",
    ),
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

    # Everything the slow SQLite timeline query needs is now detached scalar
    # data. Release the main MySQL connection before awaiting the worker:
    # aborted/full-round searches can keep running for up to their budget, and
    # retaining one checked-out connection per search used to exhaust the
    # 5+10 pool and make unrelated requests, workers and Discord wait 30s.
    resolved_replay_id = int(replay.id)
    resolved_server_name = str(replay.server_name)
    resolved_round_id = end_event.id if end_event else None
    db.close()

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
        search_logs_in_round,
    )

    def load_pages():
        """Read and enrich one exact visible window in the worker thread."""
        if round_search and search and search.strip():
            result = search_logs_in_round(
                server_name=resolved_server_name,
                player_name=player or None,
                action_type=action_type or None,
                search_text=search,
                time_from=query_started_at,
                time_to=query_ended_at,
                category=category or None,
                limit=limit,
                player_scope=player_scope,
                raise_on_unavailable=True,
            )
            loaded = list(result.events)
            first_total = result.total
            first_total_is_capped = result.total_is_capped
            has_more = result.has_more
        else:
            loaded = []
            cursor = None
            first_total = None
            first_total_is_capped = False
            has_more = False
            while len(loaded) < limit:
                page_size = min(5000, limit - len(loaded))
                result = search_logs(
                    server_name=resolved_server_name,
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
                "target_name": extract_replay_timeline_target(event.action_type, event.details),
                "precision_ms": 1_000,
            })
        return rows, first_total, first_total_is_capped, has_more, player_counts, action_counts

    def load_overview():
        # About 600 columns is enough to preserve the shape of a full round
        # while remaining much smaller than its exact event stream.
        bucket_ms = max(1_000, (effective_duration_ms + 599) // 600)
        raw = get_log_timeline_overview(
            server_name=resolved_server_name,
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
            resolved_replay_id,
            resolved_server_name,
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
        server_stats = stats.get(resolved_server_name, {})
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
            resolved_replay_id,
            resolved_server_name,
            resolved_round_id,
            anchor_source,
            started_at,
            ended_at,
            oldest_raw,
            newest_raw,
        )

    if unavailable_reason:
        return JSONResponse(_timeline_unavailable_payload(
            unavailable_reason,
            round_id=resolved_round_id,
            server_name=resolved_server_name,
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
        "round_id": resolved_round_id,
        "server_name": resolved_server_name,
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


def _replay_archive_member_name(replay) -> str:
    original_name = os.path.basename((replay.filename or "").strip())
    stored_name = os.path.basename((replay.stored_filename or "").strip())
    if stored_name.endswith(".gz"):
        return original_name if original_name.endswith(".gz") else f"replay_{replay.id}.barodemo.gz"
    return original_name if original_name.endswith(".barodemo") else f"replay_{replay.id}.barodemo"


@panel_router.get("/api/v1/admin/replays/{replay_id}/archive")
async def api_replay_archive(request: Request, replay_id: int, db: Session = Depends(get_db)):
    """Download a genuine ZIP regardless of the replay's storage format."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "replays", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    svc = ReplayService(db)
    replay = svc.get_by_id(replay_id)
    if not replay:
        raise HTTPException(status_code=404, detail="Реплей не найден")

    source_path = svc.get_file_path(replay)
    if not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    archive_name = f"replay_{replay.id}.barodemo.zip"
    if replay.stored_filename.endswith(".zip"):
        return FileResponse(
            path=source_path,
            filename=archive_name,
            media_type="application/zip",
            headers={"Cache-Control": "private, no-store"},
        )

    archive_name_ascii = f"replay_{replay.id}.barodemo.zip"
    return StreamingResponse(
        iter_replay_download_archive(
            source_path,
            _replay_archive_member_name(replay),
        ),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{archive_name_ascii}"',
            "X-Content-Type-Options": "nosniff",
        },
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
