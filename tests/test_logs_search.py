import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

import services_logs


class LogSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus-log-search-")
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
        base = datetime(2026, 8, 29, 12, 0)
        rows = [
            (base, "ASPER1", "Игрок Один", "wire", "Игрок Один connected a wire to relay"),
            (base + timedelta(seconds=1), "ASPER1", "Инженер", "wire", "Инженер disconnected a wire near Игрок Один"),
            (base + timedelta(seconds=2), "ASPER1", "Игрок Один", "opened", "Игрок Один opened шкаф"),
            (base + timedelta(seconds=3), "ASPER1", "Игрок Два", "wire", "Игрок Два connected a wire"),
        ]
        self.conn.executemany(
            "INSERT INTO log_events(ts, server, player, action, details, source_file) VALUES (?,?,?,?,?,?)",
            [
                (services_logs._ts_to_ms(ts), server, player, action, details, "server.log")
                for ts, server, player, action, details in rows
            ],
        )
        self.conn.commit()

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

    def test_actor_scope_returns_only_events_performed_by_player(self):
        result = services_logs.search_logs(
            server_name="ASPER1",
            names_list=["Игрок Один"],
            player_scope="actor",
            action_type="wire",
            limit=100,
        )

        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].player_name, "Игрок Один")
        self.assertEqual(result.events[0].action_type, "wire")

    def test_related_scope_also_returns_mentions_in_details(self):
        result = services_logs.search_logs(
            server_name="ASPER1",
            names_list=["Игрок Один"],
            player_scope="related",
            action_type="wire",
            limit=100,
        )

        self.assertEqual(
            {event.player_name for event in result.events},
            {"Игрок Один", "Инженер"},
        )

    def test_actor_and_action_query_is_forced_through_player_index(self):
        from_sql, where, params = services_logs._build_where(
            "ASPER1",
            None,
            "wire",
            None,
            None,
            None,
            None,
            names_list=["Игрок Один"],
            use_fts=False,
            player_scope="actor",
        )

        self.assertEqual(from_sql, "log_events INDEXED BY ix_log_player")
        self.assertNotIn("details LIKE", where)
        plan = self.conn.execute(
            f"EXPLAIN QUERY PLAN SELECT log_events.id FROM {from_sql}{where} "
            "ORDER BY log_events.ts DESC, log_events.id DESC LIMIT 101",
            params,
        ).fetchall()
        self.assertTrue(any("ix_log_player" in row[-1] for row in plan), plan)

    def test_multiple_actor_names_use_exact_in_filter(self):
        result = services_logs.search_logs(
            names_list=["Игрок Один", "Игрок Два"],
            player_scope="actor",
            action_type="wire",
            limit=100,
        )

        self.assertEqual(
            {event.player_name for event in result.events},
            {"Игрок Один", "Игрок Два"},
        )


if __name__ == "__main__":
    unittest.main()
