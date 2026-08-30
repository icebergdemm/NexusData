import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

import services_logs


class LogTimelineOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus-log-timeline-")
        self.original_storage_dir = services_logs.LOG_STORAGE_DIR
        self.original_raw_dir = services_logs.LOG_RAW_DIR
        self.original_db_path = services_logs.LOG_DB_PATH
        self.original_tls = services_logs._tls
        self.original_fts_cache = services_logs._fts_available_cache

        services_logs.LOG_STORAGE_DIR = self.temp_dir
        services_logs.LOG_RAW_DIR = f"{self.temp_dir}/raw"
        services_logs.LOG_DB_PATH = f"{self.temp_dir}/logs.db"
        services_logs._tls = threading.local()
        services_logs._fts_available_cache = None
        services_logs._players_cache.clear()
        services_logs._players_cache_ts.clear()

        self.conn = services_logs._get_conn()
        self.start = datetime(2026, 8, 30, 12, 0, 0)

    def tearDown(self):
        self.conn.close()
        services_logs.LOG_STORAGE_DIR = self.original_storage_dir
        services_logs.LOG_RAW_DIR = self.original_raw_dir
        services_logs.LOG_DB_PATH = self.original_db_path
        services_logs._tls = self.original_tls
        services_logs._fts_available_cache = self.original_fts_cache
        services_logs._players_cache.clear()
        services_logs._players_cache_ts.clear()
        shutil.rmtree(self.temp_dir)

    def _insert(self, offset_ms, server, player, action):
        timestamp = self.start + timedelta(milliseconds=offset_ms)
        self.conn.execute(
            "INSERT INTO log_events(ts, server, player, action, details, source_file) "
            "VALUES (?,?,?,?,?,?)",
            (
                services_logs._ts_to_ms(timestamp),
                server,
                player,
                action,
                f"{player or '-'} {action}",
                "server.log",
            ),
        )

    def _overview(self, *, bucket_ms=1000, player_limit=300, player_name=None):
        self.conn.commit()
        return services_logs.get_log_timeline_overview(
            server_name="ASPER1",
            time_from=self.start,
            time_to=self.start + timedelta(milliseconds=2999),
            bucket_ms=bucket_ms,
            player_limit=player_limit,
            player_name=player_name,
        )

    def test_groups_actions_into_time_buckets_and_counts_them(self):
        self._insert(0, "ASPER1", "Алиса", "wire")
        self._insert(999, "ASPER1", "Алиса", "wire")
        self._insert(1000, "ASPER1", "Борис", "attack")
        self._insert(1499, "ASPER1", "Борис", "attack")
        self._insert(1999, "ASPER1", "Алиса", "repair")
        self._insert(2000, "ASPER1", "Вера", "radio")

        overview = self._overview()

        self.assertEqual(
            overview.buckets,
            [
                {"offset_ms": 500, "action_type": "wire", "count": 2},
                {"offset_ms": 1500, "action_type": "attack", "count": 2},
                {"offset_ms": 1500, "action_type": "repair", "count": 1},
                {"offset_ms": 2500, "action_type": "radio", "count": 1},
            ],
        )

    def test_counts_players_orders_by_activity_and_honours_limit(self):
        for offset in (0, 100, 200):
            self._insert(offset, "ASPER1", "Алиса", "wire")
        for offset in (300, 400):
            self._insert(offset, "ASPER1", "Борис", "attack")
        self._insert(500, "ASPER1", "Вера", "radio")
        self._insert(600, "ASPER1", None, "structure")
        self._insert(700, "ASPER1", "", "other")

        overview = self._overview(player_limit=2)

        self.assertEqual(
            overview.players,
            [
                {"name": "Алиса", "event_count": 3},
                {"name": "Борис", "event_count": 2},
            ],
        )

    def test_isolates_requested_server_and_time_range(self):
        self._insert(500, "ASPER1", "Внутри", "wire")
        self._insert(500, "ASPER2", "Другой сервер", "attack")
        self._insert(-1, "ASPER1", "До раунда", "radio")
        self._insert(3000, "ASPER1", "После раунда", "repair")

        overview = self._overview()

        self.assertEqual(
            overview.buckets,
            [{"offset_ms": 500, "action_type": "wire", "count": 1}],
        )
        self.assertEqual(
            overview.players,
            [{"name": "Внутри", "event_count": 1}],
        )

    def test_bucket_size_is_clamped_to_250_milliseconds(self):
        self._insert(0, "ASPER1", "Алиса", "wire")
        self._insert(249, "ASPER1", "Алиса", "wire")
        self._insert(250, "ASPER1", "Алиса", "wire")

        overview = self._overview(bucket_ms=10)

        self.assertEqual(
            overview.buckets,
            [
                {"offset_ms": 125, "action_type": "wire", "count": 2},
                {"offset_ms": 375, "action_type": "wire", "count": 1},
            ],
        )

    def test_optional_player_filter_limits_buckets_and_player_counts(self):
        self._insert(100, "ASPER1", "Алиса", "wire")
        self._insert(200, "ASPER1", "Борис", "attack")
        self._insert(1100, "ASPER1", "Алиса", "repair")
        self._insert(1200, "ASPER1", "Борис", "radio")

        overview = self._overview(player_name="Алиса")

        self.assertEqual(
            overview.buckets,
            [
                {"offset_ms": 500, "action_type": "wire", "count": 1},
                {"offset_ms": 1500, "action_type": "repair", "count": 1},
            ],
        )
        self.assertEqual(
            overview.players,
            [{"name": "Алиса", "event_count": 2}],
        )


if __name__ == "__main__":
    unittest.main()
