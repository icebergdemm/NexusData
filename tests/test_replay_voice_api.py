from types import SimpleNamespace


def test_replay_voice_speaker_payload_preserves_confirmed_and_unresolved_identity():
    from routes.replays import _serialize_replay_voice_segment, _serialize_replay_voice_speaker

    mapped = _serialize_replay_voice_speaker(SimpleNamespace(
        speaker_id=11,
        names=["Alice", "Alice old"],
        steam_id="76561198000000001",
        player_id=7,
        resolution_status="mapped",
    ))
    unresolved = _serialize_replay_voice_speaker(SimpleNamespace(
        speaker_id=12,
        names=["Unknown"],
        steam_id=None,
        player_id=None,
        resolution_status="unresolved",
    ))

    assert mapped == {
        "speaker_id": 11,
        "names": ["Alice", "Alice old"],
        "steam_id": "76561198000000001",
        "player_id": 7,
        "resolution_status": "mapped",
    }
    assert unresolved["player_id"] is None
    assert unresolved["steam_id"] is None
    assert unresolved["resolution_status"] == "unresolved"

    segment = _serialize_replay_voice_segment(SimpleNamespace(
        speaker_id=11,
        start_ms=100,
        end_ms=300,
        packet_count=4,
        radio_packet_count=4,
    ))
    assert segment["radio"] is True
    assert segment["start_ms"] == 100
