import os
import tempfile
import unittest
import zipfile

from services_replays import build_replay_download_archive


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


if __name__ == "__main__":
    unittest.main()
