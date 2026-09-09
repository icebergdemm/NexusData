"""Persistent indexing of voice packets already stored in replay files.

The replay remains the audio source.  This module only walks the binary frame
stream and stores enough metadata to find a speaker's continuous fragments;
it never writes WAV/MP3 derivatives. Historical replays may use a unique
nickname from NexusData as a fallback identity hint.
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
from datetime import datetime, timedelta, timezone
import logging
from typing import BinaryIO
import unicodedata

logger = logging.getLogger(__name__)


VOICE_MAP_MEMBER = "voice-map.json"
# v2 fixes snapshot entity widths for the v4 DemoRecorder and v7
# WorldDemoStreamer formats.  Existing ready rows must be rebuilt once so a
# segment produced by the old misaligned parser is never treated as valid.
# v5 keeps the metadata index compatible with the replay parser while also
# materializing selected Opus packets into the replay-independent archive.
VOICE_INDEX_VERSION = 5
VOICE_INDEX_TEXT_LIMIT = 1000
VOICE_MERGE_GAP_MS = 1000
# ServerAsr and the replay recorder observe the same VoIP read on the game
# server. Their timestamps should be nearly identical; a wider match would
# start attributing nearby speakers by accident.
VOICE_COMMUNICATION_MATCH_TOLERANCE_MS = 1500
_MAGIC_DEMO = b"BDMO"
_MAGIC_ZIP = b"PK\x03\x04"


class VoiceIndexError(ValueError):
    """The replay cannot be safely indexed."""


class VoiceMappingError(VoiceIndexError):
    """A voice packet has no confirmed Steam/EOS identity."""


def _bounded_voice_text(value: object, limit: int = VOICE_INDEX_TEXT_LIMIT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    suffix = " … [обрезано]"
    return text[: max(0, limit - len(suffix))] + suffix


@dataclass(frozen=True)
class _DemoSource:
    stream: BinaryIO
    speaker_map: dict[int, str]


@dataclass(frozen=True)
class _ReplayFrame:
    kind: str
    time_ms: int
    speaker_id: int | None = None
    character_name: str | None = None
    voice_data: bytes | None = None
    voice_radio: bool = False


@dataclass(frozen=True)
class VoicePacket:
    """One encoded Opus packet read from a replay."""

    time_ms: int
    speaker_id: int
    data: bytes
    radio: bool = False


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


def _skip_frame(stream: BinaryIO, frame: int, version: int) -> _ReplayFrame | None:
    """Consume one frame and retain identity metadata needed by voice indexing."""
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
        time_ms = _u32(stream)
        character_id = _u16(stream)
        _read_string(stream)
        character_name = _read_string(stream)
        _skip(stream, 1)
        if version >= 7:
            _read_string(stream)
        return _ReplayFrame("spawn", time_ms, character_id, character_name)
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
        return _ReplayFrame("end", _u32(stream))
    elif frame in (0x07, 0x09):
        time_ms = _u32(stream)
        speaker_id = _u16(stream)
        radio = False
        if frame == 0x09:
            radio = bool(_u8(stream))
        length = _u16(stream)
        payload = _read_exact(stream, length)
        return _ReplayFrame("voice", time_ms, speaker_id, voice_data=payload, voice_radio=radio)
    else:
        raise VoiceIndexError(f"unsupported replay frame 0x{frame:02x}")
    return None


def _iter_replay_frames(stream: BinaryIO) -> Iterator[_ReplayFrame]:
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
        if parsed.kind == "end":
            return
        yield parsed


def _iter_voice_packets(stream: BinaryIO) -> Iterator[tuple[int, int]]:
    for frame in _iter_replay_frames(stream):
        if frame.kind == "voice":
            yield frame.time_ms, frame.speaker_id


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


def extract_voice_packets(path: str) -> tuple[list[tuple[int, int]], dict[int, str]]:
    """Read voice packet timestamps and any identity sidecar from a replay."""
    packets, mapping, _ = extract_voice_packets_with_names(path)
    return packets, mapping


def extract_voice_packet_records(
    path: str,
) -> tuple[list[VoicePacket], dict[int, str], dict[int, set[str]]]:
    """Read encoded voice packets, replay identity data, and observed names."""
    with _open_demo(path) as source:
        packets: list[VoicePacket] = []
        names_by_character: dict[int, set[str]] = {}
        voice_speakers: set[int] = set()
        for frame in _iter_replay_frames(source.stream):
            if frame.kind == "spawn" and frame.speaker_id is not None:
                if frame.character_name:
                    names_by_character.setdefault(frame.speaker_id, set()).add(frame.character_name)
                continue
            if frame.kind != "voice" or frame.speaker_id is None or frame.voice_data is None:
                continue
            packets.append(
                VoicePacket(
                    time_ms=frame.time_ms,
                    speaker_id=frame.speaker_id,
                    data=frame.voice_data,
                    radio=frame.voice_radio,
                )
            )
            voice_speakers.add(frame.speaker_id)
        mapping = dict(source.speaker_map)
    names_by_speaker = {
        speaker_id: names_by_character[speaker_id]
        for speaker_id in voice_speakers
        if speaker_id in names_by_character
    }
    return packets, mapping, names_by_speaker


def extract_voice_packets_with_names(
    path: str,
) -> tuple[list[tuple[int, int]], dict[int, str], dict[int, set[str]]]:
    """Read voice packets, sidecar identity, and names seen before each packet."""
    records, mapping, names_by_speaker = extract_voice_packet_records(path)
    return [(packet.time_ms, packet.speaker_id) for packet in records], mapping, names_by_speaker


def _segments_from_packets(
    packets: list[tuple[int, int]],
    speaker_map: Mapping[int | str, str] | None = None,
) -> list[dict]:
    mapping = _normalise_mapping(speaker_map)

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


def extract_voice_segments(
    path: str,
    speaker_map: Mapping[int | str, str] | None = None,
) -> list[dict]:
    """Extract mapped voice fragments from a raw, gzip, or ZIP replay."""
    packets, embedded_mapping = extract_voice_packets(path)
    mapping = {**embedded_mapping, **_normalise_mapping(speaker_map)}
    return _segments_from_packets(packets, mapping)


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


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _normalise_player_name(value: object) -> str:
    """Normalise display names without making different names look alike."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.split())


def _player_name_candidates(
    db,
    replay,
    speaker_names: Mapping[int | str, set[str]],
) -> dict[int, list[dict]]:
    """Return Nexus profiles matching names observed in one replay."""
    normalized_names = {
        _normalise_player_name(name)
        for names in speaker_names.values()
        for name in names
        if _normalise_player_name(name)
    }
    if not normalized_names:
        return {}

    from sqlalchemy import func
    from models import PlayerData, PlayerName

    sql_names = {name.lower() for name in normalized_names}
    historical_rows = (
        db.query(PlayerName, PlayerData)
        .join(PlayerData, PlayerData.player_id == PlayerName.player_id)
        .filter(func.lower(PlayerName.nickname).in_(sql_names))
        .all()
    )
    current_rows = (
        db.query(PlayerData)
        .filter(
            (func.lower(PlayerData.player_last_time_name).in_(sql_names))
            | (func.lower(PlayerData.forced_display_name).in_(sql_names))
        )
        .all()
    )

    recorded_at = _as_naive_utc(getattr(replay, "recorded_at", None))
    candidates_by_name: dict[str, dict[str, dict]] = {}

    def add_candidate(name: object, player: PlayerData) -> None:
        identity = str(getattr(player, "player_steamid", None) or "").strip()
        normalized = _normalise_player_name(name)
        if not identity or normalized not in normalized_names:
            return
        candidates_by_name.setdefault(normalized, {})[identity] = {
            "player_id": player.player_id,
            "steam_id": identity,
            "name": getattr(player, "display_name", None) or name,
        }

    for history, player in historical_rows:
        first_seen = _as_naive_utc(getattr(history, "first_seen", None))
        last_seen = _as_naive_utc(getattr(history, "last_seen", None))
        if recorded_at and first_seen and recorded_at < first_seen:
            continue
        if recorded_at and last_seen and recorded_at > last_seen:
            continue
        add_candidate(history.nickname, player)

    for player in current_rows:
        add_candidate(getattr(player, "player_last_time_name", None), player)
        add_candidate(getattr(player, "forced_display_name", None), player)

    return {
        int(speaker_id): sorted(
            {
                identity: candidate
                for name in names
                for identity, candidate in candidates_by_name.get(
                    _normalise_player_name(name), {}
                ).items()
            }.values(),
            key=lambda candidate: (str(candidate.get("name") or "").casefold(), candidate["steam_id"]),
        )
        for speaker_id, names in speaker_names.items()
    }


def _mapping_from_player_names(
    db,
    replay,
    speaker_names: Mapping[int | str, set[str]],
) -> dict[int, str]:
    """Recover old mappings when one replay nickname has one Nexus profile.

    This is deliberately a unique-match fallback. A historical nickname shared
    by two profiles, or a character that changed to conflicting names, remains
    unmapped for an administrator instead of becoming false evidence.
    """
    mapping: dict[int, str] = {}
    for speaker_id, candidates in _player_name_candidates(db, replay, speaker_names).items():
        identities = {candidate["steam_id"] for candidate in candidates}
        if len(identities) == 1:
            mapping[int(speaker_id)] = next(iter(identities))
    return mapping


def _mapping_from_voice_communications(
    db,
    replay,
    packets: list[tuple[int, int]],
) -> dict[int, str]:
    """Recover old mappings from ServerAsr's exact voice event timestamps.

    Before ``voice-map.json`` existed, the replay still had the packet stream
    but no character-id identity. ServerAsr independently recorded the first
    packet time and the real Steam/EOS ID. Both observers run from the same
    VoIP read, so a narrow timestamp match is safe enough to use as a
    historical recovery path. Ambiguous or conflicting matches are discarded;
    a false profile attribution is worse than a visible indexing error.
    """
    recorded_at = _as_naive_utc(getattr(replay, "recorded_at", None))
    server_name = str(getattr(replay, "server_name", None) or "").strip()
    if not recorded_at or not server_name or not packets:
        return {}

    from models import PlayerCommunication

    packet_times: dict[int, list[int]] = {}
    for time_ms, speaker_id in packets:
        if speaker_id <= 0:
            continue
        packet_times.setdefault(speaker_id, []).append(time_ms)
    if not packet_times:
        return {}

    max_packet_ms = max(time_ms for time_ms, _ in packets)
    window_start = recorded_at - timedelta(seconds=10)
    window_end = recorded_at + timedelta(milliseconds=max_packet_ms + 10000)
    events = (
        db.query(PlayerCommunication)
        .filter(
            PlayerCommunication.source == "voice",
            PlayerCommunication.server_name == server_name,
            PlayerCommunication.player_steamid.isnot(None),
            PlayerCommunication.occurred_at >= window_start,
            PlayerCommunication.occurred_at <= window_end,
        )
        .order_by(PlayerCommunication.occurred_at.asc(), PlayerCommunication.id.asc())
        .all()
    )

    candidates: dict[int, set[str]] = {}
    for event in events:
        occurred_at = _as_naive_utc(event.occurred_at)
        steam_id = str(event.player_steamid or "").strip()
        if not occurred_at or not steam_id:
            continue
        offset_ms = int(round((occurred_at - recorded_at).total_seconds() * 1000))
        nearest: list[tuple[int, int]] = []
        for speaker_id, times in packet_times.items():
            distance = min(abs(time_ms - offset_ms) for time_ms in times)
            if distance <= VOICE_COMMUNICATION_MATCH_TOLERANCE_MS:
                nearest.append((distance, speaker_id))
        if not nearest:
            continue
        nearest.sort()
        best_distance, best_speaker = nearest[0]
        # Two speakers close to the same ASR event mean the timestamp cannot
        # identify the source reliably. Do not manufacture a winner.
        if len(nearest) > 1 and nearest[1][0] - best_distance <= 250:
            continue
        candidates.setdefault(best_speaker, set()).add(steam_id)

    return {
        speaker_id: next(iter(steam_ids))
        for speaker_id, steam_ids in candidates.items()
        if len(steam_ids) == 1
    }


class VoiceIndexService:
    """Build and read the durable replay voice index."""

    def __init__(self, db):
        self.db = db

    def _mapping_for_replay_with_sources(
        self,
        replay,
        packets: list[tuple[int, int]] | None = None,
        speaker_names: Mapping[int | str, set[str]] | None = None,
    ) -> tuple[dict[int, str], dict[int, str], dict[int, list[dict]]]:
        mapping: dict[int, str] = {}
        sources: dict[int, str] = {}
        exact_mappings = (
            ("world_round", _mapping_from_world_round(self.db, replay)),
            ("round_event", _mapping_from_round_event(self.db, replay)),
        )
        for source, resolved in exact_mappings:
            for speaker_id, steam_id in resolved.items():
                mapping[speaker_id] = steam_id
                sources[speaker_id] = source

        if packets:
            for speaker_id, steam_id in _mapping_from_voice_communications(
                self.db, replay, packets
            ).items():
                mapping.setdefault(speaker_id, steam_id)
                sources.setdefault(speaker_id, "asr_time")
        candidates = _player_name_candidates(self.db, replay, speaker_names or {})
        for speaker_id, profiles in candidates.items():
            identities = {profile["steam_id"] for profile in profiles}
            if len(identities) != 1:
                continue
            steam_id = next(iter(identities))
            mapping.setdefault(speaker_id, steam_id)
            sources.setdefault(speaker_id, "nickname")
        return mapping, sources, candidates

    def _mapping_for_replay(
        self,
        replay,
        packets: list[tuple[int, int]] | None = None,
        speaker_names: Mapping[int | str, set[str]] | None = None,
    ) -> dict[int, str]:
        mapping, _, _ = self._mapping_for_replay_with_sources(
            replay, packets, speaker_names
        )
        return mapping

    @staticmethod
    def _speaker_stats(packets: list[tuple[int, int]]) -> dict[int, dict]:
        stats: dict[int, dict] = {}
        for time_ms, speaker_id in packets:
            row = stats.setdefault(
                speaker_id,
                {"packet_count": 0, "start_ms": time_ms, "end_ms": time_ms},
            )
            row["packet_count"] += 1
            row["start_ms"] = min(row["start_ms"], time_ms)
            row["end_ms"] = max(row["end_ms"], time_ms)
        return stats

    @staticmethod
    def _segments_for_mapping(
        packets: list[tuple[int, int]], mapping: Mapping[int, str]
    ) -> list[dict]:
        mapped_packets = [packet for packet in packets if packet[1] in mapping]
        return [
            {"steam_id": mapping[row["speaker_id"]], **row}
            for row in group_voice_packets(mapped_packets)
        ]

    def _save_speaker_details(
        self,
        replay,
        packets: list[tuple[int, int]],
        speaker_names: Mapping[int | str, set[str]],
        candidates: Mapping[int, list[dict]],
        mapping: Mapping[int, str],
        mapping_sources: Mapping[int, str],
        manual_rows: Mapping[int, object],
        skipped_speakers: set[int],
    ) -> None:
        from models import ReplayVoiceSpeaker

        stats = self._speaker_stats(packets)
        existing = dict(manual_rows)
        use_mysql_upsert = self.db.get_bind().dialect.name == "mysql"
        for speaker_id, row_stats in stats.items():
            row = existing.get(speaker_id)
            if row is None:
                initial_status = "mapped" if speaker_id in mapping else "unresolved"
                initial_source = mapping_sources.get(speaker_id) if speaker_id in mapping else None
                initial_steam_id = mapping.get(speaker_id)
                initial_values = {
                    "replay_id": replay.id,
                    "speaker_id": speaker_id,
                    "names": sorted(speaker_names.get(speaker_id, set())),
                    "candidate_profiles": candidates.get(speaker_id, []),
                    "steam_id": initial_steam_id,
                    "resolution_status": initial_status,
                    "mapping_source": initial_source,
                    "packet_count": row_stats["packet_count"],
                    "start_ms": row_stats["start_ms"],
                    "end_ms": row_stats["end_ms"],
                }
                if speaker_id in skipped_speakers:
                    initial_values.update(
                        steam_id=None,
                        resolution_status="skipped",
                        mapping_source="manual_skip",
                    )
                if use_mysql_upsert:
                    from sqlalchemy.dialects.mysql import insert as mysql_insert

                    statement = mysql_insert(ReplayVoiceSpeaker).values(**initial_values)
                    self.db.execute(statement.on_duplicate_key_update(
                        names=statement.inserted.names,
                        candidate_profiles=statement.inserted.candidate_profiles,
                        packet_count=statement.inserted.packet_count,
                        start_ms=statement.inserted.start_ms,
                        end_ms=statement.inserted.end_ms,
                    ))
                    # Another worker inserted the same speaker between our
                    # read and write. The unique key is expected here; the
                    # upsert keeps that race from aborting the replay.
                    continue
                row = ReplayVoiceSpeaker(replay_id=replay.id, speaker_id=speaker_id)
                self.db.add(row)
            row.names = sorted(speaker_names.get(speaker_id, set()))
            row.candidate_profiles = candidates.get(speaker_id, [])
            row.packet_count = row_stats["packet_count"]
            row.start_ms = row_stats["start_ms"]
            row.end_ms = row_stats["end_ms"]

            is_manual = row.resolution_status in ("mapped", "skipped") and row.mapping_source in (
                "manual",
                "manual_skip",
            )
            if is_manual:
                if row.resolution_status == "skipped":
                    skipped_speakers.add(speaker_id)
                continue

            if speaker_id in mapping:
                row.steam_id = mapping[speaker_id]
                row.resolution_status = "mapped"
                row.mapping_source = mapping_sources.get(speaker_id)
            else:
                row.steam_id = None
                row.resolution_status = "unresolved"
                row.mapping_source = None

    def _manual_speaker_rows(self, replay_id: int) -> dict[int, object]:
        from models import ReplayVoiceSpeaker

        return {
            row.speaker_id: row
            for row in self.db.query(ReplayVoiceSpeaker)
            .filter(ReplayVoiceSpeaker.replay_id == replay_id)
            .all()
        }

    def index_replay(self, replay_id: int, _retry: int = 0) -> dict:
        from models import ReplayVoiceSegment, RoundReplay
        from services_voice_archive import VOICE_ARCHIVE_VERSION, VoiceArchiveService

        replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        if not replay:
            return {"status": "missing", "replay_id": replay_id}
        if (
            replay.voice_index_status == "ready"
            and replay.voice_index_version == VOICE_INDEX_VERSION
            and replay.voice_archive_version == VOICE_ARCHIVE_VERSION
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
            packet_records, embedded_mapping, speaker_names = extract_voice_packet_records(path)
            packets = [(packet.time_ms, packet.speaker_id) for packet in packet_records]
            inferred_mapping, mapping_sources, candidates = self._mapping_for_replay_with_sources(
                replay, packets, speaker_names
            )
            manual_rows = self._manual_speaker_rows(replay.id)
            mapping = dict(embedded_mapping)
            mapping_sources_for_segments = {speaker_id: "embedded" for speaker_id in mapping}
            mapping.update(inferred_mapping)
            mapping_sources_for_segments.update(mapping_sources)
            skipped_speakers = {
                speaker_id
                for speaker_id, row in manual_rows.items()
                if row.resolution_status == "skipped"
            }
            for speaker_id, row in manual_rows.items():
                is_manual_mapping = (
                    row.mapping_source == "manual"
                    or row.decided_by_admin_id is not None
                )
                if row.resolution_status == "mapped" and row.steam_id and is_manual_mapping:
                    mapping[speaker_id] = row.steam_id
                    mapping_sources_for_segments[speaker_id] = "manual"
                elif row.resolution_status == "skipped" and (
                    row.mapping_source == "manual_skip"
                    or row.decided_by_admin_id is not None
                ):
                    mapping.pop(speaker_id, None)
                    mapping_sources_for_segments.pop(speaker_id, None)

            speaker_ids = {speaker_id for _, speaker_id in packets}
            unresolved_speaker_ids = sorted(speaker_ids - set(mapping) - skipped_speakers)
            skipped_speaker_ids = sorted(speaker_ids & skipped_speakers)
            segments = self._segments_for_mapping(packets, mapping)
            self._save_speaker_details(
                replay,
                packets,
                speaker_names,
                candidates,
                mapping,
                mapping_sources_for_segments,
                manual_rows,
                skipped_speakers,
            )

            previous_steam_ids = {
                steam_id
                for (steam_id,) in self.db.query(ReplayVoiceSegment.steam_id)
                .filter(ReplayVoiceSegment.replay_id == replay.id)
                .all()
                if steam_id
            }

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
            self.db.flush()
            archive_service = VoiceArchiveService(self.db)
            removed_archive_paths = archive_service.remove_replay_clips(replay.id)
            affected_steam_ids = previous_steam_ids | {
                segment["steam_id"] for segment in segments
            }
            archive_result = archive_service.reconcile_steam_ids(
                affected_steam_ids,
                current_replay_id=replay.id,
                current_packets=packet_records,
            )
            partial = bool(unresolved_speaker_ids or skipped_speaker_ids)
            status = "partial" if partial else "ready"
            replay.voice_index_status = status
            replay.voice_index_version = VOICE_INDEX_VERSION
            replay.voice_archive_version = VOICE_ARCHIVE_VERSION
            replay.voice_indexed_at = datetime.utcnow()
            replay.voice_index_error = (
                "unresolved speaker_id(s): " + ", ".join(map(str, unresolved_speaker_ids))
                if unresolved_speaker_ids else None
            )
            self.db.commit()
            archive_service.remove_unreferenced_files(removed_archive_paths)
            return {
                "status": status,
                "replay_id": replay.id,
                "segments": len(segments),
                "profile_count": len({segment["steam_id"] for segment in segments}),
                "profile_ids": sorted({segment["steam_id"] for segment in segments}),
                "archive_clips": archive_result["materialized"],
                "archive_selected": archive_result["selected"],
                "unresolved_speaker_ids": unresolved_speaker_ids,
                "skipped_speaker_ids": skipped_speaker_ids,
            }
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError

            if (
                _retry < 2
                and isinstance(exc, IntegrityError)
                and any(
                    key in str(exc)
                    for key in ("uq_replay_voice_speaker", "uq_replay_voice_segment")
                )
            ):
                # A legacy scheduler run or a manual reindex can overlap this
                # worker. Retry after the competing transaction has committed;
                # the MySQL upsert above handles speaker rows, while this also
                # covers segment inserts and deployments where an ORM insert
                # was already flushed before the upsert path was reached.
                self.db.rollback()
                logger.warning("Retrying replay %s after concurrent speaker-row insert", replay_id)
                try:
                    return self.index_replay(replay_id, _retry=_retry + 1)
                except IntegrityError:
                    # The bounded retry below records the final compact error.
                    pass
            self.db.rollback()
            replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
            if replay:
                replay.voice_index_status = "error"
                replay.voice_index_error = _bounded_voice_text(exc)
                self.db.commit()
            logger.exception("Voice index failed for replay %s", replay_id)
            return {"status": "error", "replay_id": replay_id, "error": _bounded_voice_text(exc)}

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
    from services_voice_archive import VOICE_ARCHIVE_VERSION
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
                    RoundReplay.voice_index_status.in_(["pending", "running", "error"]),
                    RoundReplay.voice_index_version != VOICE_INDEX_VERSION,
                    RoundReplay.voice_archive_version != VOICE_ARCHIVE_VERSION,
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
            message=_bounded_voice_text(message),
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
        .filter(VoiceIndexRun.status.in_(["pending", "running", "cancelling"]))
        .order_by(VoiceIndexRun.created_at.desc())
        .first()
    )
    # ``cancelling`` was used by the first stop implementation. It can be
    # left behind when the process restarts between the HTTP request and the
    # worker's next database refresh, so recover it as a terminal state rather
    # than making the page wait for the stale-run timeout.
    if run and run.status == "cancelling":
        now = datetime.utcnow()
        run.status = "cancelled"
        run.finished_at = run.finished_at or now
        run.heartbeat_at = now
        run.last_message = "Индексация остановлена"
        _voice_run_log(db, run, status="cancelled", message=run.last_message)
        db.commit()
        return None
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
        "partial_count": run.partial_count,
        "error_count": run.error_count,
        "segment_count": run.segment_count,
        "profile_count": run.profile_count,
        "archive_clip_count": run.archive_clip_count,
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
    from services_voice_archive import VOICE_ARCHIVE_VERSION

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
        "archive_version": VOICE_ARCHIVE_VERSION,
    }


def task_run_voice_backfill(run_id: int, session_factory=None) -> dict:
    """Index all retained voice replays for one operator-visible run."""
    from database import SessionLocal
    from models import RoundReplay, VoiceIndexRun, VoiceIndexRunLog
    from services_voice_archive import VOICE_ARCHIVE_VERSION
    from services_replays import get_retention_days
    from sqlalchemy import or_

    factory = session_factory or SessionLocal
    db = factory()
    run = None
    try:
        run = db.query(VoiceIndexRun).filter(VoiceIndexRun.id == run_id).first()
        if not run:
            return {"status": "missing", "run_id": run_id}
        if run.status == "cancelled":
            return {"status": "cancelled", "run_id": run_id}
        if run.status not in ("pending", "running", "cancelling"):
            return {"status": run.status, "run_id": run_id}

        if run.status == "cancelling":
            now = datetime.utcnow()
            run.status = "cancelled"
            run.finished_at = now
            run.heartbeat_at = now
            run.last_message = "Индексация остановлена до начала следующего реплея"
            _voice_run_log(db, run, status="cancelled", message=run.last_message)
            db.commit()
            return _voice_run_dict(run)

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
                    RoundReplay.voice_index_status.in_(["pending", "running", "error", "partial"]),
                    RoundReplay.voice_index_version != VOICE_INDEX_VERSION,
                    RoundReplay.voice_archive_version != VOICE_ARCHIVE_VERSION,
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
        seen_profile_ids = set()
        for replay_id in replay_ids:
            db.refresh(run)
            if run.status in ("cancelling", "cancelled"):
                if run.status == "cancelled":
                    return _voice_run_dict(run)
                now = datetime.utcnow()
                run.status = "cancelled"
                run.finished_at = now
                run.heartbeat_at = now
                run.last_message = "Индексация остановлена до начала следующего реплея"
                _voice_run_log(db, run, status="cancelled", message=run.last_message)
                db.commit()
                return _voice_run_dict(run)

            result = service.index_replay(replay_id)
            status = result.get("status", "error")
            is_error = status == "error"
            is_partial = status == "partial"
            run.processed += 1
            run.last_replay_id = replay_id
            run.segment_count += int(result.get("segments") or 0)
            run.archive_clip_count += int(result.get("archive_clips") or 0)
            seen_profile_ids.update(result.get("profile_ids") or [])
            run.profile_count = len(seen_profile_ids)
            if is_error:
                run.last_message = _bounded_voice_text(
                    f"Реплей #{replay_id}: ошибка — {result.get('error') or 'неизвестная ошибка'}"
                )
            elif is_partial:
                unresolved = result.get("unresolved_speaker_ids") or []
                skipped = result.get("skipped_speaker_ids") or []
                unresolved_text = ", ".join(str(value) for value in unresolved[:8])
                if len(unresolved) > 8:
                    unresolved_text += ", …"
                suffix = f"; ждёт сопоставления: {unresolved_text}" if unresolved else ""
                if skipped:
                    suffix += f"; пропущено: {len(skipped)}"
                run.last_message = (
                    f"Реплей #{replay_id}: частично — {result.get('segments', 0)} фрагм., "
                    f"{result.get('profile_count', 0)} профилей, "
                    f"в архиве {result.get('archive_selected', 0)} клип. "
                    f"Новых: {result.get('archive_clips', 0)}."
                    f"{suffix}"
                )
            else:
                run.last_message = (
                    f"Реплей #{replay_id}: готов — {result.get('segments', 0)} фрагм., "
                    f"{result.get('profile_count', 0)} профилей, "
                    f"в архиве {result.get('archive_selected', 0)} клип. "
                    f"Новых: {result.get('archive_clips', 0)}."
                )
            run.heartbeat_at = datetime.utcnow()
            if is_error:
                run.error_count += 1
            elif is_partial:
                run.partial_count += 1
            else:
                run.ready_count += 1
            _voice_run_log(
                db,
                run,
                replay_id=replay_id,
                status=status,
                level="error" if is_error else ("warning" if is_partial else "info"),
                message=run.last_message,
            )
            db.commit()

        db.refresh(run)
        if run.status in ("cancelling", "cancelled"):
            if run.status == "cancelled":
                return _voice_run_dict(run)
            run.status = "cancelled"
            run.finished_at = datetime.utcnow()
            run.heartbeat_at = run.finished_at
            run.last_message = "Индексация остановлена"
            _voice_run_log(db, run, status="cancelled", message=run.last_message)
            db.commit()
            return _voice_run_dict(run)

        run.status = "completed"
        run.finished_at = datetime.utcnow()
        run.heartbeat_at = run.finished_at
        run.last_message = (
            f"Индексация завершена: готово {run.ready_count}, частично {run.partial_count}, "
            f"ошибок {run.error_count}, новых архивных клипов {run.archive_clip_count}"
        )
        db.commit()
        return _voice_run_dict(run)
    except Exception as exc:
        db.rollback()
        run = db.query(VoiceIndexRun).filter(VoiceIndexRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.error = _bounded_voice_text(exc)
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
    from models import ReplayVoiceSpeaker, RoundReplay, VoiceIndexRunLog

    query = db.query(VoiceIndexRunLog).filter(VoiceIndexRunLog.run_id == run_id)
    total = query.count()
    rows = (
        query.order_by(VoiceIndexRunLog.created_at.desc(), VoiceIndexRunLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    result = []
    for row in rows:
        unresolved_count = 0
        replay_status = None
        if row.replay_id is not None:
            unresolved_count = db.query(ReplayVoiceSpeaker).filter(
                ReplayVoiceSpeaker.replay_id == row.replay_id,
                ReplayVoiceSpeaker.resolution_status == "unresolved",
            ).count()
            replay_status = db.query(RoundReplay.voice_index_status).filter(
                RoundReplay.id == row.replay_id,
            ).scalar()
        result.append({
            "id": row.id,
            "run_id": row.run_id,
            "replay_id": row.replay_id,
            "level": row.level,
            "status": row.status,
            "message": row.message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "unresolved_count": unresolved_count,
            "needs_resolution": unresolved_count > 0,
            "replay_status": replay_status,
        })
    return result, total
