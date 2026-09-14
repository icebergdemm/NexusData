"""Replay-independent voice profile extraction primitives.

The module starts with format-neutral packet and selection helpers. Replay I/O,
database orchestration, and the external Concentus decoder are layered on top
so the deterministic parts remain cheap to test.
"""

from __future__ import annotations

import datetime as _datetime
import gzip
import hashlib
import os
import json
import subprocess
import struct
import tempfile
import zipfile
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Mapping, Sequence


VPK1_MAGIC = b"VPK1"
VPK1_VERSION = 1
NDVC_MAGIC = b"NDVC"
NDVC_VERSION = 1
DEMO_MAGIC = b"BDMO"
VOICE_MAP_MEMBER = "voice-map.json"
MAX_OPUS_PACKET_SIZE = 40
VOICE_MERGE_GAP_MS = 1000
VOICE_PROFILE_LIMIT = 25
VOICE_PROFILE_EARLY_COUNT = 5
VOICE_PROFILE_MIDDLE_COUNT = 10
VOICE_PROFILE_LATEST_COUNT = 10
VOICE_PROFILE_ARCHIVE_VERSION = 2


@dataclass(frozen=True)
class VoicePacket:
    time_ms: int
    speaker_id: int
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
    """Select the deterministic 5-earliest/10-middle/10-latest profile."""
    ordered = sorted(candidates, key=_candidate_sort_key)
    if len(ordered) <= VOICE_PROFILE_LIMIT:
        return ordered

    early = ordered[:VOICE_PROFILE_EARLY_COUNT]
    latest = ordered[-VOICE_PROFILE_LATEST_COUNT:]
    middle_pool = ordered[VOICE_PROFILE_EARLY_COUNT:-VOICE_PROFILE_LATEST_COUNT]
    middle = _select_evenly(middle_pool, VOICE_PROFILE_MIDDLE_COUNT)
    return [*early, *middle, *latest]


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


def _extract_nwdemo_voice_metadata(path: Path) -> ReplayVoiceSegmentExtraction:
    segments: list[dict] = []
    names_by_speaker: dict[int, set[str]] = {}
    try:
        with zipfile.ZipFile(path, "r") as archive, archive.open("voice.bin", "r") as stream:
            active: dict[tuple[int, bool], dict] = {}
            while True:
                header = stream.read(6)
                if not header:
                    break
                if len(header) != 6:
                    raise VoiceProfileParseError("truncated nwdemo voice packet")
                time_ms, speaker_id = struct.unpack("<IH", header)
                _steam_id = _read_dotnet_string(stream)
                radio_raw = stream.read(1)
                length_raw = stream.read(2)
                if len(radio_raw) != 1 or len(length_raw) != 2:
                    raise VoiceProfileParseError("truncated nwdemo voice packet header")
                radio = bool(radio_raw[0])
                length = struct.unpack("<H", length_raw)[0]
                payload = stream.read(length)
                if len(payload) != length:
                    raise VoiceProfileParseError("truncated nwdemo voice payload")
                if not speaker_id:
                    continue
                key = (speaker_id, radio)
                previous = active.get(key)
                if previous and time_ms - previous["end_ms"] <= VOICE_MERGE_GAP_MS:
                    previous["end_ms"] = time_ms
                    previous["packet_count"] += 1
                    previous["radio_packet_count"] += int(radio)
                    continue
                segment = {"speaker_id": speaker_id, "start_ms": time_ms, "end_ms": time_ms,
                           "packet_count": 1, "radio_packet_count": int(radio)}
                segments.append(segment)
                active[key] = segment
    except KeyError:
        return ReplayVoiceSegmentExtraction(7, [], {}, {}, False, ["nwdemo archive has no voice.bin"])
    except (OSError, zipfile.BadZipFile) as exc:
        raise VoiceProfileParseError(f"invalid nwdemo voice archive: {exc}") from exc
    return ReplayVoiceSegmentExtraction(7, segments, {}, names_by_speaker, False, [])


def extract_voice_packets_for_speaker(
    path: str | os.PathLike[str],
    speaker_id: int,
) -> list[VoicePacket]:
    """Extract Opus packets for one speaker without retaining other speakers."""
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
            file.write(struct.pack("<IH", relative_time, len(packet.data)))
            file.write(packet.data)
    return destination.stat().st_size
