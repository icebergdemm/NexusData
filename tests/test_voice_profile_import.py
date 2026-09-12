import json

from models import VoiceProfileImport
from routes.voice_profiles import _import_payload
from services_voice_profile_import import build_import_package, validate_import_package


def test_voice_profile_import_has_durable_identity_and_status_fields():
    row = VoiceProfileImport(import_key="voice-20260912-a", status="staged")

    assert row.import_key == "voice-20260912-a"
    assert row.status == "staged"
    assert VoiceProfileImport.__table__.c.total_profiles.default.arg == 0
    assert VoiceProfileImport.__table__.c.total_clips.default.arg == 0
    assert VoiceProfileImport.__table__.c.imported_profiles.default.arg == 0
    assert VoiceProfileImport.__table__.c.imported_clips.default.arg == 0


def test_build_import_package_compresses_profile_wav_and_writes_manifest(tmp_path):
    profiles = tmp_path / "profiles" / "76561198000000001"
    profiles.mkdir(parents=True)
    (profiles / "manifest.json").write_text(
        json.dumps(
            {
                "steam_id": "76561198000000001",
                "player_id": 7,
                "display_name": "Alice",
                "clips": [{
                    "slot": 0,
                    "band": "early",
                    "filename": "slot_00_early.wav",
                    "candidate_key": "a" * 64,
                    "source_replay": "replay.zip",
                    "source_replay_hash": "b" * 64,
                    "speaker_id": 4,
                    "start_ms": 10,
                    "end_ms": 20,
                    "packet_count": 2,
                    "radio_packet_count": 1,
                }],
            }
        ),
        encoding="utf-8",
    )
    (profiles / "slot_00_early.wav").write_bytes(b"RIFF" + b"voice")

    result = build_import_package(tmp_path / "profiles", tmp_path / "package", "voice-1")

    assert result["total_profiles"] == 1
    assert result["total_clips"] == 1
    assert (tmp_path / "package" / "manifest.json").is_file()
    assert (tmp_path / "package" / "wav" / ("a" * 64 + ".wav.gz")).is_file()


def test_validate_import_package_rejects_changed_clip_checksum(tmp_path):
    staging = tmp_path / "voice-1"
    staging.mkdir()
    (staging / "manifest.json").write_text(
        '{"format":"NDVP1","version":1,"import_key":"voice-1",'
        '"manifest_sha256":"invalid","profiles":[],"total_profiles":0,"total_clips":0}',
        encoding="utf-8",
    )

    try:
        validate_import_package(staging, "voice-1")
    except ValueError as exc:
        assert "manifest" in str(exc)
    else:
        raise AssertionError("changed manifest must be rejected")


def test_import_payload_exposes_durable_status_and_counters():
    row = VoiceProfileImport(
        id=4,
        import_key="voice-1",
        status="committed",
        staging_path="/uploads/voice_profile_imports/voice-1",
        manifest_sha256="a" * 64,
    )
    row.total_profiles = 2
    row.total_clips = 50
    row.imported_profiles = 2
    row.imported_clips = 50

    payload = _import_payload(row)

    assert payload["id"] == 4
    assert payload["import_key"] == "voice-1"
    assert payload["status"] == "committed"
    assert payload["total_clips"] == 50
    assert payload["imported_clips"] == 50
