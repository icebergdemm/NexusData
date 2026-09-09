import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services_voice_archive import (
    read_ndvc,
    select_voice_archive_candidates,
    write_ndvc,
)
from services_voice_index import VoicePacket, extract_voice_packet_records
import models
from services_voice_archive import VoiceArchiveService


def _str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return len(raw).to_bytes(2, "little") + raw


def _demo(*voice_frames: tuple[int, int, bytes], version: int = 4) -> bytes:
    data = (
        b"BDMO"
        + version.to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + bytes([10])
        + (1753000000000).to_bytes(8, "little", signed=True)
        + _str("ASPER1")
        + _str("seed")
        + _str("Ridge")
        + _str("Orca")
        + (0).to_bytes(4, "little")
    )
    for time_ms, speaker_id, payload in voice_frames:
        data += bytes([0x07])
        data += time_ms.to_bytes(4, "little")
        data += speaker_id.to_bytes(2, "little")
        data += len(payload).to_bytes(2, "little")
        data += payload
    return data + bytes([0xFF]) + (2000).to_bytes(4, "little")


class VoiceArchiveTests(unittest.TestCase):
    def test_extracts_encoded_voice_packets_from_replay(self):
        with tempfile.TemporaryDirectory(prefix="voice-archive-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay:
                replay.write(_demo((100, 7, b"first"), (300, 7, b"second")))

            packets, mapping, names = extract_voice_packet_records(path)

        self.assertEqual([(p.time_ms, p.speaker_id, p.data) for p in packets], [
            (100, 7, b"first"),
            (300, 7, b"second"),
        ])
        self.assertEqual(mapping, {})
        self.assertEqual(names, {})

    def test_ndvc_round_trip_preserves_relative_packet_times_and_bytes(self):
        packets = [
            VoicePacket(100, 7, b"first"),
            VoicePacket(300, 7, b"second"),
        ]
        with tempfile.TemporaryDirectory(prefix="voice-archive-") as directory:
            path = os.path.join(directory, "clip.ndvc")
            write_ndvc(path, packets)
            self.assertEqual(read_ndvc(path), [(0, b"first"), (200, b"second")])

    def test_selects_five_early_ten_middle_and_ten_latest(self):
        candidates = [
            {"key": f"clip-{index:02d}", "time_ms": index * 1000}
            for index in range(40)
        ]

        selected = select_voice_archive_candidates(candidates)

        self.assertEqual(len(selected), 25)
        self.assertEqual([row["key"] for row in selected[:5]], [
            "clip-00", "clip-01", "clip-02", "clip-03", "clip-04",
        ])
        self.assertEqual([row["key"] for row in selected[-10:]], [
            f"clip-{index:02d}" for index in range(30, 40)
        ])
        self.assertEqual(len({row["key"] for row in selected}), 25)

    def test_keeps_all_candidates_when_fewer_than_twenty_five(self):
        candidates = [{"key": str(index), "time_ms": index} for index in range(7)]

        selected = select_voice_archive_candidates(candidates)

        self.assertEqual([row["key"] for row in selected], [str(index) for index in range(7)])

    def test_reconcile_materializes_exactly_twenty_five_current_segments(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        models.Base.metadata.create_all(engine, tables=[
            models.RoundReplay.__table__,
            models.ReplayVoiceSegment.__table__,
            models.VoiceArchiveClip.__table__,
            models.VoiceArchiveSelection.__table__,
        ])
        db = Session(engine)
        try:
            replay = models.RoundReplay(
                id=77,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                recorded_at=datetime(2026, 9, 1),
                uploaded_at=datetime(2026, 9, 1),
            )
            db.add(replay)
            packets = [VoicePacket(index * 2001, 7, bytes([index])) for index in range(26)]
            db.add_all([
                models.ReplayVoiceSegment(
                    replay_id=replay.id,
                    steam_id="steam-7",
                    speaker_id=7,
                    start_ms=packet.time_ms,
                    end_ms=packet.time_ms,
                    packet_count=1,
                )
                for packet in packets
            ])
            db.commit()

            with tempfile.TemporaryDirectory(prefix="voice-archive-root-") as archive_root:
                archive = VoiceArchiveService(db, root=archive_root)
                result = archive.reconcile_steam_id(
                    "steam-7",
                    current_replay_id=replay.id,
                    current_packets=packets,
                )
                db.commit()

                self.assertEqual(result["selected"], 25)
                self.assertEqual(db.query(models.VoiceArchiveSelection).count(), 25)
                self.assertEqual(db.query(models.VoiceArchiveClip).count(), 25)
                groups, total_groups, total_clips = archive.get_clips_for_steam_id("steam-7", 1, 8)
                self.assertEqual((total_groups, total_clips), (1, 25))
                self.assertEqual(len(groups[0]["clips"]), 25)

                db.query(models.RoundReplay).filter(models.RoundReplay.id == replay.id).delete(
                    synchronize_session=False,
                )
                db.commit()
                self.assertEqual(db.query(models.VoiceArchiveSelection).count(), 25)
                self.assertEqual(db.query(models.VoiceArchiveClip).count(), 25)
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
