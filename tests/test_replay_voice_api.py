from types import SimpleNamespace


def test_voice_activity_keeps_local_and_radio_intervals_separate():
    from services_voice_profiles import VoicePacket, group_voice_packets

    segments = group_voice_packets([
        VoicePacket(time_ms=1_000, speaker_id=7, data=b"a", radio=False),
        VoicePacket(time_ms=1_100, speaker_id=7, data=b"b", radio=True),
        VoicePacket(time_ms=1_200, speaker_id=7, data=b"c", radio=False),
    ])

    assert [(segment["start_ms"], segment["end_ms"], segment["radio_packet_count"]) for segment in segments] == [
        (1_000, 1_200, 0),
        (1_100, 1_100, 1),
    ]


def test_voice_activity_payload_keeps_unresolved_speaker_intervals():
    from services_voice_profiles import build_replay_voice_segment_rows

    rows = build_replay_voice_segment_rows(
        [{
            "speaker_id": 7,
            "start_ms": 1_000,
            "end_ms": 1_200,
            "packet_count": 2,
            "radio_packet_count": 0,
        }],
        mapping={},
        speaker_rows={},
    )

    assert rows == [{
        "speaker_id": 7,
        "steam_id": None,
        "player_id": None,
        "start_ms": 1_000,
        "end_ms": 1_200,
        "packet_count": 2,
        "radio_packet_count": 0,
    }]


def test_missing_voice_activity_rebuilds_metadata_without_profile_materialization():
    from services_voice_profiles import VoiceProfileService

    service = object.__new__(VoiceProfileService)
    calls = []
    service._has_voice_activity = lambda replay_id: False
    service._index_voice_activity_metadata = lambda replay_id: calls.append(replay_id)

    service.ensure_replay_voice_activity(1415)

    assert calls == [1415]


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
