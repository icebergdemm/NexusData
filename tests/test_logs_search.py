import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

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

    def test_cursor_pagination_has_no_gaps_or_duplicates(self):
        same_time = services_logs._ts_to_ms(datetime(2026, 8, 29, 13, 0))
        self.conn.executemany(
            "INSERT INTO log_events(ts, server, player, action, details, source_file) VALUES (?,?,?,?,?,?)",
            [
                (same_time, "ASPER1", "Первый", "wire", "same timestamp A", "server.log"),
                (same_time, "ASPER1", "Второй", "wire", "same timestamp B", "server.log"),
            ],
        )
        self.conn.commit()

        first = services_logs.search_logs(limit=3, count_total=False)
        second = services_logs.search_logs(
            limit=3,
            cursor=first.next_cursor,
            count_total=False,
        )

        first_ids = [event.row_id for event in first.events]
        second_ids = [event.row_id for event in second.events]
        self.assertEqual(len(first_ids), 3)
        self.assertEqual(len(second_ids), 3)
        self.assertTrue(first.has_more)
        self.assertIsNotNone(first.next_cursor)
        self.assertTrue(set(first_ids).isdisjoint(second_ids))
        self.assertEqual(first_ids + second_ids, sorted(first_ids + second_ids, reverse=True))
        self.assertIsNone(first.total)
        self.assertFalse(first.total_is_capped)

    def test_invalid_cursor_is_rejected(self):
        with self.assertRaises(services_logs.InvalidLogCursor):
            services_logs.search_logs(cursor="not-a-valid-cursor", count_total=False)

    def test_cursor_query_seeks_into_timestamp_index(self):
        plan = self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM log_events "
            "WHERE (ts, id) < (?, ?) ORDER BY ts DESC, id DESC LIMIT 101",
            (services_logs._ts_to_ms(datetime(2026, 8, 29, 13, 0)), 100),
        ).fetchall()

        self.assertTrue(any("SEARCH" in row[-1] and "ix_log_ts" in row[-1] for row in plan), plan)

    def test_cursor_mode_does_not_count_the_full_result_set(self):
        with patch.object(
            services_logs,
            "_bounded_count",
            side_effect=AssertionError("cursor mode must not count rows"),
        ):
            result = services_logs.search_logs(limit=2, count_total=False)

        self.assertEqual(len(result.events), 2)
        self.assertIsNone(result.total)

    def test_network_autokick_parser_keeps_only_the_player_name(self):
        line = (
            "Ст. Матрос Энджел Шарнхорст was kicked because they were expecting "
            "a very old network event (1 created 60.03 s ago). Events queued: 8"
        )
        events, _player_map = services_logs.parse_log_content(
            f"[08/29/2026 00:53:00]\n{line}",
            server_name="ASPER1",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].player_name, "Ст. Матрос Энджел Шарнхорст")
        self.assertEqual(events[0].action_type, "kick")

    def test_client_error_parser_keeps_only_the_player_name(self):
        player = "Старшина 2-ст Александр Гром"
        content = (
            "[8/30/2026 3:18:08 AM] "
            f"{player} has reported an error: System.InvalidOperationException"
        )

        events, _player_map = services_logs.parse_log_content(
            content,
            server_name="ASPER1",
            source_file="client-error.log",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].player_name, player)
        self.assertEqual(events[0].action_type, "client_error")
        self.assertIn("System.InvalidOperationException", events[0].details)

    def test_pvp_attack_parser_preserves_dotted_ranks(self):
        victim = "Ст. Матрос Жертва"
        attacker = "Ст. Матрос Нападающий"
        line = (
            f"{victim} attacked by {attacker}. "
            "Gunshot wound: 12.0 Bleeding: 4.0"
        )

        events, _player_map = services_logs.parse_log_content(
            f"[08/31/2026 12:00:00]\n{line}",
            server_name="ASPER1",
            source_file="pvp.log",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action_type, "attack")
        self.assertEqual(events[0].player_name, attacker)
        self.assertEqual(events[0].details, line)

    def test_pvp_attack_without_affliction_still_preserves_dotted_rank(self):
        attacker = "Ст. Матрос Нападающий"
        events, _player_map = services_logs.parse_log_content(
            "[08/31/2026 12:00:00]\n"
            f"Матрос Жертва attacked by {attacker}.",
            server_name="ASPER1",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action_type, "attack")
        self.assertEqual(events[0].player_name, attacker)

    def test_item_use_parser_preserves_item_and_target_details(self):
        line = "Ст. Матрос Испытатель used Медицинский сканер on Питомец Борис"
        events, _player_map = services_logs.parse_log_content(
            f"[08/31/2026 12:00:00]\n{line}",
            server_name="ASPER1",
            source_file="items.log",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action_type, "used")
        self.assertEqual(events[0].player_name, "Ст. Матрос Испытатель")
        self.assertEqual(events[0].details, line)

    def test_health_interface_item_use_without_target_remains_searchable(self):
        line = "Ст. Медик Испытатель used item Морфин"
        events, _player_map = services_logs.parse_log_content(
            f"[08/31/2026 12:00:00]\n{line}",
            server_name="ASPER1",
            source_file="items.log",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action_type, "used")
        self.assertEqual(events[0].player_name, "Ст. Медик Испытатель")
        self.assertEqual(events[0].details, line)

    def test_legacy_pvp_attack_actor_is_restored_from_details(self):
        attacker = "Ст. Матрос Нападающий"
        details = (
            f"Матрос Жертва attacked by {attacker}. "
            "Gunshot wound: 12.0 Bleeding: 4.0"
        )
        cursor = self.conn.execute(
            "INSERT INTO log_events(ts, server, player, action, details, source_file) "
            "VALUES (?,?,?,?,?,?)",
            (1, "ASPER1", "Ст", "attack", details, "legacy-pvp.log"),
        )
        row_id = cursor.lastrowid
        self.conn.execute(
            "DELETE FROM fts_meta WHERE key=?",
            (services_logs._ATTACK_PLAYER_REPAIR_KEY,),
        )
        self.conn.commit()

        with patch.object(services_logs, "_fts_available", return_value=False):
            repaired = services_logs._repair_legacy_attack_players(self.conn)

        row = self.conn.execute(
            "SELECT player, action, details FROM log_events WHERE id=?",
            (row_id,),
        ).fetchone()
        self.assertEqual(repaired, 1)
        self.assertEqual(row, (attacker, "attack", details))

    def test_player_autocomplete_normalises_legacy_client_error_name(self):
        player = "Старшина 2-ст Александр Гром"
        self.conn.executemany(
            "INSERT INTO log_events(ts, server, player, action, details, source_file) "
            "VALUES (?,?,?,?,?,?)",
            [
                (1, "ASPER1", player, "radio", "обычное событие", "legacy-error.log"),
                (
                    2,
                    "ASPER1",
                    f"{player} has reported an error",
                    "chat",
                    "legacy client error",
                    "legacy-error.log",
                ),
            ],
        )
        self.conn.commit()
        services_logs._players_cache.clear()
        services_logs._players_cache_ts.clear()

        players = services_logs.get_unique_players("ASPER1")

        self.assertEqual(players.count(player), 1)
        self.assertFalse(any("has reported an error" in name for name in players))

    def test_legacy_network_autokicks_are_repaired_and_deduplicated(self):
        separator = services_logs._AUTO_KICK_PLAYER_SEPARATOR
        player = "Ст. Матрос Энджел Шарнхорст"
        plan = self.conn.execute(
            "EXPLAIN QUERY PLAN UPDATE log_events INDEXED BY ix_log_action "
            "SET player=trim(substr(player,1,instr(player,?)-1)), action='kick' "
            "WHERE action='chat' AND instr(player,?)>1",
            (separator, separator),
        ).fetchall()
        self.assertTrue(any("ix_log_action" in row[-1] for row in plan), plan)
        legacy_players = [
            f"{player}{separator}old network event (1) Events queued",
            f"{player}{separator}old network event (2) Events queued",
        ]
        self.conn.execute(
            "DELETE FROM fts_meta WHERE key=?",
            (services_logs._AUTO_KICK_PLAYER_REPAIR_KEY,),
        )
        self.conn.executemany(
            "INSERT INTO log_events(ts, server, player, action, details, source_file) "
            "VALUES (?,?,?,?,?,?)",
            [
                (
                    services_logs._ts_to_ms(datetime(2026, 8, 29, 14, 0)),
                    "ASPER1",
                    legacy_player,
                    "chat",
                    f"{legacy_player}: 8",
                    "legacy.log",
                )
                for legacy_player in legacy_players
            ],
        )
        self.conn.commit()

        self.conn.execute(
            "INSERT OR REPLACE INTO fts_meta(key, value) VALUES ('backfill_done', '0')"
        )
        self.conn.commit()
        self.assertEqual(services_logs._repair_legacy_auto_kick_players(self.conn), 0)
        dirty_count = self.conn.execute(
            "SELECT COUNT(*) FROM log_events WHERE source_file='legacy.log' AND action='chat'"
        ).fetchone()[0]
        self.assertEqual(dirty_count, 2)

        self.conn.execute(
            "INSERT OR REPLACE INTO fts_meta(key, value) VALUES ('backfill_done', '1')"
        )
        self.conn.commit()
        repaired = services_logs._repair_legacy_auto_kick_players(self.conn)
        services_logs._players_cache.clear()
        services_logs._players_cache_ts.clear()

        rows = self.conn.execute(
            "SELECT DISTINCT player, action FROM log_events WHERE source_file='legacy.log'"
        ).fetchall()
        self.assertEqual(repaired, 2)
        self.assertEqual(rows, [(player, "kick")])
        self.assertEqual(services_logs.get_unique_players("ASPER1").count(player), 1)
        self.assertEqual(services_logs._repair_legacy_auto_kick_players(self.conn), 0)


if __name__ == "__main__":
    unittest.main()
