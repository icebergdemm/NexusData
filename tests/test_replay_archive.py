import io
import os
import tempfile
import unittest
import zipfile
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import AppSetting, RoundReplay
from services_replays import ReplayService, build_replay_download_archive, iter_replay_download_archive


class ReplayArchiveTests(unittest.TestCase):
    def test_accepts_complete_nwdemo_contract(self):
        with tempfile.TemporaryDirectory(prefix="nexus-nwdemo-") as directory:
            path = os.path.join(directory, "replay.nwdemo")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({
                    "format": "nwdemo-v2", "world_format": "BDMO", "world_version": 7, "round_id": "round-1"
                }))
                archive.writestr("world.bin", b"BDMO\x07")
                archive.writestr("voice.bin", b"")
            ReplayService._validate_nwdemo(path)

    def test_extracts_broken_nwdemo_marker(self):
        with tempfile.TemporaryDirectory(prefix="nexus-nwdemo-") as directory:
            path = os.path.join(directory, "replay.nwdemo")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({
                    "format": "nwdemo-v2", "world_format": "BDMO", "world_version": 7,
                    "round_id": "round-1", "is_broken": True
                }))
                archive.writestr("world.bin", b"BDMO\x07")
                archive.writestr("voice.bin", b"")
            self.assertEqual(ReplayService._extract_metadata(path)["is_broken"], 1)

    def test_rejects_nwdemo_without_world_stream(self):
        with tempfile.TemporaryDirectory(prefix="nexus-nwdemo-") as directory:
            path = os.path.join(directory, "replay.nwdemo")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", b'{"format":"nwdemo-v2"}')
                archive.writestr("voice.bin", b"")
            with self.assertRaises(Exception):
                ReplayService._validate_nwdemo(path)

    def test_allows_empty_nwdemo_world_only_with_explicit_lobby_allowance(self):
        with tempfile.TemporaryDirectory(prefix="nexus-nwdemo-") as directory:
            path = os.path.join(directory, "lobby.nwdemo")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({
                    "format": "nwdemo-v2", "world_format": "BDMO", "world_version": 7, "round_id": "lobby-1"
                }))
                archive.writestr("world.bin", b"")
                archive.writestr("voice.bin", b"")
            with self.assertRaises(Exception):
                ReplayService._validate_nwdemo(path)
            ReplayService._validate_nwdemo(path, allow_empty_world=True)

    def test_wraps_raw_replay_in_real_zip_without_changing_bytes(self):
        with tempfile.TemporaryDirectory(prefix="nexus-replay-archive-") as directory:
            source = os.path.join(directory, "source.barodemo")
            destination = os.path.join(directory, "download.barodemo.zip")
            payload = b"BARO" + bytes(range(64))
            with open(source, "wb") as replay_file:
                replay_file.write(payload)

            build_replay_download_archive(source, destination, "../round.barodemo")

            self.assertTrue(zipfile.is_zipfile(destination))
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(archive.namelist(), ["round.barodemo"])
                self.assertEqual(archive.read("round.barodemo"), payload)

    def test_streams_real_zip_without_temporary_destination(self):
        with tempfile.TemporaryDirectory(prefix="nexus-replay-stream-") as directory:
            source = os.path.join(directory, "source.barodemo")
            payload = (b"BDMO" + bytes(range(251))) * 8192
            with open(source, "wb") as replay_file:
                replay_file.write(payload)

            chunks = iter_replay_download_archive(
                source,
                "../round.barodemo",
                chunk_size=64 * 1024,
            )
            first_chunk = next(chunks)
            self.assertTrue(first_chunk.startswith(b"PK\x03\x04"))
            archive_bytes = first_chunk + b"".join(chunks)

            archive_path = os.path.join(directory, "streamed.zip")
            with open(archive_path, "wb") as archive_file:
                archive_file.write(archive_bytes)
            self.assertTrue(zipfile.is_zipfile(archive_path))
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.namelist(), ["round.barodemo"])
                self.assertEqual(archive.read("round.barodemo"), payload)


if __name__ == "__main__":
    unittest.main()


def _nwdemo_bytes(manifest: dict, world: bytes = b"BDMO\x07") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("world.bin", world)
        archive.writestr("voice.bin", b"")
    return buffer.getvalue()


class _MemoryUpload:
    """Minimal async upload source for ReplayService.save_upload_stream."""

    def __init__(self, data: bytes, filename: str):
        self._data = data
        self._pos = 0
        self.filename = filename

    async def read(self, size: int = -1):
        if size < 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos:self._pos + size]
            self._pos += len(chunk)
        return chunk


class ReplayLobbyLinkTests(unittest.IsolatedAsyncioTestCase):
    def _service(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[RoundReplay.__table__, AppSetting.__table__])
        db = sessionmaker(bind=engine)()
        svc = ReplayService(db)
        tmp = tempfile.TemporaryDirectory(prefix="nexus-replay-upload-")
        svc.upload_dir = tmp.name
        # addCleanup runs LIFO: close the session before disposing the engine.
        self.addCleanup(tmp.cleanup)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return svc, db

    def test_metadata_extracts_round_link_ids(self):
        with tempfile.TemporaryDirectory(prefix="nexus-nwdemo-") as directory:
            path = os.path.join(directory, "lobby.nwdemo")
            with open(path, "wb") as handle:
                handle.write(_nwdemo_bytes({
                    "format": "nwdemo-v2", "world_format": "BDMO", "world_version": 7,
                    "round_id": "lobby-stream-9", "replay_kind": "lobby",
                    "after_round_stream_id": "round-stream-1",
                }))
            meta = ReplayService._extract_metadata(path)
        self.assertEqual(meta["round_id"], "lobby-stream-9")
        self.assertEqual(meta["after_round_stream_id"], "round-stream-1")
        self.assertEqual(meta["replay_kind"], "lobby")

    async def test_file_upload_stores_stream_id_and_lobby_link(self):
        svc, db = self._service()

        round_replay = await svc.save_upload_stream(
            _MemoryUpload(_nwdemo_bytes({
                "format": "nwdemo-v2", "world_format": "BDMO", "world_version": 7,
                "round_id": "round-stream-1", "replay_kind": "round",
                "server_name": "ASPER1", "started_at": 1756540800000,
            }), "replay_round.nwdemo"),
            server_name="ASPER1",
        )
        self.assertEqual(round_replay.stream_id, "round-stream-1")
        self.assertIsNone(round_replay.after_round_stream_id)

        lobby_replay = await svc.save_upload_stream(
            _MemoryUpload(_nwdemo_bytes({
                "format": "nwdemo-v2", "world_format": "BDMO", "world_version": 7,
                "round_id": "lobby-stream-9", "replay_kind": "lobby",
                "after_round_stream_id": "round-stream-1",
                "server_name": "ASPER1", "started_at": 1756544400000,
            }), "replay_lobby.nwdemo"),
            server_name="ASPER1",
            replay_kind="lobby_dump",
            after_round_stream_id="round-stream-1",
        )
        self.assertEqual(lobby_replay.stream_id, "lobby-stream-9")
        self.assertEqual(lobby_replay.after_round_stream_id, "round-stream-1")

        # Same lookup the round detail endpoint uses.
        matched = db.query(RoundReplay).filter(
            RoundReplay.replay_kind.in_(("lobby", "lobby_dump")),
            RoundReplay.after_round_stream_id == round_replay.stream_id,
        ).first()
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, lobby_replay.id)

    async def test_duplicate_upload_retry_returns_stored_row(self):
        svc, db = self._service()
        payload = _nwdemo_bytes({
            "format": "nwdemo-v2", "world_format": "BDMO", "world_version": 7,
            "round_id": "round-stream-2", "replay_kind": "round",
            "server_name": "ASPER1", "started_at": 1756540800000,
        })

        first = await svc.save_upload_stream(_MemoryUpload(payload, "replay.nwdemo"), server_name="ASPER1")
        second = await svc.save_upload_stream(_MemoryUpload(payload, "replay.nwdemo"), server_name="ASPER1")

        self.assertEqual(second.id, first.id)
        self.assertEqual(db.query(RoundReplay).count(), 1)
