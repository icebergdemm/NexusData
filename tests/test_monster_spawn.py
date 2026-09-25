import unittest
from datetime import datetime
from types import SimpleNamespace

from routes.monster_spawn import _build_spawn_analysis, _serialize


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

        self.assertTrue(payload["config"]["global"]["intensity_gate_enabled"])
        self.assertTrue(payload["config"]["global"]["enabled"])
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

        self.assertTrue(payload["config"]["global"]["intensity_gate_enabled"])
        self.assertEqual(payload["config"]["global"]["intensity_threshold"], 0.2)
        self.assertEqual(payload["config"]["waves"], [])
        self.assertEqual(payload["config"]["future_option"], 7)
        self.assertEqual(payload["memory"], {})
        self.assertEqual(payload["plans"], [])
        self.assertEqual(payload["telemetry"], {})
        self.assertEqual(payload["dashboard"]["known_submarines"], 0)

    def test_dashboard_resolves_current_submarine_from_telemetry(self):
        payload = _serialize(self._row(
            memory={"submarines": {"hash:ship": {"name": "Vigil", "performance_ema": 1.4}}},
            telemetry={"round_active": True, "submarine": "hash:ship", "submarine_name": "Vigil", "intensity": 0.32},
        ))

        self.assertEqual(payload["dashboard"]["submarine_name"], "Vigil")
        self.assertEqual(payload["dashboard"]["submarine"]["performance_ema"], 1.4)
        self.assertEqual(payload["dashboard"]["intensity"], 0.32)

    def test_analysis_returns_wave_and_ship_logs_without_mutating_state(self):
        row = self._row(memory={
            "global_performance_ema": 0.68,
            "waves": {
                "vanilla:reef": {
                    "times_spawned": 4,
                    "times_unresolved": 1,
                    "total_repel_seconds": 400,
                    "total_crew_deaths": 2,
                    "total_sub_health_loss": 20,
                    "recent_performance": [0.6, 0.7, 0.65],
                },
            },
            "submarines": {
                "hash:ship": {
                    "name": "Vigil",
                    "type": "Player",
                    "rounds_played": 3,
                    "times_spawned": 4,
                    "total_repel_seconds": 400,
                    "total_crew_deaths": 2,
                    "total_sub_health_loss": 20,
                    "performance_ema": 0.7,
                    "recent_performance": [0.6, 0.8],
                },
            },
        })
        before = repr(row.memory)

        analysis = _build_spawn_analysis(row)

        self.assertTrue(analysis["read_only"])
        self.assertEqual(analysis["wave_logs"][0]["avg_repel_seconds"], 100.0)
        self.assertEqual(analysis["ship_logs"][0]["name"], "Vigil")
        self.assertTrue(any(item["id"] == "ease-slow-wave" for item in analysis["recommendations"]))
        self.assertTrue(any(item["id"] == "global-pressure" for item in analysis["recommendations"]))
        self.assertEqual(repr(row.memory), before)


if __name__ == "__main__":
    unittest.main()
