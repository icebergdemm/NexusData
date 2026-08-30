import unittest
from datetime import datetime

from routes.replays import _timeline_unavailable_payload


class ReplayTimelineUnavailablePayloadTests(unittest.TestCase):
    def test_unavailable_payload_keeps_demo_contract_and_exposes_status(self):
        payload = _timeline_unavailable_payload(
            "Серверные логи временно недоступны.",
            round_id=42,
            server_name="ASPER1",
            started_at=datetime(2026, 8, 30, 12, 0),
            ended_at=datetime(2026, 8, 30, 13, 0),
            duration_ms=3_600_000,
            duration_source="request",
            anchor_source="recording",
            log_timezone="Europe/Kyiv",
            window_from_ms=30_000,
            window_to_ms=60_000,
        )

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["overview"], [])
        self.assertEqual(payload["total"], 0)
        self.assertFalse(payload["has_more"])
        self.assertEqual(payload["round_id"], 42)
        self.assertEqual(payload["server_name"], "ASPER1")
        self.assertEqual(payload["duration_ms"], 3_600_000)
        self.assertEqual(payload["window_from_ms"], 30_000)
        self.assertEqual(payload["window_to_ms"], 60_000)
        self.assertEqual(payload["players"], [])
        self.assertEqual(payload["action_types"], [])
        self.assertEqual(payload["log_timezone"], "Europe/Kyiv")
        self.assertTrue(payload["unavailable_reason"])


if __name__ == "__main__":
    unittest.main()
