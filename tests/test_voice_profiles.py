import json
import struct
import wave
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from services_voice_profiles import (
    VOICE_PROFILE_ARCHIVE_VERSION,
    ReplayVoiceExtraction,
    VoicePacket,
    VoiceProfileService,
    extract_voice_packet_records,
    extract_segment_wav,
    group_voice_packets,
    read_vpk1,
    stale_profile_clips,
    select_profile_candidates,
    write_vpk1,
    write_wave_slice,
)


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


def test_select_profile_candidates_keeps_five_early_ten_middle_and_ten_latest():
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

    assert len(selected) == 25
    assert [row["candidate_key"] for row in selected[:5]] == [f"clip-{i}" for i in range(5)]
    assert [row["candidate_key"] for row in selected[-10:]] == [f"clip-{i}" for i in range(30, 40)]
    assert [row["candidate_key"] for row in selected[5:15]] == [
        "clip-5", "clip-8", "clip-10", "clip-13", "clip-16",
        "clip-18", "clip-21", "clip-24", "clip-26", "clip-29",
    ]


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


def test_profile_candidate_keeps_segment_packets_separate_from_speaker_stream(monkeypatch, tmp_path):
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

    assert candidates[0]["packet_records"] == [first]
    assert candidates[0]["_all_speaker_packets"] == [first, later]


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
