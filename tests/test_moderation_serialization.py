import unittest
from datetime import datetime
import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path


def _load_moderation_module():
    """Load only moderation.py without importing routes/__init__.py."""
    repo_root = Path(__file__).resolve().parents[1]
    routes_package = SimpleNamespace(__path__=[str(repo_root / "routes")])
    sys.modules.setdefault("routes", routes_package)
    module_name = "routes.moderation_for_serialization_test"
    spec = importlib.util.spec_from_file_location(
        module_name, repo_root / "routes" / "moderation.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_moderation = _load_moderation_module()
_serialize_offline_player = _moderation._serialize_offline_player
_serialize_online_player = _moderation._serialize_online_player
_serialize_search_player = getattr(_moderation, "_serialize_search_player", None)


class ModerationDiscordLinkSerializationTests(unittest.TestCase):
    def test_online_player_payload_exposes_only_link_status(self):
        payload = _serialize_online_player(
            {
                "player_steamid": "76561198000000000",
                "player_name": "Тестовый игрок",
                "forced_display_name": None,
                "character_name": None,
                "job_id": None,
                "ping": 42,
                "player_id": 7,
                "rank_id": 0,
                "server_name": "ASPER1",
                "join_time": datetime(2026, 9, 4, 12, 0),
                "last_seen": datetime(2026, 9, 4, 12, 1),
                "total_play_time": 3600,
                "is_new_player": True,
                "has_discord_link": True,
                "label_ids": [7, 8],
                "discord_id": "do-not-leak-this-id",
            }
        )

        self.assertTrue(payload["has_discord_link"])
        self.assertEqual(payload["label_ids"], [7, 8])
        self.assertNotIn("discord_id", payload)

    def test_offline_player_without_discord_id_is_unlinked(self):
        player = SimpleNamespace(
            player_id=8,
            player_steamid="76561198000000001",
            player_last_time_name="Игрок без Discord",
            forced_display_name=None,
            rank_id=0,
            first_login=datetime(2026, 1, 1),
            last_login_time=datetime(2026, 9, 4),
            last_session_logout=datetime(2026, 9, 4, 11, 59),
            play_time_hours=3.5,
            discord_id=None,
            labels=[7],
        )

        payload = _serialize_offline_player(player)

        self.assertFalse(payload["has_discord_link"])
        self.assertEqual(payload["label_ids"], [7])
        self.assertNotIn("discord_id", payload)

    def test_online_player_without_database_record_is_unlinked(self):
        payload = _serialize_online_player(
            {
                "player_steamid": "76561198000000002",
                "player_name": "Новый игрок",
                "forced_display_name": None,
                "character_name": None,
                "job_id": None,
                "ping": None,
                "player_id": None,
                "rank_id": None,
                "server_name": "ASPER2",
                "join_time": None,
                "last_seen": None,
                "total_play_time": 0,
                "is_new_player": True,
            }
        )

        self.assertFalse(payload["has_discord_link"])

    def test_search_player_payload_keeps_first_login_without_discord_id(self):
        player = SimpleNamespace(
            player_id=9,
            player_steamid="76561198000000003",
            player_last_time_name="Найденный игрок",
            forced_display_name=None,
            rank_id=0,
            first_login=datetime(2025, 12, 1),
            last_login_time=datetime(2026, 9, 3),
            play_time_hours=12.0,
            discord_id="123456789012345678",
        )

        self.assertIsNotNone(_serialize_search_player)
        payload = _serialize_search_player(player)

        self.assertEqual(payload["first_login"], "2025-12-01T00:00:00")
        self.assertTrue(payload["has_discord_link"])
        self.assertNotIn("discord_id", payload)


if __name__ == "__main__":
    unittest.main()
