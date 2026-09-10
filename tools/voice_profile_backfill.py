"""Offline voice-profile backfill for downloaded replay archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services_voice_profiles import (
    VoicePacket,
    extract_voice_packet_records,
    group_voice_packets,
    select_profile_candidates,
    write_ndvc,
    write_vpk1,
    write_wave_slice,
)


REPLAY_TIME_RE = re.compile(r"replay_(\d{8})_(\d{6})", re.IGNORECASE)
DEFAULT_INPUT_DIR = Path(r"D:\ASPER_replays")
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR / "voice_profiles"
DEFAULT_NEXUS_URL = "https://nd.rp-asper.ru"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replay_recorded_at(path: Path) -> datetime:
    match = REPLAY_TIME_RE.search(path.name)
    if not match:
        return datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")


class NexusPlayerLookup:
    def __init__(self, base_url: str, token: str | None):
        self.endpoint = base_url.rstrip("/") + "/api/v1/admin/voice-profiles/player-search"
        self.token = token
        self.cache: dict[str, list[dict]] = {}

    def search(self, query: str) -> list[dict]:
        query = query.strip()
        if len(query) < 2 or not self.token:
            return []
        if query in self.cache:
            return self.cache[query]
        request = Request(
            self.endpoint + "?" + urlencode({"q": query}),
            headers={"X-API-Key": self.token, "Accept": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        players = payload.get("players") if isinstance(payload, dict) else []
        self.cache[query] = players if isinstance(players, list) else []
        return self.cache[query]


def resolve_speaker(
    speaker_id: int,
    names: set[str],
    embedded_steam_id: str | None,
    lookup: NexusPlayerLookup,
) -> tuple[dict | None, list[dict]]:
    candidates: dict[str, dict] = {}
    queries = list(names)
    if embedded_steam_id:
        queries.insert(0, embedded_steam_id)
    for query in queries:
        for player in lookup.search(query):
            steam_id = str(player.get("player_steamid") or "").strip()
            if steam_id:
                candidates[steam_id] = player

    if embedded_steam_id and embedded_steam_id in candidates:
        return candidates[embedded_steam_id], list(candidates.values())
    if len(candidates) == 1:
        return next(iter(candidates.values())), list(candidates.values())
    return None, sorted(candidates.values(), key=lambda row: str(row.get("player_steamid") or ""))


def _candidate_key(replay_hash: str, segment: dict, steam_id: str) -> str:
    raw = "|".join(
        (
            steam_id,
            replay_hash,
            str(segment["speaker_id"]),
            str(segment["start_ms"]),
            str(segment["end_ms"]),
        )
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_mapping(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _mapping_override(mapping: dict[str, dict], name: str) -> dict | None:
    value = mapping.get(name)
    return value if isinstance(value, dict) else None


def _build_decoder(project: Path) -> Path:
    subprocess.run(["dotnet", "build", str(project), "-c", "Release"], check=True)
    dll = project.parent / "bin" / "Release" / "net8.0" / "VoiceProfileDecoder.dll"
    if not dll.is_file():
        raise RuntimeError(f"decoder build did not produce {dll}")
    return dll


def _decode_speaker_stream(
    decoder_dll: Path,
    packets: list[VoicePacket],
    segments: list[dict],
    temp_dir: Path,
) -> dict[str, Path]:
    vpk_path = temp_dir / "speaker.vpk1"
    wav_path = temp_dir / "speaker.wav"
    index_path = temp_dir / "speaker.json"
    write_vpk1(vpk_path, packets)
    subprocess.run(
        [
            "dotnet",
            str(decoder_dll),
            "--input",
            str(vpk_path),
            "--output",
            str(wav_path),
            "--index",
            str(index_path),
        ],
        check=True,
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    by_timestamp: dict[int, list[dict]] = {}
    for entry in index:
        by_timestamp.setdefault(int(entry["timestamp_ms"]), []).append(entry)

    output: dict[str, Path] = {}
    for segment in sorted(segments, key=lambda row: (int(row["start_ms"]), str(row["candidate_key"]))):
        entries: list[dict] = []
        for packet in segment["packet_records"]:
            values = by_timestamp.get(packet.time_ms, [])
            if values:
                entries.append(values.pop(0))
        if not entries:
            continue
        start_frame = min(int(entry["sample_start"]) for entry in entries)
        end_frame = max(int(entry["sample_start"]) + int(entry["sample_count"]) for entry in entries)
        candidate_key = str(segment["candidate_key"])
        clip_path = temp_dir / f"{candidate_key}.wav"
        write_wave_slice(wav_path, clip_path, start_frame=start_frame, end_frame=end_frame)
        output[candidate_key] = clip_path
    return output


def _write_profile(
    output_dir: Path,
    steam_id: str,
    player: dict,
    selected: list[dict],
    clip_paths: dict[str, Path],
) -> None:
    profile_dir = output_dir / steam_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "steam_id": steam_id,
        "player_id": player.get("player_id"),
        "display_name": player.get("forced_display_name") or player.get("player_last_time_name"),
        "clips": [],
    }
    for slot, candidate in enumerate(selected):
        band = "early" if slot < 5 else "middle" if slot < 15 else "latest"
        source = clip_paths.get(str(candidate["candidate_key"]))
        if source is None:
            continue
        filename = f"slot_{slot:02d}_{band}.wav"
        destination = profile_dir / filename
        destination.write_bytes(source.read_bytes())
        manifest["clips"].append({
            "slot": slot,
            "band": band,
            "filename": filename,
            "candidate_key": candidate["candidate_key"],
            "source_replay": candidate["replay_filename"],
            "source_replay_hash": candidate["replay_hash"],
            "speaker_id": candidate["speaker_id"],
            "start_ms": candidate["start_ms"],
            "end_ms": candidate["end_ms"],
            "packet_count": candidate["packet_count"],
            "radio_packet_count": candidate["radio_packet_count"],
            "mapping_source": candidate["mapping_source"],
        })
    (profile_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(input_dir.glob("*.barodemo.zip")) + sorted(input_dir.glob("*.barodemo"))
    if not files:
        raise RuntimeError(f"no replay files found in {input_dir}")

    mapping_override = _load_mapping(Path(args.mapping_json) if args.mapping_json else None)
    token = os.environ.get(args.token_env)
    lookup = NexusPlayerLookup(args.nexus_url, token)
    decoder_dll = _build_decoder(Path(args.decoder_project))
    candidates_by_steam: dict[str, list[dict]] = {}
    player_by_steam: dict[str, dict] = {}
    unresolved: list[dict] = []
    replay_packets: dict[tuple[str, int], list[VoicePacket]] = {}
    replay_hashes: dict[str, str] = {}

    for replay_path in files:
        replay_hash = sha256_file(replay_path)
        replay_hashes[str(replay_path)] = replay_hash
        extraction = extract_voice_packet_records(replay_path)
        grouped = group_voice_packets(extraction.packets)
        by_speaker = {speaker_id: [packet for packet in extraction.packets if packet.speaker_id == speaker_id] for speaker_id in {packet.speaker_id for packet in extraction.packets}}
        for speaker_id in sorted(by_speaker):
            override = next((
                _mapping_override(mapping_override, name)
                for name in extraction.names_by_speaker.get(speaker_id, set())
                if _mapping_override(mapping_override, name)
            ), None)
            player, candidates = (
                (override, [override] if override else [])
                if override
                else resolve_speaker(speaker_id, extraction.names_by_speaker.get(speaker_id, set()), extraction.mapping.get(speaker_id), lookup)
            )
            if player is None:
                unresolved.append({
                    "replay_filename": replay_path.name,
                    "speaker_id": speaker_id,
                    "names": sorted(extraction.names_by_speaker.get(speaker_id, set())),
                    "candidates": candidates,
                    "warnings": extraction.warnings,
                })
                continue
            steam_id = str(player.get("player_steamid") or "").strip()
            if not steam_id:
                unresolved.append({"replay_filename": replay_path.name, "speaker_id": speaker_id, "names": sorted(extraction.names_by_speaker.get(speaker_id, set())), "candidates": candidates})
                continue
            replay_key = str(replay_path)
            replay_packets[(replay_key, speaker_id)] = by_speaker[speaker_id]
            for segment in grouped:
                if segment["speaker_id"] != speaker_id:
                    continue
                candidate = dict(segment)
                candidate.update({
                    "candidate_key": _candidate_key(replay_hash, segment, steam_id),
                    "recorded_at": replay_recorded_at(replay_path),
                    "replay_filename": replay_path.name,
                    "replay_hash": replay_hash,
                    "replay_key": replay_key,
                    "steam_id": steam_id,
                    "player_id": player.get("player_id"),
                    "mapping_source": "override" if override else "nexus_name_search",
                })
                candidates_by_steam.setdefault(steam_id, []).append(candidate)
            player_by_steam[steam_id] = player

    (output_dir / "unresolved.json").write_text(json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"replays={len(files)} profiles={len(candidates_by_steam)} unresolved={len(unresolved)}")
    if not candidates_by_steam:
        return 0

    with tempfile.TemporaryDirectory(prefix="voice-profile-") as temporary:
        temp_dir = Path(temporary)
        for steam_id, candidates in sorted(candidates_by_steam.items()):
            selected = [dict(row) for row in select_profile_candidates(candidates)]
            packets_by_source: dict[tuple[str, int], list[dict]] = {}
            for candidate in selected:
                packets_by_source.setdefault((candidate["replay_key"], candidate["speaker_id"]), []).append(candidate)
            clip_paths: dict[str, Path] = {}
            for source_key, source_segments in packets_by_source.items():
                packets = replay_packets[source_key]
                clip_paths.update(_decode_speaker_stream(decoder_dll, packets, source_segments, temp_dir))
            _write_profile(output_dir, steam_id, player_by_steam[steam_id], selected, clip_paths)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--nexus-url", default=DEFAULT_NEXUS_URL)
    parser.add_argument("--token-env", default="NEXUS_API_TOKEN")
    parser.add_argument("--mapping-json")
    parser.add_argument(
        "--decoder-project",
        default=str(Path(__file__).resolve().parents[1] / "tools" / "VoiceProfileDecoder" / "VoiceProfileDecoder.csproj"),
    )
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except Exception as exc:
        print(f"voice backfill failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
