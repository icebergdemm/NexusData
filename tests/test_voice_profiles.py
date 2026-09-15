import json
import struct
import wave
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from routes.voice_profiles import _profile_players_payload
from services_voice_profiles import (
    VOICE_PROFILE_ARCHIVE_VERSION,
    ReplayVoiceExtraction,
    VoicePacket,
    VoiceProfileService,
    extract_voice_packet_records,
    extract_segment_wav,
    group_voice_packets,
    group_nwdemo_voice_packets,
    mfcc_v1_from_pcm16,
    cosine_similarity,
    aggregate_mfcc_v1,
    read_nwdemo_voice_packets,
    read_ndvc,
    read_vpk1,
    stale_profile_clips,
    select_profile_candidates,
    steam2_to_steam64,
    write_vpk1,
    write_ndvc,
    write_wave_slice,
)


def _dotnet_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    assert len(encoded) < 0x80
    return bytes([len(encoded)]) + encoded


def _nwdemo_voice_packet(time_ms: int, local_speaker_id: int, steam2: str, radio: bool, opus: bytes) -> bytes:
    return (
        struct.pack("<IH", time_ms, local_speaker_id)
        + _dotnet_string(steam2)
        + bytes([int(radio)])
        + struct.pack("<H", len(opus))
        + opus
    )


def _write_nwdemo(path, voice: bytes, *, format_name: str = "nwdemo-v2", world: bytes = b"BDMO"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"format": format_name, "world_format": "BDMO", "world_version": 7}),
        )
        archive.writestr("world.bin", world)
        archive.writestr("voice.bin", voice)


def test_nwdemo_v2_uses_steam_identity_not_local_speaker_id_and_keeps_radio(tmp_path):
    path = tmp_path / "round.nwdemo"
    _write_nwdemo(
        path,
        _nwdemo_voice_packet(100, 7, "STEAM_1:1:168500928", False, b"one")
        + _nwdemo_voice_packet(1_100, 99, "STEAM_1:1:168500928", False, b"two")
        + _nwdemo_voice_packet(2_101, 99, "STEAM_1:1:168500928", True, b"radio"),
    )

    packets = read_nwdemo_voice_packets(path)
    segments = group_nwdemo_voice_packets(packets)

    assert {packet.steam_id for packet in packets} == {"76561198297267585"}
    assert [(row["steam_id"], row["start_ms"], row["end_ms"], row["radio"]) for row in segments] == [
        ("76561198297267585", 100, 1_100, False),
        ("76561198297267585", 2_101, 2_101, True),
    ]


def test_nwdemo_v2_rejects_legacy_or_missing_members(tmp_path):
    legacy = tmp_path / "legacy.nwdemo"
    _write_nwdemo(legacy, b"", format_name="nwdemo-v1")

    with pytest.raises(ValueError, match="unsupported nwdemo format"):
        read_nwdemo_voice_packets(legacy)

    incomplete = tmp_path / "incomplete.nwdemo"
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("manifest.json", '{"format":"nwdemo-v2"}')
    with pytest.raises(ValueError, match="required member"):
        read_nwdemo_voice_packets(incomplete)

    broken_world = tmp_path / "broken-world.nwdemo"
    _write_nwdemo(broken_world, b"", world=b"nope")
    with pytest.raises(ValueError, match="world.bin header"):
        read_nwdemo_voice_packets(broken_world)


def test_nwdemo_invalid_steam_identity_does_not_create_a_profile_packet(tmp_path):
    path = tmp_path / "invalid-steam.nwdemo"
    _write_nwdemo(path, _nwdemo_voice_packet(100, 7, "STEAM_1:2:1", False, b"opus"))

    assert read_nwdemo_voice_packets(path) == []


def test_mfcc_v1_is_deterministic_normalized_and_comparable():
    samples = [int(12_000 * ((index % 97) / 96.0 - 0.5)) for index in range(4_800)]

    first = mfcc_v1_from_pcm16(samples)
    second = mfcc_v1_from_pcm16(samples)

    assert first == second
    assert len(first) == 26
    assert pytest.approx(sum(value * value for value in first), abs=1e-9) == 1.0
    assert cosine_similarity(first, second) == pytest.approx(1.0)


def test_low_quality_voiceprint_is_not_aggregated():
    low = {"quality": "low", "duration_ms": 500, "vector": [1.0, 0.0]}
    good = {"quality": "good", "duration_ms": 4_000, "vector": [0.0, 2.0]}

    assert aggregate_mfcc_v1([low]) is None
    assert aggregate_mfcc_v1([low, good]) == ([0.0, 1.0], 1, 4_000)


def test_ndvc_v2_preserves_radio_without_a_speaker_identifier(tmp_path):
    path = tmp_path / "clip.ndvc"
    packets = [VoicePacket(12, 0, b"opus-1", False), VoicePacket(44, 0, b"opus-2", True)]

    write_ndvc(path, packets)

    restored = read_ndvc(path)
    assert [(packet.time_ms, packet.data, packet.radio) for packet in restored] == [
        (0, b"opus-1", False),
        (32, b"opus-2", True),
    ]


def test_steam2_to_steam64_uses_direct_arithmetic_and_rejects_bad_values():
    assert steam2_to_steam64("STEAM_1:1:168500928") == "76561198297267585"
    assert steam2_to_steam64("STEAM_1:2:1") is None
    assert steam2_to_steam64("Alice") is None


def test_profile_players_payload_groups_clips_and_preserves_playback_metadata():
    clips = [
        SimpleNamespace(
            id=1,
            archive_key="one",
            steam_id="76561198000000001",
            player_id=7,
            source_replay_id=1062,
            source_replay_filename="replay.barodemo.zip",
            source_replay_hash="hash-one",
            speaker_id=42,
            start_ms=100,
            end_ms=900,
            packet_count=12,
            radio_packet_count=0,
            storage_format="wav-gzip",
            storage_size=1234,
            recorded_at=datetime(2026, 1, 1),
        ),
        SimpleNamespace(
            id=2,
            archive_key="two",
            steam_id="76561198000000002",
            player_id=8,
            source_replay_id=1063,
            source_replay_filename="replay-two.barodemo.zip",
            source_replay_hash="hash-two",
            speaker_id=43,
            start_ms=200,
            end_ms=1_200,
            packet_count=14,
            radio_packet_count=4,
            storage_format="wav-gzip",
            storage_size=2345,
            recorded_at=datetime(2026, 1, 2),
        ),
    ]
    selections = [
        SimpleNamespace(clip_id=1, slot=0, band="early"),
        SimpleNamespace(clip_id=2, slot=24, band="latest"),
    ]
    players = [
        SimpleNamespace(player_id=7, player_steamid="76561198000000001", display_name="Alice", player_last_time_name=None),
        SimpleNamespace(player_id=8, player_steamid="76561198000000002", display_name=None, player_last_time_name="Bob"),
    ]

    result = _profile_players_payload(clips, selections, players)

    assert [player["display_name"] for player in result] == ["Alice", "Bob"]
    assert result[0]["clips"][0]["audio_url"] == "/api/v1/admin/voice-profiles/clips/1/audio"
    assert result[0]["clips"][0]["band"] == "early"
    assert result[1]["clips"][0]["radio_packet_count"] == 4
    assert result[1]["clips"][0]["band"] == "latest"


def _u16_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<H", len(encoded)) + encoded


def _demo_header(version: int) -> bytes:
    return (
        b"BDMO"
        + struct.pack("<HHBQ", version, 1, 10, 0)
        + b"".join(_u16_string(value) for value in ("server", "seed", "level", "sub"))
        + struct.pack("<I", 0)
    )


def _spawn_frame(version: int, time_ms: int, speaker_id: int, name: str) -> bytes:
    frame = b"\x03" + struct.pack("<IH", time_ms, speaker_id)
    frame += _u16_string("character") + _u16_string(name) + b"\x00"
    if version >= 7:
        frame += _u16_string("job")
    return frame


def _voice_frame(version: int, time_ms: int, speaker_id: int, payload: bytes, radio: bool = False) -> bytes:
    marker = b"\x09" if version >= 7 else b"\x07"
    frame = marker + struct.pack("<IH", time_ms, speaker_id)
    if version >= 7:
        frame += bytes([int(radio)])
    return frame + struct.pack("<H", len(payload)) + payload


def test_select_profile_candidates_keeps_three_early_three_middle_and_five_latest():
    origin = datetime(2026, 1, 1)
    candidates = [
        {
            "candidate_key": f"clip-{index}",
            "recorded_at": origin + timedelta(days=index),
            "start_ms": index * 1000,
        }
        for index in range(40)
    ]

    selected = select_profile_candidates(candidates)

    assert len(selected) == 11
    assert [row["candidate_key"] for row in selected[:3]] == ["clip-0", "clip-1", "clip-2"]
    assert [row["candidate_key"] for row in selected[-5:]] == [f"clip-{i}" for i in range(35, 40)]
    assert [row["candidate_key"] for row in selected[3:6]] == ["clip-3", "clip-19", "clip-34"]


def test_group_voice_packets_keeps_interleaved_speakers_and_radio_metadata():
    packets = [
        VoicePacket(0, 100, b"a", False),
        VoicePacket(20, 200, b"b", True),
        VoicePacket(40, 100, b"c", False),
        VoicePacket(2_000, 100, b"d", True),
    ]

    segments = group_voice_packets(packets)

    assert [(row["speaker_id"], row["start_ms"], row["end_ms"]) for row in segments] == [
        (100, 0, 40),
        (200, 20, 20),
        (100, 2_000, 2_000),
    ]
    assert segments[0]["radio_packet_count"] == 0
    assert segments[2]["radio_packet_count"] == 1


def test_vpk1_round_trip_preserves_timestamps_radio_and_opus_payload(tmp_path):
    packets = [
        VoicePacket(12, 42, b"opus-1", False),
        VoicePacket(32, 42, b"opus-2", True),
    ]
    path = tmp_path / "speaker.vpk1"

    write_vpk1(path, packets)

    assert read_vpk1(path, speaker_id=42) == packets


def test_vpk1_rejects_trailing_bytes(tmp_path):
    path = tmp_path / "broken.vpk1"
    path.write_bytes(b"VPK1" + b"\x01\x00" + b"\x00\x00\x00\x00" + b"junk")

    with pytest.raises(ValueError, match="trailing"):
        read_vpk1(path, speaker_id=42)


def test_write_wave_slice_copies_only_requested_pcm_frames(tmp_path):
    source = tmp_path / "source.wav"
    destination = tmp_path / "slice.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48_000)
        audio.writeframes(struct.pack("<hhhh", 1, 2, 3, 4))

    write_wave_slice(source, destination, start_frame=1, end_frame=3)

    with wave.open(str(destination), "rb") as audio:
        assert audio.getparams() == (1, 2, 48_000, 2, "NONE", "not compressed")
        assert audio.readframes(audio.getnframes()) == struct.pack("<hh", 2, 3)


def test_extract_segment_wav_reuses_cached_speaker_stream(monkeypatch, tmp_path):
    packets = [VoicePacket(100, 42, b"first"), VoicePacket(2_000, 42, b"later")]
    segment = {"candidate_key": "segment", "packet_records": [packets[0]]}
    calls = []

    def fake_decode(decoder_path, input_path, output_path, index_path):
        calls.append((input_path, output_path, index_path))
        output_path.write_bytes(b"stream")
        index_path.write_text(
            json.dumps([
                {"timestamp_ms": 100, "sample_start": 0, "sample_count": 2},
                {"timestamp_ms": 2_000, "sample_start": 2, "sample_count": 2},
            ]),
            encoding="utf-8",
        )

    monkeypatch.setattr("services_voice_profiles.decode_vpk1", fake_decode)
    monkeypatch.setattr(
        "services_voice_profiles.write_wave_slice",
        lambda source, output, start_frame, end_frame: output.write_bytes(
            f"{start_frame}:{end_frame}".encode()
        ),
    )
    cache = {}
    first = extract_segment_wav(
        "decoder", packets, segment, tmp_path, stream_cache=cache, stream_key=(7, 42)
    )
    second_segment = {"candidate_key": "segment-2", "packet_records": [packets[1]]}
    second = extract_segment_wav(
        "decoder", packets, second_segment, tmp_path, stream_cache=cache, stream_key=(7, 42)
    )

    assert len(calls) == 1
    assert first.read_bytes() == b"0:2"
    assert second.read_bytes() == b"2:4"


def test_profile_candidate_metadata_does_not_retain_packet_payload(monkeypatch, tmp_path):
    first = VoicePacket(100, 42, b"first")
    later = VoicePacket(2_000, 42, b"later")
    replay_path = tmp_path / "replay.barodemo"
    replay_path.write_bytes(b"replay")
    segment = SimpleNamespace(
        replay_id=7,
        speaker_id=42,
        steam_id="76561198000000001",
        player_id=101,
        start_ms=100,
        end_ms=100,
        packet_count=1,
        radio_packet_count=0,
    )
    replay = SimpleNamespace(
        id=7,
        filename="replay.barodemo",
        recorded_at=None,
        uploaded_at=None,
    )

    class Query:
        def join(self, *args):
            return self

        def filter(self, *args):
            return self

        def all(self):
            return [(segment, replay)]

    class FakeDb:
        def query(self, *args):
            return Query()

    class FakeReplayService:
        def __init__(self, db):
            pass

        def get_file_path(self, value):
            return replay_path

    monkeypatch.setattr("services_replays.ReplayService", FakeReplayService)
    monkeypatch.setattr(
        "services_voice_profiles.extract_voice_packet_records",
        lambda path: ReplayVoiceExtraction(
            version=7,
            packets=[first, later],
            mapping={},
            names_by_speaker={},
            complete=True,
            warnings=[],
        ),
    )

    service = VoiceProfileService.__new__(VoiceProfileService)
    service.db = FakeDb()
    candidates = service._profile_candidates("76561198000000001")

    assert "packet_records" not in candidates[0]
    assert "_all_speaker_packets" not in candidates[0]


def test_profile_candidates_cache_replay_hash_and_keep_metadata_light(monkeypatch, tmp_path):
    first = VoicePacket(100, 42, b"first")
    second = VoicePacket(2_000, 42, b"second")
    segment_one = SimpleNamespace(
        speaker_id=42,
        player_id=9,
        start_ms=100,
        end_ms=100,
        packet_count=1,
        radio_packet_count=0,
    )
    segment_two = SimpleNamespace(
        speaker_id=42,
        player_id=9,
        start_ms=2_000,
        end_ms=2_000,
        packet_count=1,
        radio_packet_count=0,
    )
    replay_path = tmp_path / "replay.barodemo"
    replay_path.write_bytes(b"replay")
    replay = SimpleNamespace(id=7, filename="replay.barodemo", recorded_at=None, uploaded_at=None)

    class Query:
        def join(self, *args):
            return self

        def filter(self, *args):
            return self

        def all(self):
            return [(segment_one, replay), (segment_two, replay)]

    class FakeDb:
        def query(self, *args):
            return Query()

    class FakeReplayService:
        def __init__(self, db):
            pass

        def get_file_path(self, value):
            return replay_path

    hash_calls = []
    monkeypatch.setattr("services_replays.ReplayService", FakeReplayService)
    monkeypatch.setattr(
        "services_voice_profiles.extract_voice_packet_records",
        lambda path: ReplayVoiceExtraction(
            version=7,
            packets=[first, second],
            mapping={},
            names_by_speaker={},
            complete=True,
            warnings=[],
        ),
    )
    monkeypatch.setattr(
        "services_voice_profiles._file_sha256",
        lambda path: hash_calls.append(path) or "hash",
    )

    service = VoiceProfileService.__new__(VoiceProfileService)
    service.db = FakeDb()
    candidates = service._profile_candidates("76561198000000001")

    assert len(candidates) == 2
    assert len(hash_calls) == 1
    assert all("_all_speaker_packets" not in candidate for candidate in candidates)


def test_old_profile_archive_version_is_materialized_again(tmp_path):
    from services_voice_profiles import needs_materialization

    wav_path = tmp_path / "clip.wav.gz"
    ndvc_path = tmp_path / "clip.ndvc"
    wav_path.write_bytes(b"old-wav")
    ndvc_path.write_bytes(b"old-ndvc")

    old_clip = SimpleNamespace(archive_version=VOICE_PROFILE_ARCHIVE_VERSION - 1)
    current_clip = SimpleNamespace(archive_version=VOICE_PROFILE_ARCHIVE_VERSION)

    assert needs_materialization(old_clip, wav_path, ndvc_path) is True
    assert needs_materialization(current_clip, wav_path, ndvc_path) is False


def test_stale_cleanup_keeps_clips_from_missing_source_replays():
    missing = SimpleNamespace(source_replay_id=1060)
    available = SimpleNamespace(source_replay_id=1061)

    assert stale_profile_clips([missing, available], {1061}) == [available]
    assert stale_profile_clips([missing], set()) == []


def test_extract_voice_packets_supports_v4_and_reads_character_names(tmp_path):
    path = tmp_path / "v4.barodemo"
    path.write_bytes(
        _demo_header(4)
        + _spawn_frame(4, 100, 42, "Alice")
        + _voice_frame(4, 100, 42, b"opus")
        + b"\xff"
        + struct.pack("<I", 500),
    )

    result = extract_voice_packet_records(path)

    assert result.version == 4
    assert result.complete is True
    assert result.packets == [VoicePacket(100, 42, b"opus", False)]
    assert result.names_by_speaker == {42: {"Alice"}}


def test_extract_voice_packets_supports_v7_radio_and_partial_eof(tmp_path):
    path = tmp_path / "v7.barodemo"
    path.write_bytes(
        _demo_header(7)
        + _spawn_frame(7, 200, 77, "Bob")
        + _voice_frame(7, 200, 77, b"radio-opus", radio=True)
    )

    result = extract_voice_packet_records(path)

    assert result.version == 7
    assert result.complete is False
    assert result.packets == [VoicePacket(200, 77, b"radio-opus", True)]
    assert result.names_by_speaker == {77: {"Bob"}}
    assert result.warnings == ["replay ended before FRAME_END"]


def test_extract_voice_packets_drops_zero_speaker_and_bad_opus_length(tmp_path):
    path = tmp_path / "damaged-v4.barodemo"
    path.write_bytes(
        _demo_header(4)
        + _voice_frame(4, 100, 0, b"ignored")
        + _voice_frame(4, 120, 42, b"x" * 41)
        + _voice_frame(4, 140, 42, b"valid")
    )

    result = extract_voice_packet_records(path)

    assert result.packets == [VoicePacket(140, 42, b"valid", False)]
    assert result.warnings == ["invalid Opus packet for speaker_id 42", "replay ended before FRAME_END"]
