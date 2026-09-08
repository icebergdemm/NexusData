import unittest
from datetime import datetime, timezone

from services import _resolve_online_join_time, _session_login_time_to_msk


class OnlineSessionTimeTests(unittest.TestCase):
    def test_active_session_login_time_wins_over_snapshot_time(self):
        actual_login = datetime(2026, 9, 6, 12, 0)
        snapshot_time = datetime(2026, 9, 6, 12, 25)

        self.assertEqual(
            _resolve_online_join_time(
                existing={"join_time": snapshot_time},
                active_session_login_time=actual_login,
                now=datetime(2026, 9, 6, 12, 30),
            ),
            actual_login,
        )

    def test_existing_snapshot_time_is_fallback_without_active_session(self):
        snapshot_time = datetime(2026, 9, 6, 12, 25)

        self.assertEqual(
            _resolve_online_join_time(
                existing={"join_time": snapshot_time},
                active_session_login_time=None,
                now=datetime(2026, 9, 6, 12, 30),
            ),
            snapshot_time,
        )

    def test_new_player_without_active_session_uses_now(self):
        now = datetime(2026, 9, 6, 12, 30)

        self.assertEqual(
            _resolve_online_join_time(
                existing=None,
                active_session_login_time=None,
                now=now,
            ),
            now,
        )

    def test_stored_utc_session_time_is_converted_to_msk(self):
        stored_utc = datetime(2026, 9, 6, 9, 0)

        self.assertEqual(
            _session_login_time_to_msk(stored_utc),
            datetime(2026, 9, 6, 12, 0),
        )

    def test_aware_session_time_is_converted_to_msk(self):
        stored_utc = datetime(2026, 9, 6, 9, 0, tzinfo=timezone.utc)

        self.assertEqual(
            _session_login_time_to_msk(stored_utc),
            datetime(2026, 9, 6, 12, 0),
        )


if __name__ == "__main__":
    unittest.main()
