import os
import tempfile
import unittest
import zipfile
import json

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
