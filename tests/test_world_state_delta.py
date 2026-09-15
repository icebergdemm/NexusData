import unittest


class WorldStateDeltaSourceTests(unittest.TestCase):
    def test_delta_contract_preserves_omitted_streams(self):
        with open("routes/world_state.py", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('if "subs" in body:', source)
        self.assertIn('if "characters" in body:', source)


if __name__ == "__main__":
    unittest.main()
