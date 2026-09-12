"""Validation and idempotent commit for locally built voice-profile packages."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


IMPORT_FORMAT = "NDVP1"
IMPORT_VERSION = 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_digest(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256", None)
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe import path: {value}")
    return path


def _read_manifest(staging_dir: Path, import_key: str) -> dict[str, Any]:
    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("manifest.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if manifest.get("format") != IMPORT_FORMAT or int(manifest.get("version", 0)) != IMPORT_VERSION:
        raise ValueError("unsupported voice profile import format")
    if manifest.get("import_key") != import_key:
        raise ValueError("manifest import_key does not match staging key")
    if manifest.get("manifest_sha256") != _manifest_digest(manifest):
        raise ValueError("manifest checksum mismatch")
    return manifest


def validate_import_package(staging_dir: Path, import_key: str) -> dict[str, Any]:
    """Validate a complete package without changing the database or files."""
    staging_dir = staging_dir.resolve()
    manifest = _read_manifest(staging_dir, import_key)
    profiles = manifest.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("manifest profiles must be a list")

    seen_archive_keys: set[str] = set()
    seen_paths: set[str] = set()
    total_clips = 0
    for profile in profiles:
        if not isinstance(profile, dict) or not str(profile.get("steam_id") or "").strip():
            raise ValueError("profile steam_id is required")
        clips = profile.get("clips")
        if not isinstance(clips, list):
            raise ValueError("profile clips must be a list")
        for clip in clips:
            if not isinstance(clip, dict):
                raise ValueError("clip must be an object")
            archive_key = str(clip.get("archive_key") or "")
            relative_name = str(clip.get("path") or "")
            if not archive_key or archive_key in seen_archive_keys:
                raise ValueError(f"duplicate or empty archive_key: {archive_key}")
            relative_path = _safe_relative_path(relative_name)
            if relative_name in seen_paths or relative_path.parts[:1] != ("wav",):
                raise ValueError(f"invalid or duplicate clip path: {relative_name}")
            seen_archive_keys.add(archive_key)
            seen_paths.add(relative_name)
            clip_path = staging_dir / relative_path
            if not clip_path.is_file():
                raise ValueError(f"clip file is missing: {relative_name}")
            expected_hash = str(clip.get("sha256") or "")
            if not expected_hash or _sha256_file(clip_path) != expected_hash:
                raise ValueError(f"clip checksum mismatch: {relative_name}")
            try:
                with gzip.open(clip_path, "rb") as source:
                    if source.read(4) != b"RIFF":
                        raise ValueError(f"clip is not a gzip WAV: {relative_name}")
            except OSError as exc:
                raise ValueError(f"invalid gzip clip: {relative_name}") from exc
            total_clips += 1

    if int(manifest.get("total_profiles", -1)) != len(profiles):
        raise ValueError("profile counter mismatch")
    if int(manifest.get("total_clips", -1)) != total_clips:
        raise ValueError("clip counter mismatch")
    return {
        "format": manifest["format"],
        "version": manifest["version"],
        "import_key": import_key,
        "manifest_sha256": manifest["manifest_sha256"],
        "total_profiles": len(profiles),
        "total_clips": total_clips,
        "manifest": manifest,
    }


def build_import_package(profiles_dir: Path, output_dir: Path, import_key: str) -> dict[str, Any]:
    """Package local profile WAVs into a checksum-protected staging directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    profiles: list[dict[str, Any]] = []
    for profile_dir in sorted(profiles_dir.iterdir(), key=lambda path: path.name):
        if not profile_dir.is_dir() or not profile_dir.name.isdigit():
            continue
        manifest_path = profile_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profile = {
            "steam_id": str(source_manifest["steam_id"]),
            "player_id": source_manifest.get("player_id"),
            "display_name": source_manifest.get("display_name"),
            "clips": [],
        }
        for source_clip in source_manifest.get("clips") or []:
            archive_key = str(source_clip.get("candidate_key") or "")
            if len(archive_key) != 64:
                raise ValueError(f"invalid archive key in {manifest_path}")
            source_path = profile_dir / str(source_clip["filename"])
            if not source_path.is_file():
                raise ValueError(f"profile clip is missing: {source_path}")
            relative_name = f"wav/{archive_key}.wav.gz"
            target_path = output_dir / relative_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with source_path.open("rb") as source, gzip.open(target_path, "wb", compresslevel=6) as target:
                shutil.copyfileobj(source, target)
            profile["clips"].append({
                "archive_key": archive_key,
                "path": relative_name,
                "sha256": _sha256_file(target_path),
                "storage_format": "wav-gzip",
                "storage_size": target_path.stat().st_size,
                "player_id": source_clip.get("player_id", profile["player_id"]),
                "source_replay_id": source_clip.get("source_replay_id"),
                "source_replay_filename": source_clip.get("source_replay"),
                "source_replay_hash": source_clip.get("source_replay_hash") or "",
                "speaker_id": source_clip.get("speaker_id", 0),
                "start_ms": source_clip.get("start_ms", 0),
                "end_ms": source_clip.get("end_ms", 0),
                "packet_count": source_clip.get("packet_count", 0),
                "radio_packet_count": source_clip.get("radio_packet_count", 0),
                "slot": source_clip.get("slot"),
                "band": source_clip.get("band"),
                "recorded_at": source_clip.get("recorded_at"),
            })
        profiles.append(profile)

    manifest: dict[str, Any] = {
        "format": IMPORT_FORMAT,
        "version": IMPORT_VERSION,
        "import_key": import_key,
        "total_profiles": len(profiles),
        "total_clips": sum(len(profile["clips"]) for profile in profiles),
        "profiles": profiles,
    }
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return validate_import_package(output_dir, import_key)


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def commit_import_package(db, staging_dir: Path, import_key: str) -> dict[str, Any]:
    """Validate and upsert one package into durable NexusData storage."""
    from models import PlayerData, VoiceProfileClip, VoiceProfileImport, VoiceProfileSelection
    from services_replays import get_upload_dir

    summary = validate_import_package(staging_dir, import_key)
    manifest = summary["manifest"]
    record = db.query(VoiceProfileImport).filter(
        VoiceProfileImport.import_key == import_key,
    ).with_for_update().first()
    if record is not None and record.status == "committed":
        return {
            "id": record.id,
            "import_key": import_key,
            "status": record.status,
            "total_profiles": record.total_profiles,
            "total_clips": record.total_clips,
            "imported_profiles": record.imported_profiles,
            "imported_clips": record.imported_clips,
        }
    if record is not None and record.status == "importing":
        raise RuntimeError("импорт уже выполняется")
    if record is None:
        record = VoiceProfileImport(
            import_key=import_key,
            staging_path=str(staging_dir),
            manifest_sha256=summary["manifest_sha256"],
        )
        db.add(record)
    record.status = "importing"
    record.total_profiles = summary["total_profiles"]
    record.total_clips = summary["total_clips"]
    record.error_message = None
    record.started_at = datetime.utcnow()
    record.finished_at = None
    db.flush()

    upload_root = Path(get_upload_dir(db))
    clip_by_profile: dict[str, list[tuple[dict[str, Any], VoiceProfileClip]]] = {}
    for profile in manifest["profiles"]:
        steam_id = str(profile["steam_id"])
        player = db.query(PlayerData).filter(PlayerData.player_steamid == steam_id).first()
        player_id = player.player_id if player else profile.get("player_id")
        for clip_data in profile["clips"]:
            archive_key = clip_data["archive_key"]
            target_relative = f"voice_profiles/wav/{archive_key}.wav.gz"
            _copy_atomic(staging_dir / _safe_relative_path(clip_data["path"]), upload_root / target_relative)
            clip = db.query(VoiceProfileClip).filter(VoiceProfileClip.archive_key == archive_key).first()
            values = {
                "steam_id": steam_id,
                "player_id": player_id,
                "source_replay_id": clip_data.get("source_replay_id"),
                "source_replay_filename": clip_data.get("source_replay_filename") or "local-import",
                "source_replay_hash": clip_data.get("source_replay_hash") or "",
                "speaker_id": int(clip_data.get("speaker_id") or 0),
                "start_ms": int(clip_data.get("start_ms") or 0),
                "end_ms": int(clip_data.get("end_ms") or 0),
                "packet_count": int(clip_data.get("packet_count") or 0),
                "radio_packet_count": int(clip_data.get("radio_packet_count") or 0),
                "ndvc_filename": None,
                "wav_filename": target_relative,
                "storage_format": "wav-gzip",
                "storage_size": int(clip_data["storage_size"]),
                "archive_version": 2,
            }
            if clip is None:
                clip = VoiceProfileClip(archive_key=archive_key, **values)
                db.add(clip)
                db.flush()
            else:
                for field, value in values.items():
                    setattr(clip, field, value)
            clip_by_profile.setdefault(steam_id, []).append((clip_data, clip))

    for steam_id, clips in clip_by_profile.items():
        db.query(VoiceProfileSelection).filter(VoiceProfileSelection.steam_id == steam_id).delete(synchronize_session=False)
        for clip_data, clip in clips:
            if clip_data.get("slot") is None:
                continue
            db.add(VoiceProfileSelection(
                steam_id=steam_id,
                slot=int(clip_data["slot"]),
                band=str(clip_data.get("band") or "middle"),
                clip_id=clip.id,
            ))

    record.status = "committed"
    record.imported_profiles = len(clip_by_profile)
    record.imported_clips = sum(len(clips) for clips in clip_by_profile.values())
    record.finished_at = datetime.utcnow()
    db.commit()
    return {
        "id": record.id,
        "import_key": import_key,
        "status": record.status,
        "total_profiles": record.total_profiles,
        "total_clips": record.total_clips,
        "imported_profiles": record.imported_profiles,
        "imported_clips": record.imported_clips,
    }
