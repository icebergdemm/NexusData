import struct
import wave
from datetime import datetime, timedelta

import pytest

from services_voice_profiles import (
    VoicePacket,
    extract_voice_packet_records,
    group_voice_packets,
    read_vpk1,
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
