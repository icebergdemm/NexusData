from tools.voice_profile_backfill import NexusPlayerLookup
from services_voice_profiles import VoicePacket
import tools.voice_profile_backfill as backfill


def test_lookup_accepts_nexus_login_key_for_admin_search():
    lookup = NexusPlayerLookup(
        "https://example.test",
        token=None,
        admin_token=None,
        login_key="nd-test-key",
    )

    assert lookup.endpoint == "https://example.test/api/v1/admin/players/search-suggestions"
    assert lookup.headers["X-ND-Login-Key"] == "nd-test-key"


def test_selected_source_decode_reloads_only_selected_replays(monkeypatch, tmp_path):
    packet = VoicePacket(time_ms=100, speaker_id=7, data=b"opus")
    calls = []
    decoded_segments = []

    def extract(path, _speaker_id):
        calls.append(path)
        return [packet]

    monkeypatch.setattr(backfill, "extract_voice_packets_for_speaker", extract)

    def decode(_decoder, _packets, segments, _temp_dir):
        decoded_segments.extend(segments)
        return {}

    monkeypatch.setattr(backfill, "_decode_speaker_stream", decode)

    source = tmp_path / "replay.barodemo.zip"
    selected = {
        (str(source), 7): [{"candidate_key": "clip", "start_ms": 100, "end_ms": 100}],
    }

    result = backfill._decode_selected_sources(None, selected, tmp_path)

    assert result == {}
    assert calls == [str(source)]
    assert decoded_segments[0]["packet_records"] == [packet]


def test_backfill_progress_checkpoint_round_trips(tmp_path):
    path = tmp_path / "backfill-progress.json"
    progress = {"version": 1, "completed_steam_ids": ["76561198000000001"]}

    backfill._save_backfill_progress(path, progress)

    assert backfill._load_backfill_progress(path) == progress
