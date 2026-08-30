import os
import time
import unittest
from datetime import datetime
from types import SimpleNamespace

from routes.replays import _replay_log_timezone_name, _utc_anchor_to_log_clock


class ReplayLogTimezoneTests(unittest.TestCase):
    @staticmethod
    def _config(**overrides):
        values = {
            "logs_default_timezone": "Europe/Moscow",
            "sftp_server_1_name": "ASPER1",
            "sftp_server_1_logs_timezone": "",
            "sftp_server_2_name": "ASPER2",
            "sftp_server_2_logs_timezone": "",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_moscow_is_utc_plus_three_in_summer_and_winter(self):
        config = self._config()

        summer, summer_zone = _utc_anchor_to_log_clock(
            datetime(2026, 8, 30, 5, 15), "ASPER1", config
        )
        winter, winter_zone = _utc_anchor_to_log_clock(
            datetime(2026, 1, 15, 5, 15), "ASPER1", config
        )

        self.assertEqual(summer, datetime(2026, 8, 30, 8, 15))
        self.assertEqual(winter, datetime(2026, 1, 15, 8, 15))
        self.assertEqual(summer_zone, "Europe/Moscow")
        self.assertEqual(winter_zone, "Europe/Moscow")

    def test_dst_transition_uses_per_server_zoneinfo_rules(self):
        config = self._config(sftp_server_1_logs_timezone="Europe/Kyiv")

        before, _ = _utc_anchor_to_log_clock(
            datetime(2026, 3, 29, 0, 30), "ASPER1", config
        )
        after, _ = _utc_anchor_to_log_clock(
            datetime(2026, 3, 29, 1, 30), "ASPER1", config
        )

        self.assertEqual(before, datetime(2026, 3, 29, 2, 30))
        self.assertEqual(after, datetime(2026, 3, 29, 4, 30))

    def test_server_override_is_case_insensitive_and_does_not_leak(self):
        config = self._config(sftp_server_2_logs_timezone="Europe/Warsaw")

        asper2, asper2_zone = _utc_anchor_to_log_clock(
            datetime(2026, 8, 30, 5, 15), " asper2 ", config
        )
        asper1, asper1_zone = _utc_anchor_to_log_clock(
            datetime(2026, 8, 30, 5, 15), "ASPER1", config
        )

        self.assertEqual(asper2, datetime(2026, 8, 30, 7, 15))
        self.assertEqual(asper2_zone, "Europe/Warsaw")
        self.assertEqual(asper1, datetime(2026, 8, 30, 8, 15))
        self.assertEqual(asper1_zone, "Europe/Moscow")
        self.assertEqual(_replay_log_timezone_name("unknown", config), "Europe/Moscow")

    @unittest.skipUnless(hasattr(time, "tzset"), "tzset is unavailable")
    def test_conversion_is_independent_of_process_timezone(self):
        config = self._config()
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Pacific/Honolulu"
            time.tzset()
            converted, zone = _utc_anchor_to_log_clock(
                datetime(2026, 1, 15, 5, 15), "ASPER1", config
            )
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

        self.assertEqual(converted, datetime(2026, 1, 15, 8, 15))
        self.assertEqual(zone, "Europe/Moscow")


if __name__ == "__main__":
    unittest.main()
