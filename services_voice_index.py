"""Persistent indexing of voice packets already stored in replay files.

The replay remains the audio source.  This module only walks the binary frame
stream and stores enough metadata to find a speaker's continuous fragments;
it never writes WAV/MP3 derivatives and never attributes a speaker by name.
"""

from __future__ import annotations

import gzip
import json
import os
import struct
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import BinaryIO

logger = logging.getLogger(__name__)


VOICE_MAP_MEMBER = "voice-map.json"
# v2 fixes snapshot entity widths for the v4 DemoRecorder and v7
# WorldDemoStreamer formats.  Existing ready rows must be rebuilt once so a
# segment produced by the old misaligned parser is never treated as valid.
VOICE_INDEX_VERSION = 2
VOICE_MERGE_GAP_MS = 1000
_MAGIC_DEMO = b"BDMO"
_MAGIC_ZIP = b"PK\x03\x04"


class VoiceIndexError(ValueError):
    """The replay cannot be safely indexed."""


class VoiceMappingError(VoiceIndexError):
    """A voice packet has no confirmed Steam/EOS identity."""


@dataclass(frozen=True)
class _DemoSource:
    stream: BinaryIO
    speaker_map: dict[int, str]


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    if size < 0:
        raise VoiceIndexError("negative replay read size")
    data = stream.read(size)
    if len(data) != size:
        raise VoiceIndexError("truncated replay frame")
    return data


def _u8(stream: BinaryIO) -> int:
    return _read_exact(stream, 1)[0]


def _u16(stream: BinaryIO) -> int:
    return struct.unpack("<H", _read_exact(stream, 2))[0]


def _u32(stream: BinaryIO) -> int:
    return struct.unpack("<I", _read_exact(stream, 4))[0]


def _read_string(stream: BinaryIO) -> str:
    size = _u16(stream)
    return _read_exact(stream, size).decode("utf-8", errors="replace")


def _skip(stream: BinaryIO, size: int) -> None:
    _read_exact(stream, size)


def _normalise_mapping(raw: object) -> dict[int, str]:
    if isinstance(raw, Mapping):
        entries = raw.items()
    elif isinstance(raw, list):
        entries = (
            (entry.get("speaker_id"), entry.get("steam_id"))
            for entry in raw
            if isinstance(entry, Mapping)
        )
    else:
        return {}

    result: dict[int, str] = {}
    for speaker_id, steam_id in entries:
        try:
            speaker = int(speaker_id)
        except (TypeError, ValueError):
            continue
        identity = str(steam_id or "").strip()
        if speaker > 0 and identity:
            result[speaker] = identity
    return result


def _mapping_from_json(data: bytes) -> dict[int, str]:
    try:
        return _normalise_mapping(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceIndexError(f"invalid {VOICE_MAP_MEMBER}: {exc}") from exc


@contextmanager
def _open_demo(path: str) -> Iterator[_DemoSource]:
    with open(path, "rb") as file:
        signature = file.read(4)

    if signature == _MAGIC_ZIP:
        archive = zipfile.ZipFile(path, "r")
        try:
            names = archive.namelist()
            demo_names = [
                name for name in names
                if name.lower().endswith(".barodemo")
            ]
            if not demo_names:
                raise VoiceIndexError("replay archive contains no .barodemo entry")
            mapping = {}
            if VOICE_MAP_MEMBER in names:
                mapping = _mapping_from_json(archive.read(VOICE_MAP_MEMBER))
            with archive.open(demo_names[0], "r") as entry:
                # ZipExtFile is a real buffered binary stream and keeps the
                # large replay out of RAM.
                yield _DemoSource(entry, mapping)
        finally:
            archive.close()
        return

    if signature[:2] == b"\x1f\x8b":
        with gzip.open(path, "rb") as stream:
            yield _DemoSource(stream, {})
        return

    if signature == _MAGIC_DEMO:
        with open(path, "rb") as stream:
            yield _DemoSource(stream, {})
        return

    raise VoiceIndexError("unsupported replay container")


def _skip_frame(stream: BinaryIO, frame: int, version: int) -> tuple[int, int] | None:
    """Consume one non-voice frame and return (time, speaker) for voice."""
    if frame == 0x10:  # geometry
        hulls = _u16(stream)
        _skip(stream, hulls * 20)
        walls = _u16(stream)
        _skip(stream, walls * 19)
        doors = _u16(stream)
        _skip(stream, doors * 21)
    elif frame == 0x11:  # world outline
        _skip(stream, 8)
        polygons = _u16(stream)
        for _ in range(polygons):
            _skip(stream, _u16(stream) * 8)
    elif frame == 0x01:  # snapshot
        _skip(stream, 4)
        count = _u16(stream)
        # v4-v6 writers emit id + x + y + flags + health = 8 bytes.
        # WorldDemoStreamer v7 adds oxygen = 9 bytes.  Using 6/7 here
        # desynchronises the stream after the first snapshot and turns
        # arbitrary payload bytes into fake frame types.
        _skip(stream, count * (9 if version >= 7 else 8))
        _skip(stream, _u16(stream) * 3)  # doors
        _skip(stream, _u16(stream) * 3)  # hull water
        _skip(stream, _u16(stream) * 10)  # submarine positions
    elif frame == 0x02:  # origin
        _skip(stream, 8)
    elif frame == 0x03:  # spawn
        _skip(stream, 6)
        _read_string(stream)
        _read_string(stream)
        _skip(stream, 1)
        if version >= 7:
            _read_string(stream)
    elif frame == 0x04:  # despawn
        _skip(stream, 6)
    elif frame == 0x05:  # chat
        _skip(stream, 7)
        _read_string(stream)
        _read_string(stream)
    elif frame == 0x06:  # event
        _skip(stream, 4 + 1 + 2 + 2 + 4 + 4 + 4)
        _read_string(stream)
    elif frame == 0x08:  # inventory
        count = _u16(stream)
        for _ in range(count):
            _skip(stream, 2)
            if version >= 7:
                owner_kind = _u8(stream)
                _skip(stream, 2)
            else:
                _skip(stream, 2)
                owner_kind = 1
            _read_string(stream)
            _skip(stream, 1)
            if version >= 7 and owner_kind in (0, 3):
                _skip(stream, 4)
    elif frame == 0x0A:  # hull assignment
        _skip(stream, 4)
        _skip(stream, _u16(stream) * 4)
    elif frame == 0xFF:
        return (_u32(stream), -1)
    elif frame in (0x07, 0x09):
        time_ms = _u32(stream)
        speaker_id = _u16(stream)
        if frame == 0x09:
            _skip(stream, 1)
        length = _u16(stream)
        _skip(stream, length)
        return (time_ms, speaker_id)
    else:
        raise VoiceIndexError(f"unsupported replay frame 0x{frame:02x}")
    return None


def _iter_voice_packets(stream: BinaryIO) -> Iterator[tuple[int, int]]:
    if _read_exact(stream, 4) != _MAGIC_DEMO:
        raise VoiceIndexError("invalid replay header")
    version = _u16(stream)
    _skip(stream, 2 + 1 + 8)
    for _ in range(4):
        _read_string(stream)
    _skip(stream, 4)

    while True:
        marker = stream.read(1)
        if not marker:
            raise VoiceIndexError("replay has no end frame")
        parsed = _skip_frame(stream, marker[0], version)
        if parsed is None:
            continue
        time_ms, speaker_id = parsed
        if speaker_id == -1:
            return
        yield time_ms, speaker_id


def group_voice_packets(
    packets: list[tuple[int, int]],
    *,
    merge_gap_ms: int = VOICE_MERGE_GAP_MS,
) -> list[dict]:
    """Merge packets per speaker; another speaker may talk over them."""
    segments: list[dict] = []
    last_by_speaker: dict[int, dict] = {}
    for time_ms, speaker_id in packets:
        previous = last_by_speaker.get(speaker_id)
        if (
            previous
            and time_ms - previous["end_ms"] <= merge_gap_ms
        ):
            previous["end_ms"] = time_ms
            previous["packet_count"] += 1
            continue
        segment = {
            "speaker_id": speaker_id,
            "start_ms": time_ms,
            "end_ms": time_ms,
            "packet_count": 1,
        }
        segments.append(segment)
        last_by_speaker[speaker_id] = segment
    return sorted(segments, key=lambda row: (row["start_ms"], row["speaker_id"]))


def extract_voice_segments(
    path: str,
    speaker_map: Mapping[int | str, str] | None = None,
) -> list[dict]:
    """Extract mapped voice fragments from a raw, gzip, or ZIP replay."""
    with _open_demo(path) as source:
        mapping = dict(source.speaker_map)
        if speaker_map:
            mapping.update(_normalise_mapping(speaker_map))
        packets = list(_iter_voice_packets(source.stream))

    if not packets:
        return []

    missing = sorted({speaker_id for _, speaker_id in packets if speaker_id not in mapping})
    if missing:
        raise VoiceMappingError(
            "confirmed Steam/EOS mapping is missing for speaker_id(s): "
            + ", ".join(str(value) for value in missing)
        )

    return [
        {
            "steam_id": mapping[row["speaker_id"]],
            **row,
        }
        for row in group_voice_packets(packets)
    ]


def _mapping_from_world_round(db, replay) -> dict[int, str]:
    """Return only mappings captured with the stream, never name matches."""
    if not getattr(replay, "stream_id", None):
        return {}
    from models import WorldRound

    row = (
        db.query(WorldRound)
        .filter(WorldRound.round_id == replay.stream_id)
        .first()
    )
    if not row:
        return {}

    mapping = _normalise_mapping(getattr(row, "character_steamids", None) or {})
    for character in getattr(row, "last_characters", None) or []:
        if not isinstance(character, Mapping):
            continue
        speaker_id = character.get("id")
        steam_id = character.get("steamid")
        if speaker_id is not None and steam_id:
            mapping.update(_normalise_mapping({speaker_id: steam_id}))
    return mapping


def _mapping_from_round_event(db, replay) -> dict[int, str]:
    """Accept an explicit mapping if a future uploader puts it in round data.

    Historical round snapshots normally contain no character ID.  In that
    case this intentionally returns nothing; matching a name here would make
    the archive look complete while silently poisoning it.
    """
    event_id = getattr(replay, "manual_round_event_id", None)
    if not event_id:
        return {}
    from models import RoundEvent

    event = db.query(RoundEvent).filter(RoundEvent.id == event_id).first()
    if not event:
        return {}
    mapping: dict[int, str] = {}
    for entry in event.players or []:
        if not isinstance(entry, Mapping):
            continue
        speaker_id = entry.get("speaker_id", entry.get("character_id"))
        steam_id = entry.get("steam_id", entry.get("player_steamid", entry.get("steamid")))
        if speaker_id is not None and steam_id:
            mapping.update(_normalise_mapping({speaker_id: steam_id}))
    return mapping


class VoiceIndexService:
    """Build and read the durable replay voice index."""

    def __init__(self, db):
        self.db = db

    def _mapping_for_replay(self, replay) -> dict[int, str]:
        return {
            **_mapping_from_world_round(self.db, replay),
            **_mapping_from_round_event(self.db, replay),
        }

    def index_replay(self, replay_id: int) -> dict:
        from models import ReplayVoiceSegment, RoundReplay

        replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if not replay:
            return {"status": "missing", "replay_id": replay_id}
        if (
            replay.voice_index_status == "ready"
            and replay.voice_index_version == VOICE_INDEX_VERSION
        ):
            return {"status": "ready", "replay_id": replay_id}

        replay.voice_index_status = "running"
        replay.voice_index_error = None
        self.db.commit()
        try:
            from services_replays import ReplayService

            path = ReplayService(self.db).get_file_path(replay)
            if not os.path.isfile(path):
                raise VoiceIndexError("replay file is missing")
            segments = extract_voice_segments(path, self._mapping_for_replay(replay))

            self.db.query(ReplayVoiceSegment).filter(
                ReplayVoiceSegment.replay_id == replay.id
            ).delete(synchronize_session=False)
            self.db.add_all(
                ReplayVoiceSegment(
                    replay_id=replay.id,
                    steam_id=segment["steam_id"],
                    speaker_id=segment["speaker_id"],
                    start_ms=segment["start_ms"],
                    end_ms=segment["end_ms"],
                    packet_count=segment["packet_count"],
                )
                for segment in segments
            )
            replay.voice_index_status = "ready"
            replay.voice_index_version = VOICE_INDEX_VERSION
            replay.voice_indexed_at = datetime.utcnow()
            replay.voice_index_error = None
            self.db.commit()
            return {"status": "ready", "replay_id": replay.id, "segments": len(segments)}
        except Exception as exc:
            self.db.rollback()
            replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
            if replay:
                replay.voice_index_status = "error"
                replay.voice_index_error = str(exc)[:1000]
                self.db.commit()
            logger.exception("Voice index failed for replay %s", replay_id)
            return {"status": "error", "replay_id": replay_id, "error": str(exc)}

    def get_segments_for_player(self, steam_id: str, page: int, page_size: int) -> tuple[list[dict], int]:
        from sqlalchemy import desc, func
        from models import ReplayVoiceSegment, RoundReplay

        query = (
            self.db.query(ReplayVoiceSegment, RoundReplay)
            .join(RoundReplay, RoundReplay.id == ReplayVoiceSegment.replay_id)
            .filter(ReplayVoiceSegment.steam_id == steam_id)
        )
        total = query.count()
        rows = (
            query.order_by(
                desc(func.coalesce(RoundReplay.recorded_at, RoundReplay.uploaded_at)),
                desc(ReplayVoiceSegment.start_ms),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [
            {
                "id": segment.id,
                "replay_id": replay.id,
                "replay_filename": replay.filename,
                "server_name": replay.server_name,
                "recorded_at": replay.recorded_at.isoformat() if replay.recorded_at else None,
                "uploaded_at": replay.uploaded_at.isoformat() if replay.uploaded_at else None,
                "speaker_id": segment.speaker_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "duration_ms": max(0, segment.end_ms - segment.start_ms),
                "packet_count": segment.packet_count,
                "replay_url": f"/replays/{replay.id}?voice_speaker={segment.speaker_id}&voice_at={segment.start_ms}",
            }
            for segment, replay in rows
        ], total


def _index_replay_task(replay_id: int) -> dict:
    from database import SessionLocal

    db = SessionLocal()
    try:
        return VoiceIndexService(db).index_replay(replay_id)
    finally:
        db.close()


def queue_replay_voice_index(replay_id: int) -> str | None:
    """Queue one replay without doing binary work in the HTTP request."""
    try:
        from task_scheduler import Priority, scheduler

        return scheduler.submit(
            _index_replay_task,
            priority=Priority.BACKGROUND,
            task_id=f"replay_voice_index_{replay_id}_v{VOICE_INDEX_VERSION}",
            args=(replay_id,),
            timeout=300.0,
        )
    except Exception:
        logger.exception("Could not queue voice index for replay %s", replay_id)
        return None


def task_index_pending_voice_replays(limit: int = 8) -> dict:
    """Queue retained pending/error replays; safe to run repeatedly."""
    from database import SessionLocal
    from models import RoundReplay
    from services_replays import get_retention_days
    from sqlalchemy import or_

    db = SessionLocal()
    try:
        if get_active_voice_index_run(db):
            return {"queued": 0, "candidates": 0, "skipped": "manual_run_active"}
        cutoff = datetime.utcnow() - timedelta(days=get_retention_days(db))
        rows = (
            db.query(RoundReplay)
            .filter(
                RoundReplay.uploaded_at >= cutoff,
                RoundReplay.has_voice == 1,
                or_(
                    RoundReplay.voice_index_status != "ready",
                    RoundReplay.voice_index_version != VOICE_INDEX_VERSION,
                    RoundReplay.voice_index_status.is_(None),
                ),
            )
            .order_by(RoundReplay.uploaded_at.asc())
            .limit(limit)
            .all()
        )
        queued = [queue_replay_voice_index(row.id) for row in rows]
        return {"queued": len([task for task in queued if task]), "candidates": len(rows)}
    finally:
        db.close()


VOICE_RUN_LOG_RETENTION_DAYS = 7
VOICE_RUN_STALE_SECONDS = 3600


def _voice_run_log(db, run, *, status: str, message: str, replay_id: int | None = None, level: str = "info"):
    from models import VoiceIndexRunLog

    db.add(
        VoiceIndexRunLog(
            run_id=run.id,
            replay_id=replay_id,
            level=level,
            status=status,
            message=str(message)[:1000],
        )
    )


def _mark_stale_voice_run(db, run):
    now = datetime.utcnow()
    heartbeat = run.heartbeat_at or run.created_at
    if not heartbeat or (now - heartbeat).total_seconds() <= VOICE_RUN_STALE_SECONDS:
        return False
    run.status = "failed"
    run.error = "Задача не обновляла heartbeat больше часа; помечена как зависшая."
    run.finished_at = now
    _voice_run_log(db, run, status="failed", level="error", message=run.error)
    db.commit()
    return True


def get_active_voice_index_run(db):
    from models import VoiceIndexRun

    run = (
        db.query(VoiceIndexRun)
        .filter(VoiceIndexRun.status.in_(["pending", "running"]))
        .order_by(VoiceIndexRun.created_at.desc())
        .first()
    )
    if run and _mark_stale_voice_run(db, run):
        return None
    return run


def _voice_run_dict(run) -> dict:
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "total": run.total,
        "processed": run.processed,
        "ready_count": run.ready_count,
        "error_count": run.error_count,
        "last_replay_id": run.last_replay_id,
        "last_message": run.last_message,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
    }


def get_voice_index_status(db) -> dict:
    from models import VoiceIndexRun

    active = get_active_voice_index_run(db)
    latest = (
        db.query(VoiceIndexRun)
        .order_by(VoiceIndexRun.created_at.desc())
        .first()
    )
    return {
        "active": _voice_run_dict(active),
        "latest": _voice_run_dict(latest),
        "log_retention_days": VOICE_RUN_LOG_RETENTION_DAYS,
        "index_version": VOICE_INDEX_VERSION,
    }


def task_run_voice_backfill(run_id: int, session_factory=None) -> dict:
    """Index all retained voice replays for one operator-visible run."""
    from database import SessionLocal
    from models import RoundReplay, VoiceIndexRun, VoiceIndexRunLog
    from services_replays import get_retention_days
    from sqlalchemy import or_

    factory = session_factory or SessionLocal
    db = factory()
    run = None
    try:
        run = db.query(VoiceIndexRun).filter(VoiceIndexRun.id == run_id).first()
        if not run:
            return {"status": "missing", "run_id": run_id}
        if run.status not in ("pending", "running"):
            return {"status": run.status, "run_id": run_id}

        now = datetime.utcnow()
        run.status = "running"
        run.started_at = run.started_at or now
        run.heartbeat_at = now
        db.query(VoiceIndexRunLog).filter(
            VoiceIndexRunLog.created_at < now - timedelta(days=VOICE_RUN_LOG_RETENTION_DAYS)
        ).delete(synchronize_session=False)

        cutoff = now - timedelta(days=get_retention_days(db))
        replay_ids = [
            replay_id
            for (replay_id,) in db.query(RoundReplay.id)
            .filter(
                RoundReplay.uploaded_at >= cutoff,
                RoundReplay.has_voice == 1,
                or_(
                    RoundReplay.voice_index_status != "ready",
                    RoundReplay.voice_index_version != VOICE_INDEX_VERSION,
                    RoundReplay.voice_index_status.is_(None),
                ),
            )
            .order_by(RoundReplay.uploaded_at.asc())
            .all()
        ]
        run.total = len(replay_ids)
        run.last_message = f"Найдено реплеев: {run.total}"
        db.commit()

        service = VoiceIndexService(db)
        for replay_id in replay_ids:
            result = service.index_replay(replay_id)
            status = result.get("status", "error")
            is_error = status != "ready"
            run.processed += 1
            run.last_replay_id = replay_id
            run.last_message = (
                f"Реплей #{replay_id}: {status}"
                + (f" — {result.get('error')}" if result.get("error") else "")
            )
            run.heartbeat_at = datetime.utcnow()
            if is_error:
                run.error_count += 1
            else:
                run.ready_count += 1
            _voice_run_log(
                db,
                run,
                replay_id=replay_id,
                status=status,
                level="error" if is_error else "info",
                message=run.last_message,
            )
            db.commit()

        run.status = "completed"
        run.finished_at = datetime.utcnow()
        run.heartbeat_at = run.finished_at
        run.last_message = "Индексация завершена"
        db.commit()
        return _voice_run_dict(run)
    except Exception as exc:
        db.rollback()
        run = db.query(VoiceIndexRun).filter(VoiceIndexRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.error = str(exc)[:1000]
            run.finished_at = datetime.utcnow()
            _voice_run_log(db, run, status="failed", level="error", message=run.error)
            db.commit()
        logger.exception("Voice index backfill failed for run %s", run_id)
        return {"status": "failed", "run_id": run_id, "error": str(exc)}
    finally:
        if session_factory is None:
            db.close()


def queue_voice_index_backfill(run_id: int) -> str | None:
    try:
        from task_scheduler import Priority, scheduler

        return scheduler.submit(
            task_run_voice_backfill,
            priority=Priority.BACKGROUND,
            task_id=f"voice_index_backfill_run_{run_id}",
            args=(run_id,),
            timeout=None,
        )
    except Exception:
        logger.exception("Could not queue voice index backfill %s", run_id)
        return None


def list_voice_index_run_logs(db, run_id: int, page: int, page_size: int) -> tuple[list[dict], int]:
    from models import VoiceIndexRunLog

    query = db.query(VoiceIndexRunLog).filter(VoiceIndexRunLog.run_id == run_id)
    total = query.count()
    rows = (
        query.order_by(VoiceIndexRunLog.created_at.desc(), VoiceIndexRunLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "replay_id": row.replay_id,
            "level": row.level,
            "status": row.status,
            "message": row.message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ], total
