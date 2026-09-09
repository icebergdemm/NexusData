import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.LOCK_NB = 4
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

from routes import replays


def _make_stream(root, stream_id, chunk_count, age_seconds, start_seq=0):
    directory = os.path.join(root, stream_id)
    os.makedirs(directory, exist_ok=True)
    for offset in range(chunk_count):
        with open(os.path.join(directory, f"{start_seq + offset:08d}.bin"), "wb") as f:
            f.write(b"chunk-%d" % (start_seq + offset))
    old = time.time() - age_seconds
    for name in os.listdir(directory):
        os.utime(os.path.join(directory, name), (old, old))
    os.utime(directory, (old, old))
    return directory


class ReplayStreamFinalizeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="replay-streaming-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_expired_stream_is_packed_as_broken_and_removed(self):
        directory = _make_stream(self.root, "old-stream", 2, age_seconds=400)
        calls = []

        def fake_assemble(dir_path, end, server_name, broken=False, keep_directory=False):
            calls.append((dir_path, end, server_name, broken, keep_directory))
            shutil.rmtree(dir_path, ignore_errors=True)
            return {"success": True, "broken": broken}

        with patch.object(replays, "_assemble_stream", side_effect=fake_assemble):
            result = replays.finalize_expired_streams(root=self.root)

        self.assertEqual(result["finalized"], ["old-stream"])
        self.assertEqual(result["errors"], {})
        self.assertEqual(len(calls), 1)
        dir_path, end, server_name, broken, keep_directory = calls[0]
        self.assertEqual(dir_path, directory)
        self.assertEqual(end, b"")
        self.assertTrue(broken)
        self.assertFalse(keep_directory)
        self.assertFalse(os.path.exists(directory))

    def test_fresh_stream_is_skipped(self):
        directory = _make_stream(self.root, "live-stream", 2, age_seconds=10)
        with patch.object(replays, "_assemble_stream") as assemble:
            result = replays.finalize_expired_streams(root=self.root)

        self.assertEqual(result["finalized"], [])
        self.assertEqual(result["skipped"], ["live-stream"])
        assemble.assert_not_called()
        self.assertTrue(os.path.isdir(directory))

    def test_newest_chunk_mtime_governs_not_directory_mtime(self):
        directory = _make_stream(self.root, "resumed-stream", 2, age_seconds=400)
        # The stream resumed: the newest chunk is fresh even though the
        # directory itself (and older chunks) look abandoned.
        with open(os.path.join(directory, "00000002.bin"), "wb") as f:
            f.write(b"chunk-2-fresh")
        with patch.object(replays, "_assemble_stream") as assemble:
            result = replays.finalize_expired_streams(root=self.root)

        self.assertEqual(result["finalized"], [])
        self.assertEqual(result["skipped"], ["resumed-stream"])
        assemble.assert_not_called()

    def test_orphan_without_sequence_zero_is_skipped(self):
        directory = _make_stream(self.root, "orphan", 1, age_seconds=400, start_seq=3)
        with patch.object(replays, "_assemble_stream") as assemble:
            result = replays.finalize_expired_streams(root=self.root)

        self.assertEqual(result["finalized"], [])
        self.assertEqual(result["skipped"], ["orphan"])
        assemble.assert_not_called()
        self.assertTrue(os.path.isdir(directory))

    def test_failed_assembly_is_reported_per_stream(self):
        _make_stream(self.root, "bad-stream", 1, age_seconds=400)
        _make_stream(self.root, "good-stream", 1, age_seconds=400)

        def fake_assemble(dir_path, end, server_name, broken=False, keep_directory=False):
            if dir_path.endswith("bad-stream"):
                raise RuntimeError("db down")
            return {"success": True, "broken": broken}

        with patch.object(replays, "_assemble_stream", side_effect=fake_assemble):
            result = replays.finalize_expired_streams(root=self.root)

        self.assertEqual(result["finalized"], ["good-stream"])
        self.assertIn("bad-stream", result["errors"])
        self.assertIn("db down", result["errors"]["bad-stream"])

    def test_missing_root_returns_empty_result(self):
        with patch.object(replays, "_assemble_stream") as assemble:
            result = replays.finalize_expired_streams(
                root=os.path.join(self.root, "no-such-dir"))
        self.assertEqual(result, {"finalized": [], "skipped": [], "errors": {}})
        assemble.assert_not_called()

    def test_task_delegates_to_finalizer(self):
        import workers
        with patch.object(
            replays, "finalize_expired_streams",
            return_value={"finalized": ["s"], "skipped": [], "errors": {}},
        ) as finalize:
            result = workers.task_finalize_replay_streams()
        finalize.assert_called_once_with()
        self.assertEqual(result["finalized"], ["s"])

    def test_task_reports_internal_errors(self):
        import workers
        with patch.object(
            replays, "finalize_expired_streams", side_effect=RuntimeError("boom")):
            result = workers.task_finalize_replay_streams()
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["error"])


if __name__ == "__main__":
    unittest.main()
