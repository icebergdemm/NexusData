import unittest


class WorldStateDeltaMergeTests(unittest.TestCase):
    def test_character_delta_preserves_unchanged_rows_and_fields(self):
        source = open("routes/world_state.py", encoding="utf-8").read()
        self.assertIn("def _merge_id_rows", source)
        self.assertIn("current.setdefault(row[\"id\"], {}).update(row)", source)


if __name__ == "__main__":
    unittest.main()
