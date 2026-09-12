"""Offline voice-profile backfill for downloaded replay archives."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import ssl
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, Request, build_opener, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services_voice_profiles import (
    VoicePacket,
    extract_voice_packets_for_speaker,
    extract_voice_segment_metadata,
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
    def __init__(
        self,
        base_url: str,
        token: str | None,
        admin_token: str | None = None,
        login_key: str | None = None,
        insecure: bool = False,
    ):
        self.endpoint = base_url.rstrip("/") + (
            "/api/v1/admin/voice-profiles/player-search"
            if token
            else "/api/v1/admin/players/search-suggestions"
        )
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["X-API-Key"] = token
        elif login_key:
            self.headers["X-ND-Login-Key"] = login_key
        elif admin_token:
            self.headers["Cookie"] = f"admin_token={admin_token}"
        self.authorized = bool(token or login_key or admin_token)
        self.ssl_context = ssl._create_unverified_context() if insecure else None
        self.cache: dict[str, list[dict]] = {}

    def search(self, query: str) -> list[dict]:
        query = query.strip()
        if len(query) < 2 or not self.authorized:
            return []
        if query in self.cache:
            return self.cache[query]
        request = Request(
            self.endpoint + "?" + urlencode({"q": query}),
            headers=self.headers,
        )
        try:
            response_context = self.ssl_context
            response = urlopen(request, timeout=30, context=response_context)
        except HTTPError as exc:
            if exc.code != 403:
                raise
            response = _urlopen_ipv6(request, timeout=30, context=self.ssl_context)
        with response:
            payload = json.loads(response.read().decode("utf-8"))
        players = payload.get("players") if isinstance(payload, dict) else []
        self.cache[query] = players if isinstance(players, list) else []
        return self.cache[query]


class _IPv6HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        addresses = socket.getaddrinfo(
            self.host,
            self.port,
            socket.AF_INET6,
            socket.SOCK_STREAM,
        )
        last_error = None
        for family, socktype, proto, _canonname, sockaddr in addresses:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                if self._tunnel_host:
                    self.sock = sock
                    self._tunnel()
                self.sock = self._context.wrap_socket(
                    sock,
                    server_hostname=self._tunnel_host or self.host,
                )
                return
            except OSError as exc:
                last_error = exc
                sock.close()
        raise last_error or OSError(f"unable to connect to IPv6 host {self.host}")


class _IPv6HTTPSHandler(HTTPSHandler):
    def __init__(self, context):
        super().__init__(context=context)

    def https_open(self, request):
        return self.do_open(
            _IPv6HTTPSConnection,
            request,
            context=self._context,
        )


def _urlopen_ipv6(request: Request, *, timeout: int, context):
    if context is None:
        context = ssl.create_default_context()
    return build_opener(_IPv6HTTPSHandler(context)).open(request, timeout=timeout)


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


def _load_backfill_progress(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "completed_steam_ids": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"version": 1, "completed_steam_ids": []}
    if not isinstance(payload, dict):
        return {"version": 1, "completed_steam_ids": []}
    completed = payload.get("completed_steam_ids", [])
    payload["completed_steam_ids"] = [str(value) for value in completed if value]
    return payload


def _save_backfill_progress(path: Path, progress: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _profile_checkpoint_matches(profile_dir: Path, selected: list[dict]) -> bool:
    manifest_path = profile_dir / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    existing = {
        str(clip.get("candidate_key"))
        for clip in manifest.get("clips", [])
        if isinstance(clip, dict) and clip.get("candidate_key")
    }
    expected = {str(candidate["candidate_key"]) for candidate in selected}
    return existing == expected


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


def _decode_selected_sources(
    decoder_dll: Path,
    selected_by_source: dict[tuple[str, int], list[dict]],
    temp_dir: Path,
) -> dict[str, Path]:
    """Re-read only replay/speaker streams needed by the selected clips."""
    by_replay: dict[str, list[tuple[int, list[dict]]]] = {}
    for (replay_key, speaker_id), segments in selected_by_source.items():
        by_replay.setdefault(replay_key, []).append((speaker_id, segments))

    clip_paths: dict[str, Path] = {}
    for replay_key in sorted(by_replay):
        for speaker_id, segments in sorted(by_replay[replay_key], key=lambda item: item[0]):
            packets = extract_voice_packets_for_speaker(replay_key, speaker_id)
            if packets:
                packet_records = [
                    [packet for packet in packets if int(segment["start_ms"]) <= packet.time_ms <= int(segment["end_ms"])]
                    for segment in segments
                ]
                prepared_segments = []
                for segment, records in zip(segments, packet_records):
                    prepared = dict(segment)
                    prepared["packet_records"] = records
                    prepared_segments.append(prepared)
                clip_paths.update(_decode_speaker_stream(decoder_dll, packets, prepared_segments, temp_dir))
    return clip_paths


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
    admin_token = os.environ.get(args.admin_token_env)
    login_key = os.environ.get(args.login_key_env)
    lookup = NexusPlayerLookup(args.nexus_url, token, admin_token, login_key, args.insecure)
    decoder_dll = _build_decoder(Path(args.decoder_project))
    candidates_by_steam: dict[str, list[dict]] = {}
    player_by_steam: dict[str, dict] = {}
    unresolved: list[dict] = []

    for replay_index, replay_path in enumerate(files, start=1):
        replay_hash = sha256_file(replay_path)
        replay_key = str(replay_path)
        extraction = extract_voice_segment_metadata(replay_path)
        grouped = extraction.segments
        speaker_ids = {segment["speaker_id"] for segment in grouped}
        for speaker_id in sorted(speaker_ids):
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
            for segment in grouped:
                if segment["speaker_id"] != speaker_id:
                    continue
                candidate = {
                    key: value
                    for key, value in segment.items()
                    if key != "packet_records"
                }
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
        print(
            f"scan={replay_index}/{len(files)} replay={replay_path.name} "
            f"segments={len(grouped)} profiles={len(candidates_by_steam)}",
            flush=True,
        )

    (output_dir / "unresolved.json").write_text(json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"replays={len(files)} profiles={len(candidates_by_steam)} unresolved={len(unresolved)}")
    if not candidates_by_steam:
        return 0

    progress_path = output_dir / "backfill_progress.json"
    progress = _load_backfill_progress(progress_path)
    completed_steam_ids = set(progress.get("completed_steam_ids", []))
    progress.update({
        "version": 1,
        "replays": len(files),
        "profile_count": len(candidates_by_steam),
    })
    progress.setdefault("failed_profiles", {})
    _save_backfill_progress(progress_path, progress)

    with tempfile.TemporaryDirectory(prefix="voice-profile-") as temporary:
        temp_dir = Path(temporary)
        ordered_profiles = sorted(candidates_by_steam.items())
        total_profiles = len(ordered_profiles)
        for profile_index, (steam_id, candidates) in enumerate(ordered_profiles, start=1):
            selected = [dict(row) for row in select_profile_candidates(candidates)]
            profile_dir = output_dir / steam_id
            if steam_id in completed_steam_ids and _profile_checkpoint_matches(profile_dir, selected):
                print(f"profile={profile_index}/{total_profiles} steam_id={steam_id} status=checkpointed", flush=True)
                continue

            try:
                selected_by_source: dict[tuple[str, int], list[dict]] = {}
                for candidate in selected:
                    selected_by_source.setdefault(
                        (candidate["replay_key"], candidate["speaker_id"]), []
                    ).append(candidate)
                clip_paths = _decode_selected_sources(decoder_dll, selected_by_source, temp_dir)
                _write_profile(output_dir, steam_id, player_by_steam[steam_id], selected, clip_paths)
            except Exception as exc:
                progress["failed_profiles"][steam_id] = str(exc)
                _save_backfill_progress(progress_path, progress)
                print(
                    f"profile={profile_index}/{total_profiles} steam_id={steam_id} "
                    f"status=failed error={exc}",
                    flush=True,
                )
                continue

            completed_steam_ids.add(steam_id)
            progress["completed_steam_ids"] = sorted(completed_steam_ids)
            progress["last_completed_steam_id"] = steam_id
            progress["failed_profiles"].pop(steam_id, None)
            _save_backfill_progress(progress_path, progress)
            print(
                f"profile={profile_index}/{total_profiles} steam_id={steam_id} "
                f"clips={len(clip_paths)} status=done",
                flush=True,
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--nexus-url", default=DEFAULT_NEXUS_URL)
    parser.add_argument("--token-env", default="NEXUS_API_TOKEN")
    parser.add_argument("--admin-token-env", default="NEXUS_ADMIN_TOKEN")
    parser.add_argument("--login-key-env", default="NEXUS_LOGIN_KEY")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="disable TLS certificate verification for a local backfill against an expired internal certificate",
    )
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
