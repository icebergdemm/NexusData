import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import services_logs
from routes import replays


class _FakeDb:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class ReplayDatabaseReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_round_search_releases_mysql_before_slow_log_scan(self):
        db = _FakeDb()
        started = datetime(2026, 8, 30, 10, 0)
        replay = SimpleNamespace(
            id=77,
            server_name="ASPER1",
            recorded_at=started,
            uploaded_at=started + timedelta(minutes=10),
            duration_ms=600_000,
        )

        async def run_after_release(func):
            self.assertTrue(db.closed)
            return func()

        empty_result = services_logs.LogSearchResult([], 0, False, False, None)
        with (
            patch.object(replays, "_get_admin", return_value=SimpleNamespace()),
            patch.object(replays, "check_permission", return_value=True),
            patch.object(replays, "ReplayService") as replay_service,
            patch.object(replays, "_round_for_replay", return_value=None),
            patch.object(replays, "run_in_threadpool", side_effect=run_after_release),
            patch.object(services_logs, "search_logs_in_round", return_value=empty_result),
        ):
            replay_service.return_value.get_by_id.return_value = replay
            response = await replays.api_replay_logs(
                request=SimpleNamespace(),
                replay_id=77,
                player=None,
                action_type=None,
                category=None,
                search="физ",
                round_search=True,
                limit=5000,
                from_ms=0,
                to_ms=600_000,
                overview_only=False,
                duration_ms=600_000,
                player_scope="related",
                db=db,
            )

        self.assertTrue(db.closed)
        payload = json.loads(response.body)
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
