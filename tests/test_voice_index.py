import json
import os
import struct
import tempfile
import unittest
import zipfile

from services_voice_index import (
    VoiceMappingError,
    extract_voice_segments,
    group_voice_packets,
)


def _str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def _demo(*voice_frames: tuple[int, int, bytes], version: int = 4) -> bytes:
    data = (
        b"BDMO"
        + struct.pack("<HHBq", version, 1, 10, 1753000000000)
        + _str("ASPER1")
        + _str("seed")
        + _str("Ridge")
        + _str("Orca")
        + struct.pack("<I", 0)
    )
    for time_ms, speaker_id, payload in voice_frames:
        data += bytes([0x07]) + struct.pack("<IHH", time_ms, speaker_id, len(payload)) + payload
    data += bytes([0xFF]) + struct.pack("<I", 2000)
    return data


def _demo_with_snapshot(version: int) -> bytes:
    data = bytearray(
        b"BDMO"
        + struct.pack("<HHBq", version, 1, 10, 1753000000000)
        + _str("ASPER1")
        + _str("seed")
        + _str("Ridge")
        + _str("Orca")
        + struct.pack("<I", 0)
    )
    data += bytes([0x01]) + struct.pack("<IH", 100, 1)
    data += struct.pack("<HhhBB", 7, 10, 20, 4, 255)
    if version >= 7:
        data += bytes([100])  # WorldDemoStreamer v7 oxygen byte.
    data += struct.pack("<H", 0)  # doors
    data += struct.pack("<H", 0)  # hull water
    data += struct.pack("<H", 0)  # submarine positions
    if version >= 7:
        data += bytes([0x09]) + struct.pack("<IHBH", 200, 7, 0, 1) + b"v"
    else:
        data += bytes([0x07]) + struct.pack("<IHH", 200, 7, 1) + b"v"
    data += bytes([0xFF]) + struct.pack("<I", 1000)
    return bytes(data)


class VoiceIndexTests(unittest.TestCase):
    def test_groups_contiguous_packets_by_speaker_and_keeps_packet_count(self):
        packets = [(100, 7), (300, 7), (500, 8), (900, 7), (1500, 8), (2500, 7)]

        self.assertEqual(
            group_voice_packets(packets, merge_gap_ms=1000),
            [
                {"speaker_id": 7, "start_ms": 100, "end_ms": 900, "packet_count": 3},
                {"speaker_id": 8, "start_ms": 500, "end_ms": 1500, "packet_count": 2},
                {"speaker_id": 7, "start_ms": 2500, "end_ms": 2500, "packet_count": 1},
            ],
        )

    def test_extracts_voice_from_zip_and_uses_exact_speaker_mapping(self):
        with tempfile.TemporaryDirectory(prefix="voice-index-") as directory:
            path = os.path.join(directory, "round.barodemo.zip")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("round.barodemo", _demo((100, 7, b"a"), (300, 7, b"b")))
                archive.writestr("voice-map.json", json.dumps({"7": "steam-7"}))

            self.assertEqual(
                extract_voice_segments(path),
                [
                    {
                        "steam_id": "steam-7",
                        "speaker_id": 7,
                        "start_ms": 100,
                        "end_ms": 300,
                        "packet_count": 2,
                    }
                ],
            )

    def test_refuses_unmapped_voice_instead_of_attributing_by_nickname(self):
        with tempfile.TemporaryDirectory(prefix="voice-index-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo((100, 7, b"a")))

            with self.assertRaises(VoiceMappingError):
                extract_voice_segments(path)

    def test_reads_voice_after_v4_and_v7_snapshots_without_losing_alignment(self):
        for version in (4, 7):
            with self.subTest(version=version), tempfile.TemporaryDirectory(prefix="voice-index-") as directory:
                path = os.path.join(directory, f"round-v{version}.barodemo")
                with open(path, "wb") as replay_file:
                    replay_file.write(_demo_with_snapshot(version))

                segments = extract_voice_segments(path, {7: "steam-7"})
                self.assertEqual(segments[0]["start_ms"], 200)
                self.assertEqual(segments[0]["steam_id"], "steam-7")


if __name__ == "__main__":
    unittest.main()
