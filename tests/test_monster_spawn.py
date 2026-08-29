import unittest
from datetime import datetime
from types import SimpleNamespace

from routes.monster_spawn import _serialize


class MonsterSpawnSerializationTests(unittest.TestCase):
    @staticmethod
    def _row(**overrides):
        values = {
            "server": "ASPER1",
            "config": {},
            "memory": {},
            "plans": [],
            "telemetry": {},
            "desired_revision": 0,
            "applied_revision": 0,
            "updated_at": datetime(2026, 8, 29, 12, 0),
            "last_seen_at": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_legacy_config_without_waves_gets_safe_collections(self):
        payload = _serialize(self._row(config={"global": {"enabled": True}}))

        self.assertEqual(payload["config"]["global"], {"enabled": True})
        self.assertEqual(payload["config"]["waves"], [])

    def test_invalid_collection_shapes_do_not_reach_frontend(self):
        payload = _serialize(
            self._row(
                config={"global": None, "waves": None, "future_option": 7},
                memory=[],
                plans={},
                telemetry=[],
            )
        )

        self.assertEqual(payload["config"]["global"], {})
        self.assertEqual(payload["config"]["waves"], [])
        self.assertEqual(payload["config"]["future_option"], 7)
        self.assertEqual(payload["memory"], {})
        self.assertEqual(payload["plans"], [])
        self.assertEqual(payload["telemetry"], {})


if __name__ == "__main__":
    unittest.main()
