import unittest
from unittest.mock import patch

import services_replays


class ReplayTimelineHelperTests(unittest.TestCase):
    @staticmethod
    def _item_index():
        index = {
            "водолазный скафандр": (
                "divingsuit",
                "Водолазный скафандр",
                True,
            ),
            "цепная пушка": (
                "chaingun",
                "Цепная пушка",
                True,
            ),
        }
        return index, sorted(index, key=len, reverse=True)

    def test_extracts_known_item_and_removes_runtime_id(self):
        with patch.object(
            services_replays,
            "_load_timeline_item_index",
            return_value=self._item_index(),
        ):
            item = services_replays.extract_replay_timeline_item(
                "dropped",
                "Ст. Матрос dropped Водолазный скафандр (ID: 638)",
            )

        self.assertEqual(
            item,
            {
                "identifier": "divingsuit",
                "name": "Водолазный скафандр",
                "has_icon": True,
            },
        )

    def test_extracts_attack_item_without_projectile_suffix(self):
        with patch.object(
            services_replays,
            "_load_timeline_item_index",
            return_value=self._item_index(),
        ):
            item = services_replays.extract_replay_timeline_item(
                "attack",
                "Матрос launched Цепная пушка (projectile: chaingunbolt)",
            )

        self.assertEqual(item["identifier"], "chaingun")
        self.assertEqual(item["name"], "Цепная пушка")

    def test_unknown_item_keeps_truthful_name_for_frontend_fallback(self):
        with patch.object(
            services_replays,
            "_load_timeline_item_index",
            return_value=({}, []),
        ):
            item = services_replays.extract_replay_timeline_item(
                "picked_up",
                "Матрос picked up Экспериментальный модуль",
            )

        self.assertEqual(
            item,
            {"identifier": None, "name": "Экспериментальный модуль"},
        )

    def test_non_item_action_does_not_guess_item_from_free_text(self):
        with patch.object(
            services_replays,
            "_load_timeline_item_index",
            return_value=self._item_index(),
        ):
            item = services_replays.extract_replay_timeline_item(
                "chat",
                "Я dropped Водолазный скафандр",
            )

        self.assertIsNone(item)

    def test_unknown_mod_item_is_not_replaced_by_partial_catalog_match(self):
        index = {
            "провод": ("wire", "Провод", True),
            "дверь": ("door", "Дверь", True),
        }
        with patch.object(
            services_replays,
            "_load_timeline_item_index",
            return_value=(index, sorted(index, key=len, reverse=True)),
        ):
            item = services_replays.extract_replay_timeline_item(
                "picked_up",
                "Матрос picked up Старый проводной модуль",
            )

        self.assertEqual(
            item,
            {"identifier": None, "name": "Старый проводной модуль"},
        )

    def test_lane_classification_and_unknown_fallback(self):
        expected = {
            "radio": "communication",
            "attack": "combat",
            "damage": "combat",
            "shot": "combat",
            "picked_up": "inventory",
            "looted_body": "inventory",
            "wire": "systems",
            "client_error": "system",
            "totally_unknown_action": "system",
        }

        for action_type, lane in expected.items():
            with self.subTest(action_type=action_type):
                self.assertEqual(
                    services_replays.classify_replay_timeline_lane(action_type),
                    lane,
                )

    def test_overview_keeps_distinct_actions_in_the_same_lane_and_bucket(self):
        rows, counts = services_replays.build_replay_timeline_overview_rows(
            [
                {"offset_ms": 500, "action_type": "attack", "count": 3, "player_count": 2},
                {"offset_ms": 500, "action_type": "death", "count": 1, "player_count": 1, "player_name": "Борис"},
                {"offset_ms": 500, "action_type": "radio", "count": 2},
            ],
            duration_ms=10_000,
        )

        self.assertEqual(
            [(row["action_type"], row["lane"], row["aggregate_count"]) for row in rows],
            [
                ("attack", "combat", 3),
                ("death", "combat", 1),
                ("radio", "communication", 2),
            ],
        )
        self.assertEqual(len({row["event_id"] for row in rows}), 3)
        self.assertEqual(counts, {"attack": 3, "death": 1, "radio": 2})
        self.assertEqual(sum(row["aggregate_count"] for row in rows), 6)
        self.assertEqual(rows[0]["player_count"], 2)
        self.assertIsNone(rows[0]["player_name"])
        self.assertEqual(rows[1]["player_name"], "Борис")

    def test_overview_clamps_offsets_but_keeps_adjacent_action_buckets(self):
        rows, _ = services_replays.build_replay_timeline_overview_rows(
            [
                {"offset_ms": 1_500, "action_type": "wire", "count": 1},
                {"offset_ms": 2_500, "action_type": "wire", "count": 2},
            ],
            duration_ms=2_000,
        )

        self.assertEqual([row["offset_ms"] for row in rows], [1_500, 2_000])
        self.assertEqual([row["aggregate_count"] for row in rows], [1, 2])


if __name__ == "__main__":
    unittest.main()
