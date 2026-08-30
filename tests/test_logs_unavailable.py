import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import services_logs


class _FailingQueryConnection:
    """Small sqlite-like object that fails after the connection was opened."""

    def set_progress_handler(self, _handler, _granularity):
        return None

    def execute(self, _sql, _params=()):
        raise sqlite3.DatabaseError("broken log index")


class LogUnavailableTests(unittest.TestCase):
    def test_search_keeps_legacy_empty_result_by_default(self):
        with patch.object(
            services_logs,
            "_get_conn",
            side_effect=sqlite3.DatabaseError("storage offline"),
        ):
            result = services_logs.search_logs()

        self.assertEqual(result, services_logs.LogSearchResult([], 0, False, False, None))

    def test_search_strict_mode_distinguishes_storage_failure_from_no_matches(self):
        with patch.object(
            services_logs,
            "_get_conn",
            side_effect=sqlite3.DatabaseError("storage offline"),
        ):
            with self.assertRaises(services_logs.LogDataUnavailable):
                services_logs.search_logs(raise_on_unavailable=True)

    def test_search_strict_mode_reports_query_or_index_failure(self):
        with patch.object(
            services_logs,
            "_get_conn",
            return_value=_FailingQueryConnection(),
        ):
            with self.assertRaises(services_logs.LogDataUnavailable):
                services_logs.search_logs(
                    count_total=False,
                    raise_on_unavailable=True,
                )

    def test_overview_always_reports_storage_failure(self):
        with patch.object(
            services_logs,
            "_get_conn",
            side_effect=sqlite3.DatabaseError("storage offline"),
        ):
            with self.assertRaises(services_logs.LogDataUnavailable):
                services_logs.get_log_timeline_overview(
                    server_name="ASPER1",
                    time_from=datetime(2026, 8, 30, 12, 0),
                    time_to=datetime(2026, 8, 30, 12, 1),
                    bucket_ms=1_000,
                )

    def test_overview_reports_query_or_index_failure(self):
        started_at = datetime(2026, 8, 30, 12, 0)
        with patch.object(
            services_logs,
            "_get_conn",
            return_value=_FailingQueryConnection(),
        ):
            with self.assertRaises(services_logs.LogDataUnavailable):
                services_logs.get_log_timeline_overview(
                    server_name="ASPER1",
                    time_from=started_at,
                    time_to=started_at + timedelta(minutes=1),
                    bucket_ms=1_000,
                )


if __name__ == "__main__":
    unittest.main()
