"""Replay-independent voice profile extraction primitives.

The module starts with format-neutral packet and selection helpers. Replay I/O,
database orchestration, and the external Concentus decoder are layered on top
so the deterministic parts remain cheap to test.
"""

from __future__ import annotations

import datetime as _datetime
import audioop
import gzip
import hashlib
import math
import os
import json
import subprocess
import struct
import tempfile
import zipfile
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Mapping, Sequence
from urllib.request import urlopen


VPK1_MAGIC = b"VPK1"
VPK1_VERSION = 1
NDVC_MAGIC = b"NDVC"
NDVC_VERSION = 2
DEMO_MAGIC = b"BDMO"
VOICE_MAP_MEMBER = "voice-map.json"
MAX_OPUS_PACKET_SIZE = 40
MAX_NWDEMO_OPUS_PACKET_SIZE = 1275
VOICE_MERGE_GAP_MS = 1000
VOICE_PROFILE_LIMIT = 11
VOICE_PROFILE_EARLY_COUNT = 3
VOICE_PROFILE_MIDDLE_COUNT = 3
VOICE_PROFILE_LATEST_COUNT = 5
VOICE_PROFILE_ARCHIVE_VERSION = 2
NWDEMO_PROFILE_ARCHIVE_VERSION = 3
MFCC_V1_ALGORITHM = "mfcc-v1"
MFCC_V1_COEFFICIENTS = 13
MFCC_V1_FILTERS = 26
MFCC_V1_MIN_QUALITY_MS = 3_000
VOICE_PROFILE_SCANNER_SETTING = "voice_profiles_scanner_enabled"
VOICEPRINT_WORKER_SETTING = "voiceprint_worker_enabled"
VOICE_PROFILE_STALE_AFTER_SECONDS = 15 * 60
VOICE_PROFILE_MAX_ATTEMPTS = 3
ONNX_VOICEPRINT_ALGORITHM = "redimnet-b2-onnx-v1"
ONNX_VOICEPRINT_DIMENSIONS = 192
ONNX_VOICEPRINT_SAMPLE_RATE = 16_000
ONNX_VOICEPRINT_MAX_DURATION_MS = 30_000
VOICEPRINT_MIN_SAMPLE_COUNT = 3
VOICEPRINT_MIN_DURATION_MS = 15_000
ONNX_VOICEPRINT_MODEL_FILENAME = "redimnet_b2_vox2.onnx"
ONNX_VOICEPRINT_MODEL_RELEASE_URL = (
    "https://github.com/icebergdemm/NexusData/releases/download/"
    "voiceprint-redimnet-b2-onnx-v1/redimnet_b2_vox2.onnx"
)
ONNX_VOICEPRINT_MODEL_SHA256 = "a7586b34c8db1bbf32c64efc2b269be54f66787e50320c4472614bbe95218a81"


@dataclass(frozen=True)
class VoicePacket:
    time_ms: int
    speaker_id: int
    data: bytes
    radio: bool = False


@dataclass(frozen=True)
class NwdemoVoicePacket:
    """A NewWorldStreamer packet keyed solely by its authoritative Steam64."""

    time_ms: int
    steam_id: str
    data: bytes
    radio: bool = False


@dataclass(frozen=True)
class ReplayVoiceExtraction:
    version: int
    packets: list[VoicePacket]
    mapping: dict[int, str]
    names_by_speaker: dict[int, set[str]]
    complete: bool
    warnings: list[str]


@dataclass(frozen=True)
class ReplayVoiceSegmentExtraction:
    version: int
    segments: list[dict]
    mapping: dict[int, str]
    names_by_speaker: dict[int, set[str]]
    complete: bool
    warnings: list[str]


class VoiceProfileParseError(ValueError):
    pass


def voice_profile_scanner_enabled(db) -> bool:
    """Read the persisted gate for the small periodic nwdemo scanner."""
    from models import AppSetting

    setting = db.query(AppSetting).filter(AppSetting.key == VOICE_PROFILE_SCANNER_SETTING).first()
    return bool(setting and str(setting.value or "").strip().casefold() in {"1", "true", "yes", "on"})


def set_voice_profile_scanner_enabled(db, enabled: bool) -> bool:
    """Persist the scanner gate; the worker observes it on its next minute tick."""
    from models import AppSetting

    setting = db.query(AppSetting).filter(AppSetting.key == VOICE_PROFILE_SCANNER_SETTING).first()
    if setting is None:
        setting = AppSetting(key=VOICE_PROFILE_SCANNER_SETTING)
        db.add(setting)
    setting.value = "true" if enabled else "false"
    db.flush()
    return bool(enabled)


def voiceprint_worker_enabled(db) -> bool:
    """Read the independent ONNX backfill gate; it defaults to on."""
    from models import AppSetting

    setting = db.query(AppSetting).filter(AppSetting.key == VOICEPRINT_WORKER_SETTING).first()
    return setting is None or str(setting.value or "").strip().casefold() in {"1", "true", "yes", "on"}


def set_voiceprint_worker_enabled(db, enabled: bool) -> bool:
    """Persist the ONNX backfill gate without pausing clip extraction."""
    from models import AppSetting

    setting = db.query(AppSetting).filter(AppSetting.key == VOICEPRINT_WORKER_SETTING).first()
    if setting is None:
        setting = AppSetting(key=VOICEPRINT_WORKER_SETTING)
        db.add(setting)
    setting.value = "true" if enabled else "false"
    db.flush()
    return bool(enabled)


def recover_stale_nwdemo_processing(db, *, now: _datetime.datetime | None = None) -> dict[str, int]:
    """Requeue or fail nwdemo rows abandoned by a dead worker process."""
    from models import RoundReplay
    from sqlalchemy import or_

    current = now or _datetime.datetime.utcnow()
    cutoff = current - _datetime.timedelta(seconds=VOICE_PROFILE_STALE_AFTER_SECONDS)
    stale = db.query(RoundReplay).filter(
        or_(
            RoundReplay.replay_format == "nwdemo-v2",
            RoundReplay.filename.ilike("%.nwdemo"),
            RoundReplay.stored_filename.ilike("%.nwdemo"),
        ),
        RoundReplay.voice_profile_state == "processing",
        or_(
            RoundReplay.voice_profile_heartbeat_at.is_(None),
            RoundReplay.voice_profile_heartbeat_at < cutoff,
        ),
    ).all()
    requeued = 0
    failed = 0
    for replay in stale:
        attempts = int(replay.voice_profile_attempts or 0)
        previous_progress = replay.voice_profile_progress if isinstance(replay.voice_profile_progress, dict) else {}
        is_voiceprint = previous_progress.get("mode") == "voiceprint"
        replay.voice_profile_heartbeat_at = None
        replay.voice_profile_started_at = None
        replay.voice_profile_progress = {
            "phase": "recovery",
            "percent": 0,
            "recovered_at": current.isoformat(),
        }
        if attempts >= VOICE_PROFILE_MAX_ATTEMPTS:
            replay.voice_profile_state = "failed"
            replay.voice_profile_error = (
                f"STALE_PROCESSING: exceeded {VOICE_PROFILE_MAX_ATTEMPTS} attempts"
            )
            failed += 1
        else:
            replay.voice_profile_state = "ready" if is_voiceprint else "pending"
            replay.voice_profile_error = (
                f"REQUEUED_STALE: heartbeat older than {VOICE_PROFILE_STALE_AFTER_SECONDS // 60} minutes"
            )
            requeued += 1
    if stale:
        db.commit()
    return {"requeued": requeued, "failed": failed}


def heartbeat_nwdemo_replay(
    db,
    replay,
    *,
    phase: str,
    percent: int,
    now: _datetime.datetime | None = None,
    **metrics,
) -> bool:
    """Persist bounded scanner progress at most once every five seconds."""
    current = now or _datetime.datetime.utcnow()
    previous = replay.voice_profile_heartbeat_at
    if previous is not None and (current - previous).total_seconds() < 5:
        return False
    progress = dict(replay.voice_profile_progress or {})
    progress.update({"phase": phase, "percent": max(0, min(100, int(percent)))})
    progress.update({key: value for key, value in metrics.items() if value is not None})
    replay.voice_profile_heartbeat_at = current
    replay.voice_profile_progress = progress
    db.commit()
    return True


def voice_activity_is_complete(
    speaker_ids: Iterable[int],
    segment_speaker_ids: Iterable[int],
) -> bool:
    """Return whether every indexed speaker has at least one segment row."""
    speakers = {int(speaker_id) for speaker_id in speaker_ids}
    segments = {int(speaker_id) for speaker_id in segment_speaker_ids}
    return not speakers or speakers.issubset(segments)


def _candidate_sort_key(candidate: Mapping[str, object]) -> tuple:
    recorded_at = candidate.get("recorded_at")
    if recorded_at is None:
        recorded_at = _datetime.datetime.min
    return (
        recorded_at,
        int(candidate.get("replay_id") or 0),
        int(candidate.get("start_ms") or 0),
        str(candidate.get("candidate_key") or ""),
    )


def _select_evenly(candidates: Sequence[Mapping[str, object]], count: int) -> list[Mapping[str, object]]:
    if count <= 0 or not candidates:
        return []
    if len(candidates) <= count:
        return list(candidates)
    if count == 1:
        return [candidates[len(candidates) // 2]]

    selected: list[Mapping[str, object]] = []
    used: set[int] = set()
    for index in range(count):
        position = round(index * (len(candidates) - 1) / (count - 1))
        if position in used:
            continue
        used.add(position)
        selected.append(candidates[position])
    return selected


def select_profile_candidates(
    candidates: Iterable[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Select the deterministic 3-earliest/3-middle/5-latest profile."""
    ordered = sorted(candidates, key=_candidate_sort_key)
    if len(ordered) <= VOICE_PROFILE_LIMIT:
        return ordered

    early = ordered[:VOICE_PROFILE_EARLY_COUNT]
    latest = ordered[-VOICE_PROFILE_LATEST_COUNT:]
    middle_pool = ordered[VOICE_PROFILE_EARLY_COUNT:-VOICE_PROFILE_LATEST_COUNT]
    middle = _select_evenly(middle_pool, VOICE_PROFILE_MIDDLE_COUNT)
    return [*early, *middle, *latest]


def steam2_to_steam64(value: str) -> str | None:
    """Convert the NewWorldStreamer Steam2 identity without any name lookup."""
    parts = value.strip().upper().split(":")
    if len(parts) != 3 or not parts[0].startswith("STEAM_"):
        return None
    try:
        universe = int(parts[0].split("_", 1)[1])
        auth = int(parts[1])
        account = int(parts[2])
    except ValueError:
        return None
    if universe not in (0, 1) or auth not in (0, 1) or account < 0:
        return None
    return str(76561197960265728 + auth + account * 2)


def group_voice_packets(
    packets: Sequence[VoicePacket],
    *,
    merge_gap_ms: int = VOICE_MERGE_GAP_MS,
) -> list[dict]:
    """Group packets by speaker and channel while preserving their order."""
    segments: list[dict] = []
    last_by_source: dict[tuple[int, bool], dict] = {}

    for packet in packets:
        if packet.speaker_id == 0:
            continue
        source = (packet.speaker_id, packet.radio)
        previous = last_by_source.get(source)
        if previous and packet.time_ms - previous["end_ms"] <= merge_gap_ms:
            previous["end_ms"] = packet.time_ms
            previous["packet_count"] += 1
            previous["radio_packet_count"] += int(packet.radio)
            previous["packet_records"].append(packet)
            continue

        segment = {
            "speaker_id": packet.speaker_id,
            "start_ms": packet.time_ms,
            "end_ms": packet.time_ms,
            "packet_count": 1,
            "radio_packet_count": int(packet.radio),
            "packet_records": [packet],
        }
        segments.append(segment)
        last_by_source[source] = segment

    return sorted(segments, key=lambda row: (row["start_ms"], row["speaker_id"]))


def group_nwdemo_voice_packets(
    packets: Sequence[NwdemoVoicePacket],
    *,
    merge_gap_ms: int = VOICE_MERGE_GAP_MS,
) -> list[dict]:
    """Group direct Steam64 nwdemo packets by identity and radio channel."""
    segments: list[dict] = []
    active: dict[tuple[str, bool], dict] = {}
    for packet in sorted(packets, key=lambda row: (row.time_ms, row.steam_id, row.radio)):
        source = (packet.steam_id, packet.radio)
        previous = active.get(source)
        if previous is not None and packet.time_ms - previous["end_ms"] <= merge_gap_ms:
            previous["end_ms"] = packet.time_ms
            previous["packet_count"] += 1
            previous["packet_records"].append(packet)
            continue
        segment = {
            "steam_id": packet.steam_id,
            "radio": packet.radio,
            "start_ms": packet.time_ms,
            "end_ms": packet.time_ms,
            "packet_count": 1,
            "radio_packet_count": int(packet.radio),
            "packet_records": [packet],
        }
        segments.append(segment)
        active[source] = segment
    return sorted(segments, key=lambda row: (row["start_ms"], row["steam_id"], row["radio"]))


def build_replay_voice_segment_rows(
    grouped: Sequence[Mapping[str, int]],
    *,
    mapping: Mapping[int, str],
    speaker_rows: Mapping[int, object],
) -> list[dict]:
    """Attach an optional resolved identity without dropping activity."""
    return [{
        "speaker_id": int(segment["speaker_id"]),
        "steam_id": mapping.get(int(segment["speaker_id"])),
        "player_id": getattr(speaker_rows.get(int(segment["speaker_id"])), "player_id", None),
        "start_ms": int(segment["start_ms"]),
        "end_ms": int(segment["end_ms"]),
        "packet_count": int(segment["packet_count"]),
        "radio_packet_count": int(segment["radio_packet_count"]),
    } for segment in grouped]


def _read_exact_stream(stream: BinaryIO, size: int) -> bytes:
    if size < 0:
        raise VoiceProfileParseError("negative replay read size")
    data = stream.read(size)
    if len(data) != size:
        raise VoiceProfileParseError("truncated replay frame")
    return data


def _u8(stream: BinaryIO) -> int:
    return _read_exact_stream(stream, 1)[0]


def _u16(stream: BinaryIO) -> int:
    return struct.unpack("<H", _read_exact_stream(stream, 2))[0]


def _u32(stream: BinaryIO) -> int:
    return struct.unpack("<I", _read_exact_stream(stream, 4))[0]


def _read_string(stream: BinaryIO) -> str:
    size = _u16(stream)
    return _read_exact_stream(stream, size).decode("utf-8", errors="replace")


def _skip(stream: BinaryIO, size: int) -> None:
    _read_exact_stream(stream, size)


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


@contextmanager
def _open_replay(path: str | os.PathLike[str]) -> Iterator[tuple[BinaryIO, dict[int, str]]]:
    path = str(path)
    source_path = Path(path)

    def sidecar_mapping() -> dict[int, str]:
        for sidecar in (
            source_path.with_name(source_path.name + ".voice-map.json"),
            source_path.with_suffix(".voice-map.json"),
        ):
            if not sidecar.is_file():
                continue
            try:
                return _normalise_mapping(json.loads(sidecar.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VoiceProfileParseError(f"invalid voice-map sidecar {sidecar}: {exc}") from exc
        return {}

    with open(path, "rb") as file:
        signature = file.read(4)

    if signature == b"PK\x03\x04":
        archive = zipfile.ZipFile(path, "r")
        try:
            demo_names = [name for name in archive.namelist() if name.lower().endswith(".barodemo")]
            if not demo_names:
                raise VoiceProfileParseError("replay archive contains no .barodemo entry")
            mapping = {}
            if VOICE_MAP_MEMBER in archive.namelist():
                try:
                    mapping = _normalise_mapping(json.loads(archive.read(VOICE_MAP_MEMBER).decode("utf-8")))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise VoiceProfileParseError(f"invalid {VOICE_MAP_MEMBER}: {exc}") from exc
            with archive.open(demo_names[0], "r") as entry:
                yield entry, mapping
        finally:
            archive.close()
        return

    if signature[:2] == b"\x1f\x8b":
        with gzip.open(path, "rb") as stream:
            yield stream, sidecar_mapping()
        return

    if signature == DEMO_MAGIC:
        with open(path, "rb") as stream:
            yield stream, sidecar_mapping()
        return

    raise VoiceProfileParseError("unsupported replay container")


@dataclass(frozen=True)
class _ReplayFrame:
    kind: str
    time_ms: int = 0
    speaker_id: int | None = None
    character_name: str | None = None
    voice_data: bytes | None = None
    voice_radio: bool = False


def _skip_frame(stream: BinaryIO, frame: int, version: int) -> _ReplayFrame | None:
    if frame == 0x10:  # geometry
        _skip(stream, _u16(stream) * 20)
        _skip(stream, _u16(stream) * 19)
        _skip(stream, _u16(stream) * 21)
    elif frame == 0x11:  # world outline
        _skip(stream, 8)
        for _ in range(_u16(stream)):
            _skip(stream, _u16(stream) * 8)
    elif frame == 0x01:  # snapshot
        _skip(stream, 4)
        _skip(stream, _u16(stream) * (9 if version >= 7 else 8))
        _skip(stream, _u16(stream) * 3)
        _skip(stream, _u16(stream) * 3)
        _skip(stream, _u16(stream) * 10)
    elif frame == 0x02:  # origin
        _skip(stream, 8)
    elif frame == 0x03:  # spawn
        time_ms = _u32(stream)
        speaker_id = _u16(stream)
        _read_string(stream)
        character_name = _read_string(stream)
        _skip(stream, 1)
        if version >= 7:
            _read_string(stream)
        return _ReplayFrame("spawn", time_ms, speaker_id, character_name)
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
        for _ in range(_u16(stream)):
            _skip(stream, 2)
            owner_kind = 1
            if version >= 7:
                owner_kind = _u8(stream)
                _skip(stream, 2)
            else:
                _skip(stream, 2)
            _read_string(stream)
            _skip(stream, 1)
            if version >= 7 and owner_kind in (0, 3):
                _skip(stream, 4)
    elif frame == 0x0A:  # hull assignment
        _skip(stream, 4)
        _skip(stream, _u16(stream) * 4)
    elif frame == 0xFF:
        return _ReplayFrame("end", _u32(stream))
    elif frame in (0x07, 0x09):
        time_ms = _u32(stream)
        speaker_id = _u16(stream)
        radio = bool(_u8(stream)) if frame == 0x09 else False
        length = _u16(stream)
        payload = _read_exact_stream(stream, length)
        if not 0 < length <= MAX_OPUS_PACKET_SIZE:
            return _ReplayFrame("invalid_voice", time_ms, speaker_id)
        return _ReplayFrame("voice", time_ms, speaker_id, voice_data=payload, voice_radio=radio)
    else:
        raise VoiceProfileParseError(f"unsupported replay frame 0x{frame:02x}")
    return None


def _iter_replay_frames(stream: BinaryIO) -> tuple[int, Iterator[_ReplayFrame]]:
    if _read_exact_stream(stream, 4) != DEMO_MAGIC:
        raise VoiceProfileParseError("invalid replay header")
    version = _u16(stream)
    _skip(stream, 2 + 1 + 8)
    for _ in range(4):
        _read_string(stream)
    _skip(stream, 4)

    def frames() -> Iterator[_ReplayFrame]:
        while True:
            marker = stream.read(1)
            if not marker:
                return
            parsed = _skip_frame(stream, marker[0], version)
            if parsed is not None:
                yield parsed
                if parsed.kind == "end":
                    return

    return version, frames()


def extract_voice_packet_records(path: str | os.PathLike[str]) -> ReplayVoiceExtraction:
    """Extract voice packets, observed names, and embedded identity mapping."""
    path = Path(path)
    if path.suffix.lower() == ".nwdemo":
        packets, mapping = _read_nwdemo_voice_packets(path)
        return ReplayVoiceExtraction(7, packets, mapping, {}, True, [])
    with _open_replay(path) as (stream, mapping):
        version, frames = _iter_replay_frames(stream)
        packets: list[VoicePacket] = []
        names_by_character: dict[int, set[str]] = {}
        seen_speakers: set[int] = set()
        complete = False
        warnings: list[str] = []
        for frame in frames:
            if frame.kind == "spawn" and frame.speaker_id is not None:
                if frame.character_name and frame.speaker_id > 0:
                    names_by_character.setdefault(frame.speaker_id, set()).add(frame.character_name)
                continue
            if frame.kind == "invalid_voice":
                warnings.append(f"invalid Opus packet for speaker_id {frame.speaker_id}")
                continue
            if frame.kind == "end":
                complete = True
                break
            if frame.kind != "voice" or frame.speaker_id in (None, 0) or frame.voice_data is None:
                continue
            packets.append(VoicePacket(frame.time_ms, frame.speaker_id, frame.voice_data, frame.voice_radio))
            seen_speakers.add(frame.speaker_id)

        if not complete:
            warnings.append("replay ended before FRAME_END")
        names_by_speaker = {
            speaker_id: names_by_character[speaker_id]
            for speaker_id in seen_speakers
            if speaker_id in names_by_character
        }
        return ReplayVoiceExtraction(
            version=version,
            packets=packets,
            mapping=mapping,
            names_by_speaker=names_by_speaker,
            complete=complete,
            warnings=warnings,
        )


def extract_voice_segment_metadata(path: str | os.PathLike[str]) -> ReplayVoiceSegmentExtraction:
    """Extract only voice segment metadata, without retaining Opus payloads."""
    path = Path(path)
    if path.suffix.lower() == ".nwdemo":
        # nwdemo stores voice independently from world.bin. The legacy BDMO
        # frame reader must not reject this valid archive just because it has
        # no .barodemo member.
        return _extract_nwdemo_voice_metadata(path)
    with _open_replay(path) as (stream, mapping):
        version, frames = _iter_replay_frames(stream)
        segments: list[dict] = []
        active_by_source: dict[tuple[int, bool], dict] = {}
        names_by_character: dict[int, set[str]] = {}
        seen_speakers: set[int] = set()
        complete = False
        warnings: list[str] = []
        for frame in frames:
            if frame.kind == "spawn" and frame.speaker_id is not None:
                if frame.character_name and frame.speaker_id > 0:
                    names_by_character.setdefault(frame.speaker_id, set()).add(frame.character_name)
                continue
            if frame.kind == "invalid_voice":
                warnings.append(f"invalid Opus packet for speaker_id {frame.speaker_id}")
                continue
            if frame.kind == "end":
                complete = True
                break
            if frame.kind != "voice" or frame.speaker_id in (None, 0) or frame.voice_data is None:
                continue

            speaker_id = frame.speaker_id
            seen_speakers.add(speaker_id)
            source = (speaker_id, frame.voice_radio)
            previous = active_by_source.get(source)
            if previous and frame.time_ms - previous["end_ms"] <= VOICE_MERGE_GAP_MS:
                previous["end_ms"] = frame.time_ms
                previous["packet_count"] += 1
                previous["radio_packet_count"] += int(frame.voice_radio)
                continue

            segment = {
                "speaker_id": speaker_id,
                "start_ms": frame.time_ms,
                "end_ms": frame.time_ms,
                "packet_count": 1,
                "radio_packet_count": int(frame.voice_radio),
            }
            segments.append(segment)
            active_by_source[source] = segment

        if not complete:
            warnings.append("replay ended before FRAME_END")
        names_by_speaker = {
            speaker_id: names_by_character[speaker_id]
            for speaker_id in seen_speakers
            if speaker_id in names_by_character
        }
        return ReplayVoiceSegmentExtraction(
            version=version,
            segments=sorted(segments, key=lambda row: (row["start_ms"], row["speaker_id"])),
            mapping=mapping,
            names_by_speaker=names_by_speaker,
            complete=complete,
            warnings=warnings,
        )


def _read_dotnet_string(stream: BinaryIO) -> str:
    value = 0
    shift = 0
    while True:
        raw = stream.read(1)
        if not raw:
            raise VoiceProfileParseError("truncated nwdemo voice string")
        byte = raw[0]
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            break
        shift += 7
        if shift > 28:
            raise VoiceProfileParseError("invalid nwdemo voice string length")
    data = stream.read(value)
    if len(data) != value:
        raise VoiceProfileParseError("truncated nwdemo voice string data")
    return data.decode("utf-8", errors="replace")


def read_nwdemo_voice_packets(
    path: str | os.PathLike[str],
    *,
    allow_empty_world: bool = False,
) -> list[NwdemoVoicePacket]:
    """Read the version-gated nwdemo-v2 layout, never inferring identity.

    This compatibility helper intentionally materializes a list.  The worker
    uses :func:`iter_nwdemo_voice_packets` instead for production-sized files.
    """
    return list(iter_nwdemo_voice_packets(path, allow_empty_world=allow_empty_world))


def iter_nwdemo_voice_packets(
    path: str | os.PathLike[str],
    *,
    allow_empty_world: bool = False,
) -> Iterator[NwdemoVoicePacket]:
    """Yield nwdemo-v2 packets once, preserving and validating writer order."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            if not {"manifest.json", "world.bin", "voice.bin"}.issubset(archive.namelist()):
                raise VoiceProfileParseError("nwdemo archive has no required member")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if (
                not isinstance(manifest, Mapping)
                or manifest.get("format") != "nwdemo-v2"
                or manifest.get("world_format") != "BDMO"
                or manifest.get("world_version") != 7
            ):
                raise VoiceProfileParseError("unsupported nwdemo format")
            with archive.open("world.bin", "r") as world:
                world_magic = world.read(4)
            if world_magic != DEMO_MAGIC and not (allow_empty_world and not world_magic):
                raise VoiceProfileParseError("invalid nwdemo world.bin header")
            previous_time_ms: int | None = None
            with archive.open("voice.bin", "r") as stream:
                while True:
                    header = stream.read(6)
                    if not header:
                        break
                    if len(header) != 6:
                        raise VoiceProfileParseError("truncated nwdemo voice packet")
                    time_ms, _local_speaker_id = struct.unpack("<IH", header)
                    steam_id = steam2_to_steam64(_read_dotnet_string(stream))
                    radio_raw = _read_exact_stream(stream, 1)[0]
                    if radio_raw not in (0, 1):
                        raise VoiceProfileParseError("invalid nwdemo radio flag")
                    length = struct.unpack("<H", _read_exact_stream(stream, 2))[0]
                    payload = _read_exact_stream(stream, length)
                    if not 0 < length <= MAX_NWDEMO_OPUS_PACKET_SIZE:
                        raise VoiceProfileParseError("invalid nwdemo Opus payload length")
                    if previous_time_ms is not None and time_ms < previous_time_ms:
                        raise VoiceProfileParseError("nwdemo voice packets are out of chronological order")
                    previous_time_ms = time_ms
                    if steam_id is None:
                        continue
                    yield NwdemoVoicePacket(time_ms, steam_id, payload, bool(radio_raw))
    except KeyError as exc:
        raise VoiceProfileParseError("nwdemo archive has no required member") from exc
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceProfileParseError(f"invalid nwdemo voice archive: {exc}") from exc


def collect_nwdemo_voice_stats(
    path: str | os.PathLike[str],
    *,
    allow_empty_world: bool = False,
    progress_callback=None,
) -> dict[tuple[str, bool], dict[str, int]]:
    """Collect bounded channel metadata without retaining Opus payloads."""
    stats: dict[tuple[str, bool], dict[str, int]] = {}
    bytes_read = 0
    packets_read = 0
    for packet in iter_nwdemo_voice_packets(path, allow_empty_world=allow_empty_world):
        packets_read += 1
        bytes_read += len(packet.data)
        key = (packet.steam_id, packet.radio)
        row = stats.setdefault(key, {
            "first_ms": packet.time_ms,
            "last_ms": packet.time_ms,
            "packet_count": 0,
            "payload_bytes": 0,
        })
        row["last_ms"] = packet.time_ms
        row["packet_count"] += 1
        row["payload_bytes"] += len(packet.data)
        if progress_callback is not None and packets_read % 5000 == 0:
            progress_callback(packets_read, bytes_read, packet)
    return stats


def select_nwdemo_voiceprint_packets(
    path: str | os.PathLike[str],
    stats: Mapping[tuple[str, bool], Mapping[str, int]],
    *,
    allow_empty_world: bool = False,
) -> dict[tuple[str, bool], list[NwdemoVoicePacket]]:
    """Read only bounded start/middle/end representative windows.

    The result is intentionally bounded by timestamp span, not by the number
    of packets.  It is fed directly to the decoder and never contains the
    full replay stream.
    """
    windows: dict[tuple[str, bool], list[tuple[int, int]]] = {}
    for key, row in stats.items():
        first = int(row["first_ms"])
        last = int(row["last_ms"])
        if last - first <= ONNX_VOICEPRINT_MAX_DURATION_MS:
            ranges = [(first, last)]
        else:
            width = ONNX_VOICEPRINT_MAX_DURATION_MS // 3
            middle = (first + last) // 2
            ranges = [
                (first, first + width),
                (middle - width // 2, middle + width // 2),
                (last - width, last),
            ]
        windows[key] = ranges

    selected: dict[tuple[str, bool], list[NwdemoVoicePacket]] = {key: [] for key in windows}
    for packet in iter_nwdemo_voice_packets(path, allow_empty_world=allow_empty_world):
        key = (packet.steam_id, packet.radio)
        ranges = windows.get(key)
        if not ranges:
            continue
        if any(start <= packet.time_ms <= end for start, end in ranges):
            selected[key].append(packet)
    return selected


def _read_nwdemo_voice_packets(path: Path) -> tuple[list[VoicePacket], dict[int, str]]:
    """Compatibility adapter for the retired local-speaker metadata index.

    New nwdemo processing must call :func:`read_nwdemo_voice_packets`; this
    adapter intentionally has no identity semantics beyond feeding old read-only
    inspection code until the legacy tables can be retired.
    """
    direct_packets = read_nwdemo_voice_packets(path)
    steam_ids = {packet.steam_id for packet in direct_packets}
    local_ids = {steam_id: index + 1 for index, steam_id in enumerate(sorted(steam_ids))}
    mapping = {local_ids[steam_id]: steam_id for steam_id in steam_ids}
    return (
        [
            VoicePacket(packet.time_ms, local_ids[packet.steam_id], packet.data, packet.radio)
            for packet in direct_packets
        ],
        mapping,
    )


def _extract_nwdemo_voice_metadata(path: Path) -> ReplayVoiceSegmentExtraction:
    packets, mapping = _read_nwdemo_voice_packets(path)
    return ReplayVoiceSegmentExtraction(7, group_voice_packets(packets), mapping, {}, True, [])


def extract_voice_packets_for_speaker(
    path: str | os.PathLike[str],
    speaker_id: int,
) -> list[VoicePacket]:
    """Extract Opus packets for one speaker without retaining other speakers."""
    path = Path(path)
    if path.suffix.lower() == ".nwdemo":
        packets, _mapping = _read_nwdemo_voice_packets(path)
        return [packet for packet in packets if packet.speaker_id == speaker_id]
    with _open_replay(path) as (stream, _mapping):
        _version, frames = _iter_replay_frames(stream)
        packets: list[VoicePacket] = []
        for frame in frames:
            if frame.kind == "end":
                break
            if (
                frame.kind == "voice"
                and frame.speaker_id == speaker_id
                and frame.voice_data is not None
            ):
                packets.append(VoicePacket(frame.time_ms, speaker_id, frame.voice_data, frame.voice_radio))
        return packets


def write_wave_slice(
    source_path: str | os.PathLike[str],
    destination_path: str | os.PathLike[str],
    *,
    start_frame: int,
    end_frame: int,
) -> int:
    """Copy a PCM frame range from a decoder-produced WAV atomically."""
    if start_frame < 0 or end_frame < start_frame:
        raise ValueError("invalid WAV frame range")

    source = Path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    try:
        with wave.open(str(source), "rb") as reader:
            params = reader.getparams()
            reader.setpos(min(start_frame, reader.getnframes()))
            payload = reader.readframes(max(0, end_frame - start_frame))
        with wave.open(str(temporary), "wb") as writer:
            writer.setparams(params._replace(nframes=0))
            writer.writeframes(payload)
        os.replace(temporary, destination)
        return len(payload)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def make_archive_key(
    steam_id: str,
    replay_identity: str | int,
    speaker_id: int,
    start_ms: int,
    end_ms: int,
) -> str:
    payload = "|".join(
        (str(steam_id), str(replay_identity), str(speaker_id), str(start_ms), str(end_ms))
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def make_nwdemo_archive_key(
    source_replay_hash: str,
    steam_id: str,
    radio: bool,
    start_ms: int,
    end_ms: int,
) -> str:
    """Stable nwdemo candidate identity with no local speaker component."""
    payload = "|".join(
        (source_replay_hash, steam_id, str(int(radio)), str(start_ms), str(end_ms))
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _next_power_of_two(value: int) -> int:
    return 1 << max(0, (value - 1).bit_length())


def _fft(values: Sequence[complex]) -> list[complex]:
    """Small deterministic radix-2 FFT; voiceprints must not need NumPy."""
    size = len(values)
    output = list(values)
    bits = size.bit_length() - 1
    for index in range(size):
        swapped = 0
        value = index
        for _ in range(bits):
            swapped = (swapped << 1) | (value & 1)
            value >>= 1
        if swapped > index:
            output[index], output[swapped] = output[swapped], output[index]
    width = 2
    while width <= size:
        twiddle_step = complex(math.cos(-2 * math.pi / width), math.sin(-2 * math.pi / width))
        half = width // 2
        for start in range(0, size, width):
            twiddle = 1 + 0j
            for offset in range(half):
                even = output[start + offset]
                odd = output[start + offset + half] * twiddle
                output[start + offset] = even + odd
                output[start + offset + half] = even - odd
                twiddle *= twiddle_step
        width *= 2
    return output


def _hz_to_mel(value: float) -> float:
    return 2595.0 * math.log10(1.0 + value / 700.0)


def _mel_to_hz(value: float) -> float:
    return 700.0 * (10.0 ** (value / 2595.0) - 1.0)


def mfcc_v1_from_pcm16(samples: Sequence[int], sample_rate: int = 48_000) -> list[float]:
    """Return a normalized mean/std 26-dimensional MFCC voice feature."""
    if sample_rate != 48_000:
        raise ValueError("mfcc-v1 expects 48 kHz PCM")
    frame_size = int(sample_rate * 0.025)
    hop_size = int(sample_rate * 0.010)
    fft_size = _next_power_of_two(frame_size)
    if not samples:
        return [0.0] * (MFCC_V1_COEFFICIENTS * 2)
    frame_starts = list(range(0, max(1, len(samples) - frame_size + 1), hop_size))
    # A cap makes one very long replay no more authoritative than many rounds.
    if len(frame_starts) > 120:
        step = (len(frame_starts) - 1) / 119
        frame_starts = [frame_starts[round(index * step)] for index in range(120)]
    mel_min = _hz_to_mel(20.0)
    mel_max = _hz_to_mel(sample_rate / 2)
    mel_points = [mel_min + (mel_max - mel_min) * index / (MFCC_V1_FILTERS + 1) for index in range(MFCC_V1_FILTERS + 2)]
    bins = [min(fft_size // 2, int(math.floor((fft_size + 1) * _mel_to_hz(point) / sample_rate))) for point in mel_points]
    coefficients: list[list[float]] = []
    window = [0.54 - 0.46 * math.cos(2 * math.pi * index / (frame_size - 1)) for index in range(frame_size)]
    for start in frame_starts:
        frame = [float(samples[start + index]) / 32768.0 if start + index < len(samples) else 0.0 for index in range(frame_size)]
        spectrum = _fft([complex(value * window[index], 0.0) for index, value in enumerate(frame)] + [0j] * (fft_size - frame_size))
        power = [(value.real * value.real + value.imag * value.imag) / fft_size for value in spectrum[: fft_size // 2 + 1]]
        energies = []
        for filter_index in range(MFCC_V1_FILTERS):
            left, center, right = bins[filter_index : filter_index + 3]
            energy = 0.0
            for index in range(left, center):
                if center > left:
                    energy += power[index] * (index - left) / (center - left)
            for index in range(center, right):
                if right > center:
                    energy += power[index] * (right - index) / (right - center)
            energies.append(math.log(max(energy, 1e-12)))
        coefficients.append([
            sum(energies[index] * math.cos(math.pi * coefficient * (index + 0.5) / MFCC_V1_FILTERS) for index in range(MFCC_V1_FILTERS))
            for coefficient in range(MFCC_V1_COEFFICIENTS)
        ])
    means = [sum(frame[index] for frame in coefficients) / len(coefficients) for index in range(MFCC_V1_COEFFICIENTS)]
    deviations = [
        math.sqrt(sum((frame[index] - means[index]) ** 2 for frame in coefficients) / len(coefficients))
        for index in range(MFCC_V1_COEFFICIENTS)
    ]
    vector = [*means, *deviations]
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else [0.0] * len(vector)


def mfcc_v1_from_wav(path: str | os.PathLike[str]) -> tuple[list[float], int]:
    """Validate canonical WAV and calculate its durable mfcc-v1 feature."""
    with wave.open(str(path), "rb") as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 48_000):
            raise ValueError("voiceprint input must be mono 48 kHz PCM16 WAV")
        frame_count = reader.getnframes()
        payload = reader.readframes(frame_count)
    samples = struct.unpack(f"<{frame_count}h", payload) if frame_count else ()
    return mfcc_v1_from_pcm16(samples), round(frame_count * 1000 / 48_000)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return None
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def aggregate_mfcc_v1(rows: Iterable[Mapping[str, object]]) -> tuple[list[float], int, int] | None:
    """Duration-weight high-quality features; low-quality rows stay forensic only."""
    accepted = [row for row in rows if row.get("quality") == "good" and isinstance(row.get("vector"), list)]
    if not accepted:
        return None
    width = len(accepted[0]["vector"])
    if not width or any(len(row["vector"]) != width for row in accepted):
        return None
    weighted_duration = sum(max(1, int(row.get("duration_ms") or 0)) for row in accepted)
    vector = [
        sum(float(row["vector"][index]) * max(1, int(row.get("duration_ms") or 0)) for row in accepted) / weighted_duration
        for index in range(width)
    ]
    norm = math.sqrt(sum(value * value for value in vector))
    return ([value / norm for value in vector] if norm else vector, len(accepted), weighted_duration)


def select_voiceprint_pcm16(samples: Sequence[int], *, max_samples: int) -> list[int]:
    """Keep start/middle/end speech context without making long rounds dominant."""
    if max_samples <= 0:
        return []
    source = list(samples)
    if len(source) <= max_samples:
        return source
    sections = min(3, max_samples)
    base, extra = divmod(max_samples, sections)
    output: list[int] = []
    for section in range(sections):
        width = base + int(section < extra)
        if section == 0:
            start = 0
        elif section == sections - 1:
            start = len(source) - width
        else:
            start = max(0, (len(source) - width) // 2)
        output.extend(source[start : start + width])
    return output


def resample_pcm16_for_voiceprint(samples: Sequence[int], *, sample_rate: int = 48_000) -> list[int]:
    """Use Python's PCM resampler for the fixed canonical 48 kHz input."""
    if sample_rate == ONNX_VOICEPRINT_SAMPLE_RATE:
        return list(samples)
    if sample_rate != 48_000:
        raise ValueError("voiceprint input must be 48 kHz PCM16")
    payload = struct.pack(f"<{len(samples)}h", *samples) if samples else b""
    converted, _state = audioop.ratecv(payload, 2, 1, sample_rate, ONNX_VOICEPRINT_SAMPLE_RATE, None)
    return list(struct.unpack(f"<{len(converted) // 2}h", converted)) if converted else []


def onnx_voiceprint_embedding(samples: Sequence[int], session) -> list[float]:
    """Run a raw-waveform ONNX speaker model and L2-normalize its embedding."""
    if not samples:
        raise ValueError("voiceprint input is empty")
    import numpy

    waveform = numpy.asarray(samples, dtype=numpy.float32)[None, None, :] / 32768.0
    output = session.run(None, {session.get_inputs()[0].name: waveform})[0]
    vector = [float(value) for value in numpy.asarray(output).reshape(-1)]
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        raise ValueError("voiceprint model returned a zero vector")
    return [value / norm for value in vector]


def voiceprint_is_eligible(row: Mapping[str, object]) -> bool:
    return (
        int(row.get("sample_count") or 0) >= VOICEPRINT_MIN_SAMPLE_COUNT
        and int(row.get("duration_ms") or 0) >= VOICEPRINT_MIN_DURATION_MS
        and isinstance(row.get("vector"), list)
    )


def rank_voiceprint_candidates(target: Mapping[str, object], candidates: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """Rank only comparable, sufficiently sampled profiles; never assert identity."""
    if not voiceprint_is_eligible(target):
        return []
    steam_id = str(target.get("steam_id") or "")
    channel = str(target.get("source_channel") or "")
    vector = target.get("vector")
    assert isinstance(vector, list)
    ranked = []
    for candidate in candidates:
        candidate_steam_id = str(candidate.get("steam_id") or "")
        candidate_vector = candidate.get("vector")
        if (
            not candidate_steam_id
            or candidate_steam_id == steam_id
            or str(candidate.get("source_channel") or "") != channel
            or not voiceprint_is_eligible(candidate)
            or not isinstance(candidate_vector, list)
        ):
            continue
        score = cosine_similarity(vector, candidate_vector)
        if score is None:
            continue
        ranked.append({
            "steam_id": candidate_steam_id,
            "score": score,
            "sample_count": int(candidate.get("sample_count") or 0),
            "duration_ms": int(candidate.get("duration_ms") or 0),
            "source_channel": channel,
        })
    return sorted(ranked, key=lambda row: (-float(row["score"]), str(row["steam_id"])))[:10]


def rank_global_voiceprint_pairs(
    signatures: Iterable[Mapping[str, object]], *, limit: int = 10,
) -> list[dict[str, object]]:
    """Return each eligible player pair once, never mistaking a pair for proof."""
    rows = list(signatures)
    pairs: dict[tuple[str, str], dict[str, object]] = {}
    for target in rows:
        target_steam_id = str(target.get("steam_id") or "")
        target_channel = str(target.get("source_channel") or "")
        if not target_steam_id or not target_channel:
            continue
        for candidate in rank_voiceprint_candidates(target, rows):
            candidate_steam_id = str(candidate["steam_id"])
            left_steam_id, right_steam_id = sorted((target_steam_id, candidate_steam_id))
            target_is_left = target_steam_id == left_steam_id
            pair = {
                "left_steam_id": left_steam_id,
                "right_steam_id": right_steam_id,
                "score": float(candidate["score"]),
                "source_channel": target_channel,
                "left_sample_count": int(
                    target.get("sample_count") if target_is_left else candidate["sample_count"]
                ),
                "left_duration_ms": int(
                    target.get("duration_ms") if target_is_left else candidate["duration_ms"]
                ),
                "right_sample_count": int(
                    candidate["sample_count"] if target_is_left else target.get("sample_count")
                ),
                "right_duration_ms": int(
                    candidate["duration_ms"] if target_is_left else target.get("duration_ms")
                ),
            }
            existing = pairs.get((left_steam_id, right_steam_id))
            if (
                existing is None
                or float(pair["score"]) > float(existing["score"])
                or (
                    float(pair["score"]) == float(existing["score"])
                    and pair["source_channel"] == "normal"
                    and existing["source_channel"] != "normal"
                )
            ):
                pairs[(left_steam_id, right_steam_id)] = pair
    return sorted(
        pairs.values(),
        key=lambda pair: (-float(pair["score"]), str(pair["left_steam_id"]), str(pair["right_steam_id"])),
    )[:max(0, limit)]


def gzip_file(source_path: str | os.PathLike[str], destination_path: str | os.PathLike[str]) -> int:
    """Compress a WAV without adding a third-party runtime dependency."""
    source = Path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    try:
        with source.open("rb") as src, gzip.open(temporary, "wb", compresslevel=6) as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
        os.replace(temporary, destination)
        return destination.stat().st_size
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def needs_materialization(clip, wav_path: Path, ndvc_path: Path) -> bool:
    return (
        clip is None
        or not wav_path.is_file()
        or not ndvc_path.is_file()
        or int(getattr(clip, "archive_version", 1) or 1) != VOICE_PROFILE_ARCHIVE_VERSION
    )


def stale_profile_clips(clips, candidate_replay_ids: set[int]):
    """Keep provenance clips whose source replay is no longer available."""
    if not candidate_replay_ids:
        return []
    return [
        clip
        for clip in clips
        if clip.source_replay_id is not None
        and int(clip.source_replay_id) in candidate_replay_ids
    ]


def _decoder_command(decoder_path: str | os.PathLike[str], arguments: Sequence[str]) -> list[str]:
    path = str(decoder_path)
    if path.lower().endswith(".dll"):
        return ["dotnet", path, *arguments]
    if path.lower().endswith(".csproj"):
        return ["dotnet", "run", "--project", path, "-c", "Release", "--no-restore", "--", *arguments]
    return [path, *arguments]


def decode_vpk1(
    decoder_path: str | os.PathLike[str],
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
) -> None:
    subprocess.run(
        _decoder_command(
            decoder_path,
            [
                "--input", str(input_path),
                "--output", str(output_path),
                "--index", str(index_path),
            ],
        ),
        check=True,
    )


def extract_segment_wav(
    decoder_path: str | os.PathLike[str],
    packets: Sequence[VoicePacket],
    segment: Mapping[str, object],
    temporary_directory: str | os.PathLike[str],
    *,
    stream_cache: dict[tuple[int, int], tuple[Path, list[dict]]] | None = None,
    stream_key: tuple[int, int] | None = None,
) -> Path | None:
    """Decode one speaker stream and slice one selected segment."""
    temporary = Path(temporary_directory)
    temporary.mkdir(parents=True, exist_ok=True)
    prefix = str(segment["candidate_key"])
    cached_stream = stream_cache.get(stream_key) if stream_cache is not None and stream_key is not None else None
    if cached_stream is None:
        vpk_path = temporary / f"{prefix}.vpk1"
        stream_wav = temporary / f"{prefix}.stream.wav"
        index_path = temporary / f"{prefix}.index.json"
        write_vpk1(vpk_path, packets)
        decode_vpk1(decoder_path, vpk_path, stream_wav, index_path)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if stream_cache is not None and stream_key is not None:
            stream_cache[stream_key] = (stream_wav, index)
    else:
        stream_wav, index = cached_stream
    by_timestamp: dict[int, list[dict]] = {}
    for entry in index:
        by_timestamp.setdefault(int(entry["timestamp_ms"]), []).append(entry)
    entries = []
    for packet in segment["packet_records"]:
        values = by_timestamp.get(packet.time_ms, [])
        if values:
            entries.append(values.pop(0))
    if not entries:
        return None
    start_frame = min(int(entry["sample_start"]) for entry in entries)
    end_frame = max(int(entry["sample_start"]) + int(entry["sample_count"]) for entry in entries)
    output = temporary / f"{prefix}.wav"
    write_wave_slice(stream_wav, output, start_frame=start_frame, end_frame=end_frame)
    return output


class VoiceProfileService:
    """Index replay speakers and reconcile their durable 25-clip profiles."""

    def __init__(self, db, decoder_path: str | None = None):
        self.db = db
        self.decoder_path = decoder_path or os.environ.get("VOICE_PROFILE_DECODER_PATH")
        if not self.decoder_path:
            try:
                from config import get_settings

                self.decoder_path = get_settings().voice_profile_decoder_path or None
            except Exception:
                self.decoder_path = None
        if not self.decoder_path:
            local_dll = (
                Path(__file__).resolve().parent
                / "tools"
                / "VoiceProfileDecoder"
                / "bin"
                / "Release"
                / "net8.0"
                / "VoiceProfileDecoder.dll"
            )
            if local_dll.is_file():
                self.decoder_path = str(local_dll)
        from services_replays import get_upload_dir

        self.root = Path(get_upload_dir(db)) / "voice_profiles"
        self.root.mkdir(parents=True, exist_ok=True)

    def _player_candidates(self, names: Iterable[str]) -> list[dict]:
        from services import PlayerService

        candidates: dict[str, dict] = {}
        service = PlayerService(self.db)
        for name in sorted({str(value).strip() for value in names if str(value).strip()}):
            for player in service.search_players(name, limit=5):
                steam_id = str(getattr(player, "player_steamid", None) or "").strip()
                if not steam_id:
                    continue
                candidates[steam_id] = {
                    "player_id": player.player_id,
                    "steam_id": steam_id,
                    "name": getattr(player, "display_name", None) or getattr(player, "player_last_time_name", None),
                }
        return sorted(candidates.values(), key=lambda row: (str(row.get("name") or "").casefold(), row["steam_id"]))

    def _resolve_speaker(self, speaker_id: int, names: set[str], embedded_steam_id: str | None) -> tuple[dict | None, list[dict], str | None]:
        from models import PlayerData

        if embedded_steam_id:
            player = self.db.query(PlayerData).filter(PlayerData.player_steamid == embedded_steam_id).first()
            if player:
                return {
                    "player_id": player.player_id,
                    "steam_id": embedded_steam_id,
                    "name": player.display_name or player.player_last_time_name,
                }, [], "embedded"
            # A NewWorldStreamer identity is authoritative even when NexusData
            # has not seen that player yet. Never replace it with a name guess.
            return None, [], "embedded_missing_player"

        candidates = self._player_candidates(names)
        if len(candidates) == 1:
            return candidates[0], candidates, "nexus_name_search"
        return None, candidates, None

    @staticmethod
    def _segment_stats(packets: Sequence[VoicePacket]) -> dict[int, dict]:
        stats: dict[int, dict] = {}
        for packet in packets:
            row = stats.setdefault(
                packet.speaker_id,
                {"packet_count": 0, "radio_packet_count": 0, "start_ms": packet.time_ms, "end_ms": packet.time_ms},
            )
            row["packet_count"] += 1
            row["radio_packet_count"] += int(packet.radio)
            row["start_ms"] = min(row["start_ms"], packet.time_ms)
            row["end_ms"] = max(row["end_ms"], packet.time_ms)
        return stats

    def _has_voice_activity(self, replay_id: int) -> bool:
        from models import ReplayVoiceSegment, ReplayVoiceSpeaker

        speaker_ids = {
            int(row[0])
            for row in self.db.query(ReplayVoiceSpeaker.speaker_id).filter(
                ReplayVoiceSpeaker.replay_id == replay_id,
            ).all()
        }
        segment_speaker_ids = {
            int(row[0])
            for row in self.db.query(ReplayVoiceSegment.speaker_id).filter(
                ReplayVoiceSegment.replay_id == replay_id,
            ).distinct().all()
        }
        return bool(segment_speaker_ids) and voice_activity_is_complete(speaker_ids, segment_speaker_ids)

    def ensure_replay_voice_activity(self, replay_id: int) -> None:
        """Fill timeline metadata without invoking profile audio extraction."""
        if not self._has_voice_activity(replay_id):
            self._index_voice_activity_metadata(replay_id)

    def _index_voice_activity_metadata(self, replay_id: int) -> None:
        from models import ReplayVoiceSegment, ReplayVoiceSpeaker, RoundReplay

        replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if replay is None:
            raise ValueError(f"replay {replay_id} does not exist")
        replay_path = Path(__import__("services_replays").ReplayService(self.db).get_file_path(replay))
        if not replay_path.is_file():
            raise FileNotFoundError(str(replay_path))

        extraction = extract_voice_segment_metadata(replay_path)
        speaker_rows = {
            row.speaker_id: row
            for row in self.db.query(ReplayVoiceSpeaker).filter(ReplayVoiceSpeaker.replay_id == replay_id).all()
        }
        speaker_stats: dict[int, dict[str, int]] = {}
        for segment in extraction.segments:
            speaker_id = int(segment["speaker_id"])
            stats = speaker_stats.setdefault(speaker_id, {
                "packet_count": 0,
                "radio_packet_count": 0,
                "start_ms": int(segment["start_ms"]),
                "end_ms": int(segment["end_ms"]),
            })
            stats["packet_count"] += int(segment["packet_count"])
            stats["radio_packet_count"] += int(segment["radio_packet_count"])
            stats["start_ms"] = min(stats["start_ms"], int(segment["start_ms"]))
            stats["end_ms"] = max(stats["end_ms"], int(segment["end_ms"]))
        speaker_ids = {segment["speaker_id"] for segment in extraction.segments}
        for speaker_id in speaker_ids:
            row = speaker_rows.get(speaker_id)
            names = extraction.names_by_speaker.get(speaker_id, set())
            if row is None:
                row = ReplayVoiceSpeaker(
                    replay_id=replay_id,
                    speaker_id=speaker_id,
                    names=sorted(names),
                    resolution_status="unresolved",
                )
                self.db.add(row)
                speaker_rows[speaker_id] = row
            if row.resolution_status == "unresolved" and row.decided_by_admin_id is None:
                player, candidates, source = self._resolve_speaker(
                    speaker_id,
                    names,
                    extraction.mapping.get(speaker_id),
                )
                row.names = sorted(names)
                row.candidate_profiles = candidates
                if player:
                    row.steam_id = player["steam_id"]
                    row.player_id = player.get("player_id")
                    row.resolution_status = "mapped"
                    row.mapping_source = source
            stats = speaker_stats[speaker_id]
            row.packet_count = stats["packet_count"]
            row.radio_packet_count = stats["radio_packet_count"]
            row.start_ms = stats["start_ms"]
            row.end_ms = stats["end_ms"]

        self.db.flush()
        mapping = {
            speaker_id: row.steam_id
            for speaker_id, row in speaker_rows.items()
            if row.steam_id
        }
        # A partial legacy index is being replaced here. Keeping its rows
        # would violate the replay/speaker/time uniqueness constraint when the
        # same metadata is indexed again.
        self.db.query(ReplayVoiceSegment).filter(ReplayVoiceSegment.replay_id == replay_id).delete(synchronize_session=False)
        for segment in build_replay_voice_segment_rows(
            extraction.segments,
            mapping=mapping,
            speaker_rows=speaker_rows,
        ):
            self.db.add(ReplayVoiceSegment(replay_id=replay_id, **segment))
        self.db.commit()

    def index_replay(self, replay_id: int) -> dict:
        from models import ReplayVoiceSegment, ReplayVoiceSpeaker, RoundReplay

        replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if replay is None:
            raise ValueError(f"replay {replay_id} does not exist")
        replay_path = Path(__import__("services_replays").ReplayService(self.db).get_file_path(replay))
        if not replay_path.is_file():
            raise FileNotFoundError(str(replay_path))

        extraction = extract_voice_packet_records(replay_path)
        packets = extraction.packets
        grouped = group_voice_packets(packets)
        stats = self._segment_stats(packets)
        speaker_rows = {
            row.speaker_id: row
            for row in self.db.query(ReplayVoiceSpeaker).filter(ReplayVoiceSpeaker.replay_id == replay_id).all()
        }
        mapping: dict[int, str] = {}
        affected_steam_ids: set[str] = set()
        unresolved: list[int] = []
        mapped = 0

        for speaker_id, speaker_stats in sorted(stats.items()):
            names = extraction.names_by_speaker.get(speaker_id, set())
            row = speaker_rows.get(speaker_id)
            manual = row is not None and row.decided_by_admin_id is not None
            if row is not None and row.steam_id:
                affected_steam_ids.add(row.steam_id)
            if manual and row.resolution_status == "mapped" and row.steam_id:
                player = {"player_id": row.player_id, "steam_id": row.steam_id, "name": None}
                candidates = row.candidate_profiles or []
                source = row.mapping_source or "manual"
            elif manual and row.resolution_status == "skipped":
                player, candidates, source = None, row.candidate_profiles or [], "manual_skip"
            else:
                player, candidates, source = self._resolve_speaker(
                    speaker_id,
                    names,
                    extraction.mapping.get(speaker_id),
                )

            if row is None:
                row = ReplayVoiceSpeaker(replay_id=replay_id, speaker_id=speaker_id)
                self.db.add(row)
                speaker_rows[speaker_id] = row
            row.names = sorted(names)
            row.candidate_profiles = candidates
            row.packet_count = speaker_stats["packet_count"]
            row.radio_packet_count = speaker_stats["radio_packet_count"]
            row.start_ms = speaker_stats["start_ms"]
            row.end_ms = speaker_stats["end_ms"]
            if player:
                row.steam_id = player["steam_id"]
                row.player_id = player.get("player_id")
                row.resolution_status = "mapped"
                row.mapping_source = source
                mapping[speaker_id] = player["steam_id"]
                affected_steam_ids.add(player["steam_id"])
                mapped += 1
            elif not manual:
                row.steam_id = None
                row.player_id = None
                row.resolution_status = "unresolved"
                row.mapping_source = None
            else:
                row.steam_id = None
                row.player_id = None
            if row.resolution_status == "unresolved":
                unresolved.append(speaker_id)

        self.db.flush()
        self.db.query(ReplayVoiceSegment).filter(ReplayVoiceSegment.replay_id == replay_id).delete(synchronize_session=False)
        for segment in build_replay_voice_segment_rows(
            grouped,
            mapping=mapping,
            speaker_rows=speaker_rows,
        ):
            self.db.add(ReplayVoiceSegment(
                replay_id=replay_id,
                speaker_id=segment["speaker_id"],
                steam_id=segment["steam_id"],
                player_id=segment["player_id"],
                start_ms=segment["start_ms"],
                end_ms=segment["end_ms"],
                packet_count=segment["packet_count"],
                radio_packet_count=segment["radio_packet_count"],
            ))
        self.db.commit()

        materialized = 0
        if affected_steam_ids:
            materialized = sum(self.reconcile_steam_id(steam_id) for steam_id in affected_steam_ids)
        return {
            "replay_id": replay_id,
            "status": "partial" if unresolved else "ready",
            "mapped_speakers": mapped,
            "unresolved_speakers": unresolved,
            "materialized_clips": materialized,
            "warnings": extraction.warnings,
        }

    def _profile_candidates(self, steam_id: str) -> list[dict]:
        from models import ReplayVoiceSegment, RoundReplay

        rows = (
            self.db.query(ReplayVoiceSegment, RoundReplay)
            .join(RoundReplay, RoundReplay.id == ReplayVoiceSegment.replay_id)
            .filter(ReplayVoiceSegment.steam_id == steam_id)
            .all()
        )
        candidates = []
        replay_service = __import__("services_replays").ReplayService(self.db)
        rows.sort(key=lambda pair: int(pair[1].id))
        for replay_id, replay_rows in groupby(rows, key=lambda pair: int(pair[1].id)):
            replay_rows = list(replay_rows)
            replay = replay_rows[0][1]
            path = Path(replay_service.get_file_path(replay))
            if not path.is_file():
                continue
            extraction = extract_voice_packet_records(path)
            segment_metadata = {
                (
                    int(grouped["speaker_id"]),
                    int(grouped["start_ms"]),
                    int(grouped["end_ms"]),
                ): grouped
                for grouped in group_voice_packets(extraction.packets)
            }
            replay_hash = _file_sha256(path)
            for segment, _ in replay_rows:
                segment_data = segment_metadata.get(
                    (int(segment.speaker_id), int(segment.start_ms), int(segment.end_ms))
                )
                if segment_data is None:
                    continue
                candidates.append({
                    "candidate_key": make_archive_key(steam_id, replay.id, segment.speaker_id, segment.start_ms, segment.end_ms),
                    "steam_id": steam_id,
                    "player_id": segment.player_id,
                    "source_replay_id": replay.id,
                    "source_replay_filename": replay.filename,
                    "source_replay_hash": replay_hash,
                    "recorded_at": replay.recorded_at or replay.uploaded_at,
                    "speaker_id": segment.speaker_id,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "packet_count": segment.packet_count,
                    "radio_packet_count": segment.radio_packet_count,
                    "_source_replay_path": path,
                })
        return candidates

    def reconcile_steam_id(self, steam_id: str) -> int:
        from models import VoiceProfileClip, VoiceProfileSelection

        candidates = self._profile_candidates(steam_id)
        selected = select_profile_candidates(candidates)
        if not selected:
            return 0
        if not self.decoder_path:
            raise RuntimeError("VOICE_PROFILE_DECODER_PATH is not configured")

        self.root.joinpath("ndvc").mkdir(parents=True, exist_ok=True)
        self.root.joinpath("wav").mkdir(parents=True, exist_ok=True)
        ready: list[tuple[dict, VoiceProfileClip]] = []
        with tempfile.TemporaryDirectory(prefix="voice-profile-materialize-") as temp_dir:
            temp_path = Path(temp_dir)
            stream_cache: dict[tuple[int, int], tuple[Path, list[dict]]] = {}
            payload_cache: dict[int, tuple[dict[tuple[int, int, int], dict], dict[int, list[VoicePacket]]]] = {}
            for candidate in selected:
                key = candidate["candidate_key"]
                clip = self.db.query(VoiceProfileClip).filter(VoiceProfileClip.archive_key == key).first()
                wav_relative = f"voice_profiles/wav/{key}.wav.gz"
                ndvc_relative = f"voice_profiles/ndvc/{key}.ndvc"
                from services_replays import get_upload_dir

                upload_root = Path(get_upload_dir(self.db))
                wav_path = upload_root / wav_relative
                ndvc_path = upload_root / ndvc_relative
                if needs_materialization(clip, wav_path, ndvc_path):
                    replay_id = int(candidate["source_replay_id"])
                    payload = payload_cache.get(replay_id)
                    if payload is None:
                        extraction = extract_voice_packet_records(candidate["_source_replay_path"])
                        grouped = {
                            (
                                int(segment["speaker_id"]),
                                int(segment["start_ms"]),
                                int(segment["end_ms"]),
                            ): segment
                            for segment in group_voice_packets(extraction.packets)
                        }
                        by_speaker: dict[int, list[VoicePacket]] = {}
                        for packet in extraction.packets:
                            by_speaker.setdefault(packet.speaker_id, []).append(packet)
                        payload = (grouped, by_speaker)
                        payload_cache[replay_id] = payload
                    segment_data = payload[0].get(
                        (int(candidate["speaker_id"]), int(candidate["start_ms"]), int(candidate["end_ms"]))
                    )
                    if segment_data is None:
                        continue
                    materialized_candidate = dict(candidate)
                    materialized_candidate["packet_records"] = segment_data["packet_records"]
                    write_ndvc(ndvc_path, materialized_candidate["packet_records"])
                    decoded = extract_segment_wav(
                        self.decoder_path,
                        payload[1].get(int(candidate["speaker_id"]), []),
                        materialized_candidate,
                        temp_path,
                        stream_cache=stream_cache,
                        stream_key=(replay_id, int(candidate["speaker_id"])),
                    )
                    if decoded is None:
                        continue
                    storage_size = gzip_file(decoded, wav_path)
                    if clip is None:
                        clip = VoiceProfileClip(
                            archive_key=key,
                            steam_id=steam_id,
                            player_id=candidate.get("player_id"),
                            source_replay_id=candidate["source_replay_id"],
                            source_replay_filename=candidate["source_replay_filename"],
                            source_replay_hash=candidate["source_replay_hash"],
                            speaker_id=candidate["speaker_id"],
                            start_ms=candidate["start_ms"],
                            end_ms=candidate["end_ms"],
                            packet_count=candidate["packet_count"],
                            radio_packet_count=candidate["radio_packet_count"],
                            ndvc_filename=ndvc_relative,
                            wav_filename=wav_relative,
                            storage_format="wav-gzip",
                            storage_size=storage_size,
                            archive_version=VOICE_PROFILE_ARCHIVE_VERSION,
                            recorded_at=candidate.get("recorded_at"),
                        )
                        self.db.add(clip)
                        self.db.flush()
                    else:
                        clip.storage_size = storage_size
                        clip.player_id = candidate.get("player_id")
                        clip.archive_version = VOICE_PROFILE_ARCHIVE_VERSION
                ready.append((candidate, clip))

        self.db.query(VoiceProfileSelection).filter(VoiceProfileSelection.steam_id == steam_id).delete(synchronize_session=False)
        selected_ids = set()
        for slot, (candidate, clip) in enumerate(ready):
            band = "early" if slot < VOICE_PROFILE_EARLY_COUNT else "middle" if slot < VOICE_PROFILE_EARLY_COUNT + VOICE_PROFILE_MIDDLE_COUNT else "latest"
            self.db.add(VoiceProfileSelection(steam_id=steam_id, slot=slot, band=band, clip_id=clip.id))
            selected_ids.add(clip.id)
        self.db.flush()
        stale_candidates = self.db.query(VoiceProfileClip).filter(
            VoiceProfileClip.steam_id == steam_id,
            ~VoiceProfileClip.id.in_(selected_ids or {-1}),
        ).all()
        candidate_replay_ids = {int(candidate["source_replay_id"]) for candidate in candidates}
        for clip in stale_profile_clips(stale_candidates, candidate_replay_ids):
            for relative in (clip.wav_filename, clip.ndvc_filename):
                if relative:
                    try:
                        (self.root.parent / relative).unlink()
                    except FileNotFoundError:
                        pass
            self.db.delete(clip)
        self.db.commit()
        return len(ready)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def voiceprint_model_path() -> Path:
    """Return the persistent, release-managed model path without fetching it."""
    from config import get_settings

    return Path(get_settings().voiceprint_model_dir) / ONNX_VOICEPRINT_MODEL_FILENAME


def voiceprint_model_status() -> dict[str, object]:
    path = voiceprint_model_path()
    if not path.is_file():
        return {"status": "missing", "path": str(path)}
    actual_sha256 = _file_sha256(path)
    if actual_sha256 != ONNX_VOICEPRINT_MODEL_SHA256:
        return {"status": "invalid", "path": str(path), "sha256": actual_sha256}
    return {
        "status": "ready",
        "path": str(path),
        "sha256": actual_sha256,
        "bytes": path.stat().st_size,
    }


def install_voiceprint_model(*, emit=print) -> dict[str, object]:
    """Install the pinned release asset atomically; workers never call this."""
    destination = voiceprint_model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp.{os.getpid()}")
    digest = hashlib.sha256()
    try:
        emit(f"voiceprint-model: downloading {ONNX_VOICEPRINT_MODEL_FILENAME}")
        with urlopen(ONNX_VOICEPRINT_MODEL_RELEASE_URL, timeout=120) as source, temporary.open("wb") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                target.write(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != ONNX_VOICEPRINT_MODEL_SHA256:
            raise RuntimeError(
                "voiceprint model SHA-256 mismatch: "
                f"expected {ONNX_VOICEPRINT_MODEL_SHA256}, got {actual_sha256}"
            )
        os.replace(temporary, destination)
        return voiceprint_model_status()
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@lru_cache(maxsize=1)
def voiceprint_onnx_session(model_path: str):
    """Load the local model lazily, once per process, on the CPU provider."""
    import onnxruntime

    return onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])


def enqueue_voice_profile_job(db, replay_ids: Iterable[int], mode: str = "backfill"):
    """Create one durable run; callers may safely retry after a 409."""
    from models import VoiceProfileJob

    ids = sorted({int(value) for value in replay_ids})
    active = db.query(VoiceProfileJob).filter(VoiceProfileJob.status.in_(("queued", "running"))).first()
    if active is not None:
        raise RuntimeError(f"voice profile job {active.id} is already active")
    job = VoiceProfileJob(
        mode=mode,
        status="queued",
        replay_ids=ids,
        total_replays=len(ids),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def process_voice_profile_job(db) -> dict:
    """Process at most one replay from the oldest queued/running job."""
    from datetime import datetime, timedelta
    from models import VoiceProfileJob

    job = db.query(VoiceProfileJob).filter(
        VoiceProfileJob.status.in_(("queued", "running")),
    ).order_by(VoiceProfileJob.id).with_for_update().first()
    if job is None:
        return {"status": "idle"}
    now = datetime.utcnow()
    if job.status == "running" and job.current_replay_id is not None:
        heartbeat = job.heartbeat_at
        if heartbeat is None or now - heartbeat < timedelta(hours=6):
            return {"status": "busy", "job_id": job.id, "current_replay_id": job.current_replay_id}
        # A process died between the durable claim and the next commit. The
        # six-hour lease is recovery protection, not a processing timeout.
        job.current_replay_id = None
    if job.status == "queued":
        job.status = "running"
        job.started_at = now
    ids = list(job.replay_ids or [])
    index = job.processed_replays
    if index >= len(ids):
        job.status = "completed"
        job.finished_at = now
        job.current_replay_id = None
        db.commit()
        return {"status": "completed", "job_id": job.id}

    replay_id = int(ids[index])
    job.current_replay_id = replay_id
    job.heartbeat_at = now
    db.commit()
    try:
        result = VoiceProfileService(db).index_replay(replay_id)
        job.processed_replays += 1
        job.mapped_speakers += int(result.get("mapped_speakers") or 0)
        job.unresolved_speakers += len(result.get("unresolved_speakers") or [])
        job.materialized_clips += int(result.get("materialized_clips") or 0)
        job.attempt_count = 0
        job.heartbeat_at = datetime.utcnow()
        job.current_replay_id = None
        db.commit()
        return {"status": "processed", "job_id": job.id, **result}
    except Exception as exc:
        db.rollback()
        job = db.query(VoiceProfileJob).filter(VoiceProfileJob.id == job.id).one()
        job.attempt_count += 1
        job.status = "failed" if job.attempt_count >= 3 else "queued"
        job.error_message = str(exc)[:2000]
        job.finished_at = datetime.utcnow() if job.status == "failed" else None
        job.current_replay_id = None
        job.heartbeat_at = datetime.utcnow()
        db.commit()
        if job.status == "queued":
            return {"status": "retry_scheduled", "job_id": job.id, "attempt_count": job.attempt_count}
        raise


def process_next_nwdemo_voice_profile(db) -> dict:
    """Process one pending NewWorldStreamer archive; legacy jobs stay inert."""
    from models import RoundReplay, VoiceProfileClip, VoiceReplayFingerprint
    from sqlalchemy import exists, or_

    service = NwdemoVoiceProfileService(db)
    if not service.decoder_path:
        return {"status": "awaiting_decoder"}
    replay = db.query(RoundReplay).filter(
        or_(
            RoundReplay.replay_format == "nwdemo-v2",
            RoundReplay.filename.ilike("%.nwdemo"),
            RoundReplay.stored_filename.ilike("%.nwdemo"),
        ),
        or_(
            RoundReplay.voice_profile_state.in_(("pending", "awaiting_decoder")),
            RoundReplay.voice_profile_state.is_(None),
        ),
    ).order_by(RoundReplay.id).with_for_update().first()
    if replay is None:
        if not voiceprint_worker_enabled(db):
            return {"status": "voiceprint_paused"}
        model = voiceprint_model_status()
        if model["status"] != "ready":
            return {"status": "awaiting_voiceprint_model"}
        replay = db.query(RoundReplay).filter(
            RoundReplay.replay_format == "nwdemo-v2",
            RoundReplay.voice_profile_state == "ready",
            exists().where(VoiceProfileClip.source_replay_id == RoundReplay.id),
            ~exists().where(
                (VoiceReplayFingerprint.replay_id == RoundReplay.id)
                & (VoiceReplayFingerprint.algorithm == ONNX_VOICEPRINT_ALGORITHM)
            ),
        ).order_by(RoundReplay.id).with_for_update().first()
        if replay is None:
            return {"status": "idle"}
        replay.voice_profile_state = "processing"
        replay.voice_profile_started_at = _datetime.datetime.utcnow()
        replay.voice_profile_heartbeat_at = None
        replay.voice_profile_attempts = int(replay.voice_profile_attempts or 0) + 1
        replay.voice_profile_progress = {"mode": "voiceprint", "phase": "embedding", "percent": 0}
        db.commit()
        heartbeat_nwdemo_replay(db, replay, phase="embedding", percent=0)
        try:
            def progress(phase, percent, **metrics):
                current = db.query(RoundReplay).filter(RoundReplay.id == replay.id).one()
                heartbeat_nwdemo_replay(db, current, phase=phase, percent=percent, **metrics)

            result = service.backfill_voiceprint(int(replay.id), progress_callback=progress)
            replay = db.query(RoundReplay).filter(RoundReplay.id == replay.id).one()
            replay.voice_profile_state = "ready"
            replay.voice_profile_started_at = None
            replay.voice_profile_heartbeat_at = None
            replay.voice_profile_progress = None
            db.commit()
            return {"status": "voiceprint_processed", **result}
        except Exception as exc:
            db.rollback()
            replay = db.query(RoundReplay).filter(RoundReplay.id == replay.id).one()
            replay.voice_profile_state = "failed"
            replay.voice_profile_started_at = None
            replay.voice_profile_heartbeat_at = None
            replay.voice_profile_error = f"voiceprint: {exc}"[:2000]
            db.commit()
            raise
    # Rows uploaded before v2 columns existed are admitted only because their
    # extension narrows the candidate set; index_replay still validates every
    # manifest/world/voice byte contract before writing a profile.
    replay.replay_format = "nwdemo-v2"
    replay.voice_profile_state = "processing"
    replay.voice_profile_started_at = _datetime.datetime.utcnow()
    replay.voice_profile_heartbeat_at = None
    replay.voice_profile_attempts = int(replay.voice_profile_attempts or 0) + 1
    replay.voice_profile_progress = {"mode": "clips", "phase": "reading", "percent": 0}
    replay.voice_profile_error = None
    db.commit()
    heartbeat_nwdemo_replay(db, replay, phase="reading", percent=0)
    try:
        def progress(phase, percent, **metrics):
            current = db.query(RoundReplay).filter(RoundReplay.id == replay.id).one()
            heartbeat_nwdemo_replay(
                db,
                current,
                phase=phase,
                percent=percent,
                **metrics,
            )

        result = service.index_replay(int(replay.id), progress_callback=progress)
        replay = db.query(RoundReplay).filter(RoundReplay.id == replay.id).one()
        replay.voice_profile_started_at = None
        replay.voice_profile_heartbeat_at = None
        replay.voice_profile_progress = None
        db.commit()
        return {"status": "processed", **result}
    except Exception as exc:
        db.rollback()
        replay = db.query(RoundReplay).filter(RoundReplay.id == replay.id).one()
        replay.voice_profile_state = "failed"
        replay.voice_profile_started_at = None
        replay.voice_profile_heartbeat_at = None
        replay.voice_profile_progress = None
        replay.voice_profile_error = str(exc)[:2000]
        db.commit()
        raise


def _read_exact(file, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise ValueError("truncated VPK1 file")
    return data


def write_vpk1(path: str | os.PathLike[str], packets: Sequence[VoicePacket]) -> int:
    """Write one speaker's packet stream for the Concentus helper."""
    speaker_ids = {packet.speaker_id for packet in packets}
    if len(speaker_ids) > 1:
        raise ValueError("VPK1 stream must contain one speaker")
    if len(packets) > 0xFFFFFFFF:
        raise ValueError("too many packets")

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as file:
        file.write(VPK1_MAGIC)
        file.write(struct.pack("<HI", VPK1_VERSION, len(packets)))
        for packet in packets:
            if not 0 <= packet.time_ms <= 0xFFFFFFFF:
                raise ValueError("packet timestamp is outside VPK1 range")
            if len(packet.data) > 0xFFFF:
                raise ValueError("Opus packet is too large for VPK1")
            file.write(struct.pack("<IBH", packet.time_ms, int(packet.radio), len(packet.data)))
            file.write(packet.data)
    return destination.stat().st_size


def read_vpk1(
    path: str | os.PathLike[str],
    *,
    speaker_id: int,
) -> list[VoicePacket]:
    """Read a VPK1 stream and attach its caller-provided local speaker ID."""
    with Path(path).open("rb") as file:
        if _read_exact(file, 4) != VPK1_MAGIC:
            raise ValueError("invalid VPK1 magic")
        version, count = struct.unpack("<HI", _read_exact(file, 6))
        if version != VPK1_VERSION:
            raise ValueError(f"unsupported VPK1 version {version}")

        packets = []
        for _ in range(count):
            time_ms, radio, length = struct.unpack("<IBH", _read_exact(file, 7))
            packets.append(VoicePacket(time_ms, speaker_id, _read_exact(file, length), bool(radio)))
        if file.read(1):
            raise ValueError("trailing bytes in VPK1 file")
        return packets


def write_ndvc(path: str | os.PathLike[str], packets: Sequence[VoicePacket]) -> int:
    """Persist the original Opus packets in the compact archive format."""
    if len(packets) > 0xFFFFFFFF:
        raise ValueError("too many packets")
    first_time = packets[0].time_ms if packets else 0
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as file:
        file.write(NDVC_MAGIC)
        file.write(struct.pack("<BI", NDVC_VERSION, len(packets)))
        for packet in packets:
            relative_time = packet.time_ms - first_time
            if not 0 <= relative_time <= 0xFFFFFFFF:
                raise ValueError("packet timestamp is outside NDVC range")
            if len(packet.data) > 0xFFFF:
                raise ValueError("Opus packet is too large for NDVC")
            file.write(struct.pack("<IBH", relative_time, int(packet.radio), len(packet.data)))
            file.write(packet.data)
    return destination.stat().st_size


class _NdvcStreamWriter:
    """Write one NDVC segment without retaining its Opus packets."""

    def __init__(self, path: Path, first_time_ms: int):
        self.path = path
        self.first_time_ms = first_time_ms
        self.packet_count = 0
        self.radio_packet_count = 0
        self.last_time_ms = first_time_ms
        self._file = path.open("wb")
        self._file.write(NDVC_MAGIC)
        self._file.write(struct.pack("<BI", NDVC_VERSION, 0))

    def append(self, packet: NwdemoVoicePacket) -> None:
        relative_time = packet.time_ms - self.first_time_ms
        if not 0 <= relative_time <= 0xFFFFFFFF:
            raise ValueError("packet timestamp is outside NDVC range")
        if len(packet.data) > 0xFFFF:
            raise ValueError("Opus packet is too large for NDVC")
        self._file.write(struct.pack("<IBH", relative_time, int(packet.radio), len(packet.data)))
        self._file.write(packet.data)
        self.packet_count += 1
        self.radio_packet_count += int(packet.radio)
        self.last_time_ms = packet.time_ms

    def close(self) -> int:
        self._file.flush()
        self._file.seek(5)
        self._file.write(struct.pack("<I", self.packet_count))
        self._file.close()
        return self.path.stat().st_size


def stream_nwdemo_segments(
    path: str | os.PathLike[str],
    temporary: Path,
    *,
    allow_empty_world: bool = False,
    progress_callback=None,
) -> list[dict]:
    """Segment a chronological nwdemo stream into temporary NDVC files."""
    active: dict[tuple[str, bool], tuple[_NdvcStreamWriter, dict]] = {}
    segments: list[dict] = []
    packet_index = 0
    temporary.mkdir(parents=True, exist_ok=True)

    def finish(key: tuple[str, bool]) -> None:
        writer, segment = active.pop(key)
        writer.close()
        segment["packet_count"] = writer.packet_count
        segment["radio_packet_count"] = writer.radio_packet_count
        segment["end_ms"] = writer.last_time_ms
        segment["packet_file"] = writer.path
        segments.append(segment)

    for packet in iter_nwdemo_voice_packets(path, allow_empty_world=allow_empty_world):
        packet_index += 1
        key = (packet.steam_id, packet.radio)
        current = active.get(key)
        if current is None or packet.time_ms - current[1]["end_ms"] > VOICE_MERGE_GAP_MS:
            if current is not None:
                finish(key)
            temp_path = temporary / f"segment-{packet_index:08d}.ndvc.tmp"
            writer = _NdvcStreamWriter(temp_path, packet.time_ms)
            segment = {
                "steam_id": packet.steam_id,
                "radio": packet.radio,
                "start_ms": packet.time_ms,
                "end_ms": packet.time_ms,
                "packet_count": 0,
                "radio_packet_count": 0,
            }
            active[key] = (writer, segment)
            current = (writer, segment)
        writer, segment = current
        writer.append(packet)
        segment["end_ms"] = packet.time_ms
        if progress_callback is not None:
            progress_callback(packet_index, packet)
    for key in list(active):
        finish(key)
    return sorted(segments, key=lambda row: (row["start_ms"], row["steam_id"], row["radio"]))


def read_ndvc(path: str | os.PathLike[str]) -> list[VoicePacket]:
    """Read the durable Opus archive; v1 is retained only for old corpus rows."""
    with Path(path).open("rb") as file:
        if _read_exact(file, 4) != NDVC_MAGIC:
            raise ValueError("invalid NDVC magic")
        version, count = struct.unpack("<BI", _read_exact(file, 5))
        if version not in (1, NDVC_VERSION):
            raise ValueError(f"unsupported NDVC version {version}")
        packets = []
        for _ in range(count):
            if version == 1:
                time_ms, length = struct.unpack("<IH", _read_exact(file, 6))
                radio = False
            else:
                time_ms, radio_raw, length = struct.unpack("<IBH", _read_exact(file, 7))
                if radio_raw not in (0, 1):
                    raise ValueError("invalid NDVC radio flag")
                radio = bool(radio_raw)
            packets.append(VoicePacket(time_ms, 0, _read_exact(file, length), radio))
        if file.read(1):
            raise ValueError("trailing bytes in NDVC file")
        return packets


class NwdemoVoiceProfileService(VoiceProfileService):
    """The direct-identity nwdemo-v2 profile pipeline, isolated from legacy."""

    def _lookup_player_id(self, steam_id: str) -> int | None:
        from models import PlayerData

        player = self.db.query(PlayerData).filter(PlayerData.player_steamid == steam_id).first()
        return int(player.player_id) if player is not None else None

    def _decode_ndvc(self, ndvc_path: Path, temporary: Path, key: str) -> Path:
        if not self.decoder_path:
            raise RuntimeError("VOICE_PROFILE_DECODER_PATH is not configured")
        vpk_path = temporary / f"{key}.vpk1"
        wav_path = temporary / f"{key}.wav"
        index_path = temporary / f"{key}.index.json"
        write_vpk1(vpk_path, read_ndvc(ndvc_path))
        decode_vpk1(self.decoder_path, vpk_path, wav_path, index_path)
        return wav_path

    def _update_signature(self, steam_id: str, source_channel: str, algorithm: str) -> None:
        from models import VoiceProfileSignature, VoiceReplayFingerprint

        fingerprints = self.db.query(VoiceReplayFingerprint).filter(
            VoiceReplayFingerprint.steam_id == steam_id,
            VoiceReplayFingerprint.algorithm == algorithm,
            VoiceReplayFingerprint.source_channel == source_channel,
            VoiceReplayFingerprint.quality == "good",
        ).all()
        signature = self.db.query(VoiceProfileSignature).filter(
            VoiceProfileSignature.steam_id == steam_id,
            VoiceProfileSignature.algorithm == algorithm,
            VoiceProfileSignature.source_channel == source_channel,
        ).first()
        aggregate = aggregate_mfcc_v1({
            "vector": row.vector,
            "quality": row.quality,
            "duration_ms": row.duration_ms,
        } for row in fingerprints)
        if aggregate is None:
            if signature is not None:
                self.db.delete(signature)
            return
        vector, sample_count, weighted_duration = aggregate
        if signature is None:
            signature = VoiceProfileSignature(
                steam_id=steam_id,
                algorithm=algorithm,
                source_channel=source_channel,
                vector=vector,
                sample_count=sample_count,
                weighted_duration_ms=weighted_duration,
            )
            self.db.add(signature)
        else:
            signature.vector = vector
            signature.sample_count = sample_count
            signature.weighted_duration_ms = weighted_duration

    def _update_fingerprint(self, replay, steam_id: str, packets: Sequence[NwdemoVoicePacket], temporary: Path) -> None:
        from models import VoiceReplayFingerprint

        normal = [packet for packet in packets if not packet.radio]
        source_channel = "normal" if normal else "radio"
        chosen = normal or list(packets)
        if not chosen:
            return
        key = hashlib.sha256(f"fingerprint|{replay.id}|{steam_id}".encode()).hexdigest()
        ndvc_path = temporary / f"{key}.ndvc"
        write_ndvc(ndvc_path, chosen)
        wav_path = self._decode_ndvc(ndvc_path, temporary, key)
        vector, duration_ms = mfcc_v1_from_wav(wav_path)
        quality = "good" if duration_ms >= MFCC_V1_MIN_QUALITY_MS else "low"
        row = self.db.query(VoiceReplayFingerprint).filter(
            VoiceReplayFingerprint.replay_id == replay.id,
            VoiceReplayFingerprint.steam_id == steam_id,
            VoiceReplayFingerprint.algorithm == MFCC_V1_ALGORITHM,
        ).first()
        values = {
            "source_channel": source_channel,
            "vector": vector,
            "quality": quality,
            "duration_ms": duration_ms,
            "packet_count": len(chosen),
        }
        if row is None:
            self.db.add(VoiceReplayFingerprint(
                replay_id=replay.id,
                steam_id=steam_id,
                algorithm=MFCC_V1_ALGORITHM,
                **values,
            ))
        else:
            for name, value in values.items():
                setattr(row, name, value)
        self.db.flush()
        self._update_signature(steam_id, "normal", MFCC_V1_ALGORITHM)
        self._update_signature(steam_id, "radio", MFCC_V1_ALGORITHM)

    @staticmethod
    def _read_canonical_wav(path: Path) -> tuple[list[int], int]:
        with wave.open(str(path), "rb") as reader:
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 48_000):
                raise ValueError("voiceprint input must be mono 48 kHz PCM16 WAV")
            frame_count = reader.getnframes()
            payload = reader.readframes(frame_count)
        samples = list(struct.unpack(f"<{frame_count}h", payload)) if frame_count else []
        return samples, round(frame_count * 1000 / 48_000)

    def _update_onnx_fingerprint(
        self,
        replay,
        steam_id: str,
        packets: Sequence[NwdemoVoicePacket],
        temporary: Path,
        session,
    ) -> None:
        from models import VoiceReplayFingerprint

        normal = [packet for packet in packets if not packet.radio]
        source_channel = "normal" if normal else "radio"
        chosen = normal or list(packets)
        if not chosen:
            return
        key = hashlib.sha256(f"onnx-fingerprint|{replay.id}|{steam_id}".encode()).hexdigest()
        ndvc_path = temporary / f"{key}.ndvc"
        write_ndvc(ndvc_path, chosen)
        samples, duration_ms = self._read_canonical_wav(self._decode_ndvc(ndvc_path, temporary, key))
        capped = select_voiceprint_pcm16(
            samples,
            max_samples=48_000 * (ONNX_VOICEPRINT_MAX_DURATION_MS // 1000),
        )
        embedding = onnx_voiceprint_embedding(resample_pcm16_for_voiceprint(capped), session)
        if len(embedding) != ONNX_VOICEPRINT_DIMENSIONS:
            raise ValueError(f"voiceprint model returned {len(embedding)} dimensions, expected {ONNX_VOICEPRINT_DIMENSIONS}")
        row = self.db.query(VoiceReplayFingerprint).filter(
            VoiceReplayFingerprint.replay_id == replay.id,
            VoiceReplayFingerprint.steam_id == steam_id,
            VoiceReplayFingerprint.algorithm == ONNX_VOICEPRINT_ALGORITHM,
        ).first()
        values = {
            "source_channel": source_channel,
            "vector": embedding,
            "quality": "good" if duration_ms >= MFCC_V1_MIN_QUALITY_MS else "low",
            "duration_ms": min(duration_ms, ONNX_VOICEPRINT_MAX_DURATION_MS),
            "packet_count": len(chosen),
        }
        if row is None:
            self.db.add(VoiceReplayFingerprint(
                replay_id=replay.id,
                steam_id=steam_id,
                algorithm=ONNX_VOICEPRINT_ALGORITHM,
                **values,
            ))
        else:
            for name, value in values.items():
                setattr(row, name, value)
        self.db.flush()
        self._update_signature(steam_id, "normal", ONNX_VOICEPRINT_ALGORITHM)
        self._update_signature(steam_id, "radio", ONNX_VOICEPRINT_ALGORITHM)

    def reconcile_steam_id(self, steam_id: str) -> int:
        """Select 3/3/5 and materialize WAV only for those selected clips."""
        from models import VoiceProfileClip, VoiceProfileSelection
        from services_replays import get_upload_dir

        clips = self.db.query(VoiceProfileClip).filter(
            VoiceProfileClip.steam_id == steam_id,
            VoiceProfileClip.source_format == "nwdemo-v2",
        ).all()
        candidates = [{
            "candidate_key": clip.archive_key,
            "replay_id": clip.source_replay_id,
            "start_ms": clip.start_ms,
            "recorded_at": clip.recorded_at,
            "clip": clip,
        } for clip in clips]
        selected = select_profile_candidates(candidates)
        selected_ids = {candidate["clip"].id for candidate in selected}
        root = Path(get_upload_dir(self.db))
        self.root.joinpath("wav").mkdir(parents=True, exist_ok=True)
        materialized = 0
        with tempfile.TemporaryDirectory(prefix="nwdemo-voice-profile-") as temp_dir:
            temporary = Path(temp_dir)
            for candidate in selected:
                clip = candidate["clip"]
                wav_relative = f"voice_profiles/wav/{clip.archive_key}.wav.gz"
                wav_path = root / wav_relative
                ndvc_path = root / str(clip.ndvc_filename)
                if not wav_path.is_file():
                    decoded = self._decode_ndvc(ndvc_path, temporary, clip.archive_key)
                    clip.storage_size = int(clip.storage_size or 0) + gzip_file(decoded, wav_path)
                    materialized += 1
                clip.wav_filename = wav_relative
                clip.storage_format = "ndvc+wav-gzip"
            for clip in clips:
                if clip.id in selected_ids or not clip.wav_filename:
                    continue
                try:
                    (root / clip.wav_filename).unlink()
                except FileNotFoundError:
                    pass
                clip.wav_filename = None
                clip.storage_format = "ndvc"
        self.db.query(VoiceProfileSelection).filter(
            VoiceProfileSelection.steam_id == steam_id,
        ).delete(synchronize_session=False)
        for slot, candidate in enumerate(selected):
            band = "early" if slot < VOICE_PROFILE_EARLY_COUNT else "middle" if slot < VOICE_PROFILE_EARLY_COUNT + VOICE_PROFILE_MIDDLE_COUNT else "latest"
            self.db.add(VoiceProfileSelection(
                steam_id=steam_id,
                slot=slot,
                band=band,
                clip_id=candidate["clip"].id,
            ))
        return materialized

    def index_replay(self, replay_id: int, *, progress_callback=None) -> dict:
        """Persist direct-ID candidates and then derive selections/fingerprints."""
        from models import RoundReplay, VoiceProfileClip
        from services_replays import ReplayService, get_upload_dir

        replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if replay is None:
            raise ValueError(f"replay {replay_id} does not exist")
        if (
            getattr(replay, "replay_format", None) != "nwdemo-v2"
            and not str(getattr(replay, "stored_filename", "")).lower().endswith(".nwdemo")
        ):
            raise ValueError("voice profiles accept only nwdemo-v2 replays")
        path = Path(ReplayService(self.db).get_file_path(replay))
        replay.replay_format = "nwdemo-v2"
        replay_hash = _file_sha256(path)
        upload_root = Path(get_upload_dir(self.db))
        self.root.joinpath("ndvc").mkdir(parents=True, exist_ok=True)
        affected: set[str] = set()
        allow_empty_world = replay.replay_kind in {"lobby", "lobby_dump"}
        model = voiceprint_model_status()
        with tempfile.TemporaryDirectory(prefix="nwdemo-stream-") as temp_dir:
            temporary = Path(temp_dir)
            stats_speakers: set[str] = set()
            def stats_progress(count, bytes_read, packet):
                stats_speakers.add(packet.steam_id)
                if progress_callback is not None:
                    progress_callback(
                        "reading",
                        5,
                        packets_read=count,
                        bytes_read=bytes_read,
                        speakers_seen=len(stats_speakers),
                    )

            stats = collect_nwdemo_voice_stats(
                path,
                allow_empty_world=allow_empty_world,
                progress_callback=stats_progress,
            )
            if progress_callback is not None:
                progress_callback("segmenting", 5, packets_read=0, speakers_seen=len({key[0] for key in stats}))
            samples = (
                select_nwdemo_voiceprint_packets(path, stats, allow_empty_world=allow_empty_world)
                if model["status"] == "ready"
                else {}
            )
            stream_bytes = 0
            stream_packets = 0

            def stream_progress(count, packet):
                nonlocal stream_bytes, stream_packets
                stream_bytes += len(packet.data)
                stream_packets = count
                if progress_callback is not None and count % 5000 == 0:
                    progress_callback(
                        "segmenting",
                        10,
                        packets_read=count,
                        bytes_read=stream_bytes,
                        speakers_seen=len({key[0] for key in stats}),
                    )

            segments = stream_nwdemo_segments(
                path,
                temporary,
                allow_empty_world=allow_empty_world,
                progress_callback=stream_progress,
            )
            stream_packets = sum(int(segment["packet_count"]) for segment in segments)
            for segment in segments:
                steam_id = str(segment["steam_id"])
                key = make_nwdemo_archive_key(
                    replay_hash, steam_id, bool(segment["radio"]), int(segment["start_ms"]), int(segment["end_ms"]),
                )
                ndvc_relative = f"voice_profiles/ndvc/{key}.ndvc"
                ndvc_path = upload_root / ndvc_relative
                ndvc_path.parent.mkdir(parents=True, exist_ok=True)
                if not ndvc_path.is_file():
                    os.replace(segment["packet_file"], ndvc_path)
                else:
                    Path(segment["packet_file"]).unlink(missing_ok=True)
                clip = self.db.query(VoiceProfileClip).filter(VoiceProfileClip.archive_key == key).first()
                values = {
                    "steam_id": steam_id,
                    "player_id": self._lookup_player_id(steam_id),
                    "source_replay_id": replay.id,
                    "source_replay_filename": replay.filename,
                    "source_replay_hash": replay_hash,
                    "speaker_id": None,
                    "start_ms": int(segment["start_ms"]),
                    "end_ms": int(segment["end_ms"]),
                    "packet_count": int(segment["packet_count"]),
                    "radio_packet_count": int(segment["radio_packet_count"]),
                    "ndvc_filename": ndvc_relative,
                    "storage_format": "ndvc",
                    "storage_size": ndvc_path.stat().st_size,
                    "archive_version": NWDEMO_PROFILE_ARCHIVE_VERSION,
                    "recorded_at": replay.recorded_at or replay.uploaded_at,
                    "source_format": "nwdemo-v2",
                    "source_kind": replay.replay_kind,
                    "is_radio": bool(segment["radio"]),
                }
                if clip is None:
                    self.db.add(VoiceProfileClip(archive_key=key, **values))
                else:
                    for name, value in values.items():
                        setattr(clip, name, value)
                    if clip.wav_filename:
                        wav_path = upload_root / clip.wav_filename
                        if wav_path.is_file():
                            clip.storage_size = ndvc_path.stat().st_size + wav_path.stat().st_size
                affected.add(steam_id)
            if progress_callback is not None:
                progress_callback(
                    "clips",
                    55,
                    packets_read=stream_packets,
                    speakers_seen=len({key[0] for key in stats}),
                    speakers_completed=len(affected),
                )
            self.db.flush()
            materialized = 0
            if self.decoder_path:
                for steam_id in affected:
                    materialized += self.reconcile_steam_id(steam_id)
                if model["status"] == "ready":
                    session = voiceprint_onnx_session(str(model["path"]))
                    if progress_callback is not None:
                        progress_callback("embedding", 70, speakers_seen=len({key[0] for key in stats}))
                    for steam_id in sorted({key[0] for key in samples}):
                        normal = samples.get((steam_id, False), [])
                        radio = samples.get((steam_id, True), [])
                        player_packets = normal or radio
                        if player_packets:
                            self._update_onnx_fingerprint(replay, steam_id, player_packets, temporary, session)
                            if progress_callback is not None:
                                progress_callback("embedding", 70, speakers_completed=1)
                if progress_callback is not None:
                    progress_callback("finalizing", 95, speakers_completed=len(affected))
                replay.voice_profile_state = "ready"
                replay.voice_profile_error = None if model["status"] == "ready" else "voiceprint model is not installed"
                replay.voice_profile_processed_at = _datetime.datetime.utcnow()
            else:
                replay.voice_profile_state = "awaiting_decoder"
                replay.voice_profile_error = "VOICE_PROFILE_DECODER_PATH is not configured"
        self.db.commit()
        return {
            "replay_id": replay_id,
            "status": replay.voice_profile_state,
            "mapped_speakers": len(affected),
            "unresolved_speakers": [],
            "materialized_clips": materialized,
            "warnings": [],
        }

    def backfill_voiceprint(self, replay_id: int, *, progress_callback=None) -> dict:
        """Add ONNX observations only; selected clips are intentionally untouched."""
        from models import RoundReplay
        from services_replays import ReplayService

        model = voiceprint_model_status()
        if model["status"] != "ready":
            raise RuntimeError("voiceprint model is not installed")
        replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if replay is None or replay.replay_format != "nwdemo-v2":
            raise ValueError("voiceprints accept only ready nwdemo-v2 replays")
        path = Path(ReplayService(self.db).get_file_path(replay))
        stats = collect_nwdemo_voice_stats(
            path,
            allow_empty_world=replay.replay_kind in {"lobby", "lobby_dump"},
        )
        if progress_callback is not None:
            progress_callback("reading", 10, speakers_seen=len({key[0] for key in stats}))
        samples = select_nwdemo_voiceprint_packets(
            path,
            stats,
            allow_empty_world=replay.replay_kind in {"lobby", "lobby_dump"},
        )
        session = voiceprint_onnx_session(str(model["path"]))
        with tempfile.TemporaryDirectory(prefix="nwdemo-voiceprint-") as temp_dir:
            temporary = Path(temp_dir)
            steam_ids = sorted({key[0] for key in samples})
            for index, steam_id in enumerate(steam_ids, start=1):
                player_packets = samples.get((steam_id, False), []) or samples.get((steam_id, True), [])
                if not player_packets:
                    continue
                self._update_onnx_fingerprint(replay, steam_id, player_packets, temporary, session)
                if progress_callback is not None:
                    progress_callback(
                        "embedding",
                        10 + int(index * 85 / max(1, len(steam_ids))),
                        speakers_seen=len(steam_ids),
                        speakers_completed=index,
                    )
        replay.voice_profile_error = None
        self.db.commit()
        return {"replay_id": replay_id, "fingerprints": len(samples)}
