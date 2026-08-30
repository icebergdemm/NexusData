import os
import tempfile
import unittest
import zipfile

from services_replays import build_replay_download_archive, iter_replay_download_archive


class ReplayArchiveTests(unittest.TestCase):
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
