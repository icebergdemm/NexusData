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


if __name__ == "__main__":
    unittest.main()
