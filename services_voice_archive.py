"""Durable, replay-independent voice clip storage.

The archive keeps the original encoded Opus packets instead of producing WAV or
MP3 derivatives.  The small NDVC container is intentionally boring: a header
followed by relative packet timestamps and length-prefixed packet bytes.  The
frontend can decode it with the same WebCodecs API used by the replay player.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from services_voice_index import VOICE_MERGE_GAP_MS, VoicePacket, extract_voice_packet_records
from services_replays import get_upload_dir


NDVC_MAGIC = b"NDVC"
NDVC_VERSION = 1
VOICE_ARCHIVE_VERSION = 1
VOICE_ARCHIVE_LIMIT = 25
VOICE_ARCHIVE_EARLY_COUNT = 5
VOICE_ARCHIVE_LATEST_COUNT = 10
VOICE_ARCHIVE_MIDDLE_COUNT = 10


class VoiceArchiveError(ValueError):
    """The archive file or archive metadata cannot be safely processed."""


def _read_exact(file, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise VoiceArchiveError("truncated NDVC file")
    return data


def write_ndvc(path: str | os.PathLike[str], packets: Sequence[VoicePacket]) -> int:
    """Write packets to an NDVC file and return its byte size."""
    if len(packets) > 0xFFFFFFFF:
        raise VoiceArchiveError("too many packets")
    first_time = packets[0].time_ms if packets else 0
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as file:
        file.write(NDVC_MAGIC)
        file.write(struct.pack("<BI", NDVC_VERSION, len(packets)))
        for packet in packets:
            relative_time = max(0, packet.time_ms - first_time)
            if relative_time > 0xFFFFFFFF:
                raise VoiceArchiveError("packet timestamp is too large")
            if len(packet.data) > 0xFFFF:
                raise VoiceArchiveError("voice packet is too large")
            file.write(struct.pack("<IH", relative_time, len(packet.data)))
            file.write(packet.data)
    return destination.stat().st_size


def read_ndvc(path: str | os.PathLike[str]) -> list[tuple[int, bytes]]:
    """Read an NDVC file for tests and small diagnostic tools."""
    with Path(path).open("rb") as file:
        if _read_exact(file, 4) != NDVC_MAGIC:
            raise VoiceArchiveError("invalid NDVC magic")
        version, count = struct.unpack("<BI", _read_exact(file, 5))
        if version != NDVC_VERSION:
            raise VoiceArchiveError(f"unsupported NDVC version {version}")
        packets = []
        for _ in range(count):
            relative_time, length = struct.unpack("<IH", _read_exact(file, 6))
            packets.append((relative_time, _read_exact(file, length)))
        if file.read(1):
            raise VoiceArchiveError("trailing bytes in NDVC file")
        return packets


def _candidate_sort_key(candidate: Mapping[str, object]) -> tuple:
    return (
        candidate.get("recorded_at") or candidate.get("uploaded_at") or datetime.min,
        int(candidate.get("source_replay_id") or candidate.get("replay_id") or 0),
        int(candidate.get("start_ms") or candidate.get("time_ms") or 0),
        int(candidate.get("segment_id") or candidate.get("id") or 0),
    )


def _select_evenly(candidates: Sequence[Mapping[str, object]], count: int) -> list[Mapping[str, object]]:
    if count <= 0 or not candidates:
        return []
    if len(candidates) <= count:
        return list(candidates)
    if count == 1:
        return [candidates[len(candidates) // 2]]
    selected = []
    used: set[int] = set()
    for index in range(count):
        position = round(index * (len(candidates) - 1) / (count - 1))
        if position not in used:
            used.add(position)
            selected.append(candidates[position])
    return selected


def select_voice_archive_candidates(candidates: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Select the deterministic 5 early / 10 middle / 10 latest clips."""
    ordered = sorted(candidates, key=_candidate_sort_key)
    if len(ordered) <= VOICE_ARCHIVE_LIMIT:
        return ordered

    early = ordered[:VOICE_ARCHIVE_EARLY_COUNT]
    latest = ordered[-VOICE_ARCHIVE_LATEST_COUNT:]
    middle_pool = ordered[VOICE_ARCHIVE_EARLY_COUNT:-VOICE_ARCHIVE_LATEST_COUNT]
    middle = _select_evenly(middle_pool, VOICE_ARCHIVE_MIDDLE_COUNT)
    return [*early, *middle, *latest]


def make_archive_key(
    steam_id: str,
    replay_id: int,
    speaker_id: int,
    start_ms: int,
    end_ms: int,
) -> str:
    payload = json.dumps(
        [steam_id, replay_id, speaker_id, start_ms, end_ms],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class VoiceArchivePath:
    root: str

    def for_key(self, archive_key: str) -> str:
        if len(archive_key) != 64 or any(char not in "0123456789abcdef" for char in archive_key):
            raise VoiceArchiveError("invalid archive key")
        return os.path.join(self.root, "voice_archive", f"{archive_key}.ndvc")


def group_voice_packet_records(
    packets: Sequence[VoicePacket],
    *,
    merge_gap_ms: int = VOICE_MERGE_GAP_MS,
) -> list[dict]:
    """Group encoded packets exactly like the metadata voice index."""
    segments: list[dict] = []
    last_by_speaker: dict[int, dict] = {}
    for packet in packets:
        previous = last_by_speaker.get(packet.speaker_id)
        if previous and packet.time_ms - previous["end_ms"] <= merge_gap_ms:
            previous["end_ms"] = packet.time_ms
            previous["packet_count"] += 1
            previous["packet_records"].append(packet)
            continue
        segment = {
            "speaker_id": packet.speaker_id,
            "start_ms": packet.time_ms,
            "end_ms": packet.time_ms,
            "packet_count": 1,
            "packet_records": [packet],
        }
        segments.append(segment)
        last_by_speaker[packet.speaker_id] = segment
    return sorted(segments, key=lambda row: (row["start_ms"], row["speaker_id"]))


class VoiceArchiveService:
    """Materialize and read the selected replay-independent voice clips."""

    def __init__(self, db, root: str | None = None):
        self.db = db
        self.paths = VoiceArchivePath(root or get_upload_dir(db))

    def _path_for_clip(self, clip) -> str:
        return self.paths.for_key(clip.archive_key)

    def _source_packets(self, replay, packet_cache: dict[int, list[VoicePacket]]) -> list[VoicePacket]:
        if replay.id in packet_cache:
            return packet_cache[replay.id]
        from services_replays import ReplayService

        path = ReplayService(self.db).get_file_path(replay)
        if not os.path.isfile(path):
            raise VoiceArchiveError(f"replay file is missing for replay {replay.id}")
        packets, _mapping, _names = extract_voice_packet_records(path)
        packet_cache[replay.id] = packets
        return packets

    def _materialize_candidate(
        self,
        candidate: Mapping[str, object],
        packet_cache: dict[int, list[VoicePacket]],
    ):
        from models import RoundReplay, VoiceArchiveClip

        archive_key = str(candidate["archive_key"])
        clip = (
            self.db.query(VoiceArchiveClip)
            .filter(VoiceArchiveClip.archive_key == archive_key)
            .first()
        )
        replay_id = int(candidate["source_replay_id"])
        replay = self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()
        packet_records = list(candidate.get("packet_records") or [])
        storage_path = self.paths.for_key(archive_key)
        if clip is not None and not packet_records and os.path.isfile(storage_path):
            clip.archive_version = VOICE_ARCHIVE_VERSION
            clip.storage_size = os.path.getsize(storage_path)
            return clip, False
        if not packet_records and replay is not None:
            grouped = group_voice_packet_records(self._source_packets(replay, packet_cache))
            for segment in grouped:
                if (
                    segment["speaker_id"] == int(candidate["speaker_id"])
                    and segment["start_ms"] == int(candidate["start_ms"])
                    and segment["end_ms"] == int(candidate["end_ms"])
                ):
                    packet_records = segment["packet_records"]
                    break
        if not packet_records:
            return None, False

        old_file_exists = os.path.isfile(storage_path)
        if not old_file_exists:
            temporary_path = f"{storage_path}.tmp"
            write_ndvc(temporary_path, packet_records)
            os.replace(temporary_path, storage_path)
        storage_size = os.path.getsize(storage_path)
        if clip is None:
            clip = VoiceArchiveClip(
                archive_key=archive_key,
                steam_id=str(candidate["steam_id"]),
                source_replay_id=replay_id,
                source_replay_filename=str(candidate["source_replay_filename"]),
                server_name=candidate.get("server_name"),
                recorded_at=candidate.get("recorded_at"),
                uploaded_at=candidate.get("uploaded_at"),
                speaker_id=int(candidate["speaker_id"]),
                start_ms=int(candidate["start_ms"]),
                end_ms=int(candidate["end_ms"]),
                packet_count=int(candidate["packet_count"]),
                storage_filename=os.path.relpath(storage_path, self.paths.root),
                storage_size=storage_size,
                archive_version=VOICE_ARCHIVE_VERSION,
            )
            self.db.add(clip)
            self.db.flush()
            return clip, True

        clip.storage_size = storage_size
        clip.archive_version = VOICE_ARCHIVE_VERSION
        return clip, not old_file_exists

    def remove_replay_clips(self, replay_id: int) -> list[str]:
        """Drop materialized clips for a replay before its mapping is rebuilt.

        A moderator can move a speaker from one profile to another. Keeping the
        old clip around would make the previous profile retain an attribution
        that is no longer supported by the current speaker mapping.
        """
        from models import VoiceArchiveClip, VoiceArchiveSelection

        clips = self.db.query(VoiceArchiveClip).filter(
            VoiceArchiveClip.source_replay_id == replay_id,
        ).all()
        paths = [self._path_for_clip(clip) for clip in clips]
        clip_ids = [clip.id for clip in clips]
        if clip_ids:
            self.db.query(VoiceArchiveSelection).filter(
                VoiceArchiveSelection.clip_id.in_(clip_ids),
            ).delete(synchronize_session=False)
        for clip in clips:
            self.db.delete(clip)
        if clips:
            self.db.flush()
        return paths

    def remove_unreferenced_files(self, paths: Iterable[str]) -> None:
        """Delete files removed by a committed rebuild, never live files."""
        from models import VoiceArchiveClip

        relative_names = {
            os.path.relpath(path, self.paths.root)
            for path in paths
        }
        if not relative_names:
            return
        referenced = {
            storage_filename
            for (storage_filename,) in self.db.query(VoiceArchiveClip.storage_filename).filter(
                VoiceArchiveClip.storage_filename.in_(relative_names),
            ).all()
        }
        for path, relative_name in (
            (path, os.path.relpath(path, self.paths.root))
            for path in paths
        ):
            if relative_name in referenced:
                continue
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def reconcile_steam_id(
        self,
        steam_id: str,
        *,
        current_replay_id: int | None = None,
        current_packets: Sequence[VoicePacket] | None = None,
    ) -> dict[str, int]:
        """Rebuild one player's 25 slots without requiring replay retention."""
        from models import ReplayVoiceSegment, RoundReplay, VoiceArchiveClip, VoiceArchiveSelection

        query = (
            self.db.query(ReplayVoiceSegment, RoundReplay)
            .join(RoundReplay, RoundReplay.id == ReplayVoiceSegment.replay_id)
            .filter(ReplayVoiceSegment.steam_id == steam_id)
        )
        candidates: dict[str, dict] = {}
        for segment, replay in query.all():
            key = make_archive_key(steam_id, replay.id, segment.speaker_id, segment.start_ms, segment.end_ms)
            candidates[key] = {
                "archive_key": key,
                "steam_id": steam_id,
                "source_replay_id": replay.id,
                "source_replay_filename": replay.filename,
                "server_name": replay.server_name,
                "recorded_at": replay.recorded_at,
                "uploaded_at": replay.uploaded_at,
                "speaker_id": segment.speaker_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "packet_count": segment.packet_count,
            }
            if current_replay_id == replay.id and current_packets is not None:
                candidates[key]["packet_records"] = [
                    packet
                    for packet in current_packets
                    if packet.speaker_id == segment.speaker_id
                    and segment.start_ms <= packet.time_ms <= segment.end_ms
                ]

        for clip in self.db.query(VoiceArchiveClip).filter(VoiceArchiveClip.steam_id == steam_id).all():
            candidates.setdefault(
                clip.archive_key,
                {
                    "archive_key": clip.archive_key,
                    "steam_id": clip.steam_id,
                    "source_replay_id": clip.source_replay_id,
                    "source_replay_filename": clip.source_replay_filename,
                    "server_name": clip.server_name,
                    "recorded_at": clip.recorded_at,
                    "uploaded_at": clip.uploaded_at,
                    "speaker_id": clip.speaker_id,
                    "start_ms": clip.start_ms,
                    "end_ms": clip.end_ms,
                    "packet_count": clip.packet_count,
                },
            )

        selected = select_voice_archive_candidates(candidates.values())
        packet_cache: dict[int, list[VoicePacket]] = {}
        # Track how many more selected candidates still need each replay's
        # full voice extraction. Each cached extraction holds the ENTIRE
        # replay's audio packets in RAM (hundreds of MB on long rounds), so
        # a replay is evicted as soon as its candidates are done instead of
        # accumulating every touched replay until the reconcile finishes.
        remaining_extractions = Counter(
            int(candidate["source_replay_id"])
            for candidate in selected
            if candidate.get("packet_records") is None
        )
        materialized = 0
        ready: list[tuple[Mapping[str, object], object]] = []
        for candidate in selected:
            clip, created = self._materialize_candidate(candidate, packet_cache)
            if clip is None:
                continue
            materialized += int(created)
            ready.append((candidate, clip))
            replay_id_key = int(candidate["source_replay_id"])
            if candidate.get("packet_records") is None:
                remaining_extractions[replay_id_key] -= 1
                if remaining_extractions[replay_id_key] <= 0:
                    packet_cache.pop(replay_id_key, None)

        self.db.query(VoiceArchiveSelection).filter(
            VoiceArchiveSelection.steam_id == steam_id
        ).delete(synchronize_session=False)
        self.db.flush()
        selected_ids = {clip.id for _candidate, clip in ready}
        for slot, (candidate, clip) in enumerate(ready):
            if slot < VOICE_ARCHIVE_EARLY_COUNT:
                band = "early"
            elif slot < VOICE_ARCHIVE_EARLY_COUNT + VOICE_ARCHIVE_MIDDLE_COUNT:
                band = "middle"
            else:
                band = "latest"
            self.db.add(VoiceArchiveSelection(
                steam_id=steam_id,
                slot=slot,
                band=band,
                clip_id=clip.id,
            ))

        stale = self.db.query(VoiceArchiveClip).filter(
            VoiceArchiveClip.steam_id == steam_id,
            ~VoiceArchiveClip.id.in_(selected_ids or {-1}),
        ).all()
        for clip in stale:
            path = self._path_for_clip(clip)
            self.db.delete(clip)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        self.db.flush()
        return {"selected": len(ready), "materialized": materialized}

    def reconcile_steam_ids(
        self,
        steam_ids: Iterable[str],
        *,
        current_replay_id: int | None = None,
        current_packets: Sequence[VoicePacket] | None = None,
    ) -> dict[str, int]:
        selected = materialized = 0
        for steam_id in sorted({str(value) for value in steam_ids if str(value).strip()}):
            result = self.reconcile_steam_id(
                steam_id,
                current_replay_id=current_replay_id,
                current_packets=current_packets,
            )
            selected += result["selected"]
            materialized += result["materialized"]
        return {"selected": selected, "materialized": materialized}

    def get_clips_for_steam_id(self, steam_id: str, page: int, page_size: int) -> tuple[list[dict], int, int]:
        from models import VoiceArchiveClip, VoiceArchiveSelection

        rows = (
            self.db.query(VoiceArchiveSelection, VoiceArchiveClip)
            .join(VoiceArchiveClip, VoiceArchiveClip.id == VoiceArchiveSelection.clip_id)
            .filter(VoiceArchiveSelection.steam_id == steam_id)
            .order_by(VoiceArchiveClip.recorded_at.desc(), VoiceArchiveClip.source_replay_id.desc(), VoiceArchiveSelection.slot.asc())
            .all()
        )
        grouped: dict[int, dict] = {}
        for selection, clip in rows:
            group = grouped.setdefault(clip.source_replay_id, {
                "replay_id": clip.source_replay_id,
                "replay_filename": clip.source_replay_filename,
                "server_name": clip.server_name,
                "recorded_at": clip.recorded_at.isoformat() if clip.recorded_at else None,
                "uploaded_at": clip.uploaded_at.isoformat() if clip.uploaded_at else None,
                "clips": [],
                "_sort": clip.recorded_at or clip.uploaded_at or datetime.min,
            })
            group["clips"].append({
                "id": clip.id,
                "speaker_id": clip.speaker_id,
                "start_ms": clip.start_ms,
                "end_ms": clip.end_ms,
                "duration_ms": max(0, clip.end_ms - clip.start_ms),
                "packet_count": clip.packet_count,
                "band": selection.band,
                "audio_url": f"/api/v1/admin/voice-clips/{clip.id}/audio",
            })
        groups = sorted(
            grouped.values(),
            key=lambda row: (row["_sort"], row["replay_id"]),
            reverse=True,
        )
        for group in groups:
            group.pop("_sort", None)
        for group in groups:
            group["clips"].sort(key=lambda row: (row["start_ms"], row["id"]))
        total_clips = sum(len(group["clips"]) for group in groups)
        total_groups = len(groups)
        offset = (page - 1) * page_size
        return groups[offset:offset + page_size], total_groups, total_clips

    def get_audio_path(self, clip_id: int) -> str | None:
        from models import VoiceArchiveClip

        clip = self.db.query(VoiceArchiveClip).filter(VoiceArchiveClip.id == clip_id).first()
        if not clip:
            return None
        path = self._path_for_clip(clip)
        if not os.path.isfile(path):
            return None
        return path
